import os
os.environ['DATABASE_URL']='sqlite://'
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session
from app import main as m,ranked

@pytest.fixture
def clock(monkeypatch):
    value=[1000000]
    monkeypatch.setattr(ranked,'milliseconds',lambda:value[0])
    return value

@pytest.fixture
def client(clock):
    m.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    m.Base.metadata.create_all(m.engine)
    with TestClient(m.app) as c:
        c.headers['X-Requested-With']='Belancer'
        yield c

def account(c,name='player1'):
    assert c.post('/api/auth/register',json={'username':name,'password':'longpassword'}).status_code==200
def start(c,game):
    r=c.post('/api/sessions',json={'game':game});assert r.status_code==200,r.text
    return r.json()
def act(c,s,answer):
    return c.post(f"/api/sessions/{s['id']}/actions",json={'sequence':s['sequence'],'answer':answer})
def finish(c,s): return c.post(f"/api/sessions/{s['id']}/finish",json={})
def private(s):
    with Session(m.engine) as db:return json.loads(db.get(m.GameSession,s['id']).payload)

def test_accounts_and_csrf(client):
    assert client.get('/api/auth/me').status_code==401
    account(client)
    assert client.get('/api/auth/me').json()['username']=='player1'
    assert client.post('/api/auth/logout',json={},headers={'origin':'https://evil.example'}).status_code==403
    assert client.get('/api/admin/overview').status_code==403
    client.post('/api/auth/logout',json={})
    assert client.get('/api/auth/me').status_code==401
    assert client.post('/api/auth/login',json={'username':'player1','password':'wrongpassword'}).status_code==401

@pytest.mark.parametrize('origin',['http://localhost:5173','http://127.0.0.1:5173'])
def test_signup_from_both_local_addresses(client,origin,monkeypatch):
    monkeypatch.setenv('PUBLIC_ORIGIN','http://localhost:5173')
    r=client.post('/api/auth/register',json={'username':'localplayer','password':'longpassword'},headers={'origin':origin})
    assert r.status_code==200
    assert client.get('/api/auth/me').json()['username']=='localplayer'

def test_production_origin_does_not_allow_localhost(client,monkeypatch):
    monkeypatch.setenv('PUBLIC_ORIGIN','https://game.example.com')
    for origin in ['http://localhost:5173','http://127.0.0.1:5173','https://evil.example']:
        r=client.post('/api/auth/register',json={'username':'localplayer','password':'longpassword'},headers={'origin':origin})
        assert r.status_code==403 and 'Origin rejected' in r.json()['detail']

def test_csrf_header_error_is_json(client):
    client.headers.pop('X-Requested-With')
    r=client.post('/api/auth/logout',json={})
    assert r.status_code==403 and r.json()['detail']=='CSRF header required'

def test_idempotency_order_and_limit(client):
    account(client);s=start(client,'math-sprint');a=sum(s['prompt']['question'])
    first=act(client,s,a);assert first.status_code==200
    assert act(client,s,a).json()==first.json()
    assert act(client,s,a+1).status_code==409
    assert act(client,s,True).status_code==409
    assert client.post(f"/api/sessions/{s['id']}/actions",json={'sequence':99,'answer':0}).status_code==409
    assert private(s)['correct']==1
    for _ in range(2):start(client,'math-sprint')
    assert client.post('/api/sessions',json={'game':'math-sprint'}).status_code==409
    assert next(x for x in client.get('/api/challenges').json() if x['slug']=='math-sprint')['attemptsRemaining']==0

@pytest.mark.parametrize('game',['memory-grid','quick-match','focus-finder','math-sprint','pair-finder'])
def test_shared_content_without_future_leak(client,game):
    account(client,'alice');a=start(client,game)
    account(client,'bob');b=start(client,game)
    assert private(a)['content']==private(b)['content']
    assert 'payload' not in a and 'content' not in a
    assert 'rounds' not in a['prompt'] and 'cards' not in a['prompt']
    if game=='pair-finder':assert a['prompt']['open']==[]
    assert client.get(f"/api/sessions/{a['id']}").status_code==404
    assert act(client,a,0).status_code==404

@pytest.mark.parametrize('game',['memory-grid','quick-match','focus-finder','math-sprint','pair-finder'])
def test_perfect_game(client,clock,game):
    account(client);s=start(client,game)
    if game=='pair-finder':
        cards=private(s)['content']['cards']
        for symbol in range(8):
            for index,v in enumerate(cards):
                if v==symbol:
                    r=act(client,s,index);assert r.status_code==200,r.text;s=r.json()
            clock[0]+=350
        assert s['result']['score']==800
    else:
        total=10 if game in ('memory-grid','focus-finder') else 120
        for _ in range(total):
            q=s['prompt']['question']
            if game=='memory-grid':clock[0]+=2100;answer=q
            elif game=='quick-match':answer=int(q[0]==q[1])
            elif game=='math-sprint':answer=sum(q)
            else:answer=q
            r=act(client,s,answer);assert r.status_code==200,r.text;s=r.json()
        assert s['result']['score']==(300 if game=='memory-grid' else total*100)
    assert s['result']['completed'] and s['result']['errors']==0
    assert finish(client,s).json()['result']==s['result']
    assert len(client.get('/api/history').json())==1
    assert client.get(f'/api/leaderboards/{game}').json()[0]['rank']==1

def test_memory_gate_and_response_time(client,clock):
    account(client);s=start(client,'memory-grid');answer=s['prompt']['question']
    assert act(client,s,answer).status_code==409
    clock[0]+=2250
    r=act(client,s,answer);assert r.status_code==200
    assert private(s)['responseTime']==250
    s=r.json();assert act(client,s,s['prompt']['question']).status_code==409
    assert act(client,s,[0,0,0]).status_code==409
    clock[0]+=2000;assert act(client,s,[0,0,0]).status_code==422

def test_memory_replay_does_not_reveal_hidden_pattern_or_rewind_timer(client,clock):
    account(client);s=start(client,'memory-grid');answer=s['prompt']['question']
    clock[0]+=2100;r=act(client,s,answer);assert r.status_code==200
    clock[0]+=2100
    replay=act(client,s,answer);assert replay.status_code==200
    assert replay.json()['prompt']['question']==[]
    assert replay.json()['serverNow']==clock[0]
    assert client.get(f"/api/sessions/{s['id']}").json()['prompt']['question']==[]

def test_pairs_reveal_and_delay(client,clock):
    account(client);s=start(client,'pair-finder');cards=private(s)['content']['cards']
    a=0;b=next(i for i,v in enumerate(cards) if v!=cards[a])
    first=act(client,s,a);s=first.json()
    assert s['feedback']['revealed']['symbol']==cards[a]
    assert act(client,s,a).status_code==422
    second=act(client,s,b);s=second.json();assert not s['feedback']['matched']
    assert act(client,s,a).status_code==409
    clock[0]+=850;assert act(client,s,a).status_code==200

def test_deadline_partial_result_and_legacy_rejection(client,clock):
    account(client);s=start(client,'math-sprint')
    assert finish(client,s).status_code==409
    assert client.post(f"/api/sessions/{s['id']}/submit",json={'answers':[1]*10,'duration':1}).status_code==409
    s=act(client,s,sum(s['prompt']['question'])).json()
    clock[0]=s['deadline']
    assert act(client,s,0).status_code==410
    r=finish(client,s);assert r.status_code==200,r.text
    assert r.json()['result']['score']==100 and not r.json()['result']['completed']
    assert finish(client,s).json()['result']==r.json()['result']

@pytest.mark.parametrize('bad',[True,'1',1.5,None,[],{}])
def test_malformed_answers(client,bad):
    account(client);s=start(client,'math-sprint')
    assert act(client,s,bad).status_code==422
    assert private(s)['events']==[]

def test_two_players_best_attempt_and_version_separation(client,clock):
    account(client,'alice');s=start(client,'math-sprint')
    s=act(client,s,sum(s['prompt']['question'])).json();clock[0]=s['deadline'];finish(client,s)
    s=start(client,'math-sprint');clock[0]=s['deadline'];finish(client,s)
    with Session(m.engine) as db:
        user=db.scalar(select(m.User).where(m.User.username=='alice'))
        db.add(m.GameSession(id='old',user_id=user.id,game='math-sprint',challenge=m.challenge('math-sprint').replace(':v2',':v1'),attempt=1,issued=m.now(),payload='{}',result=json.dumps({'score':999999})));db.commit()
    account(client,'bob');s=start(client,'math-sprint');clock[0]=s['deadline'];finish(client,s)
    rows=client.get('/api/leaderboards/math-sprint').json()
    assert [(r['username'],r['rank'],r['score']) for r in rows]==[('alice',1,100),('bob',2,0)]

def test_memory_ranks_complete_patterns_before_raw_cells(client,clock):
    account(client,'alice');s=start(client,'memory-grid')
    for _ in range(10):
        q=s['prompt']['question'];outside=next(i for i in range(16) if i not in q)
        clock[0]+=2100;s=act(client,s,[q[0],q[1],outside]).json()
    assert s['result']['correct']==20 and s['result']['correctRounds']==0
    account(client,'bob');s=start(client,'memory-grid')
    for i in range(10):
        q=s['prompt']['question'];answer=q if i<5 else [x for x in range(16) if x not in q][:3]
        clock[0]+=2100;s=act(client,s,answer).json()
    assert s['result']['correct']==15 and s['result']['correctRounds']==5
    assert [r['username'] for r in client.get('/api/leaderboards/memory-grid').json()]==['bob','alice']
