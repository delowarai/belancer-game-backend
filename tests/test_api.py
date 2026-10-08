import os
os.environ['DATABASE_URL']='sqlite://'
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session
from app import main as m

@pytest.fixture
def client():
    m.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    m.Base.metadata.create_all(m.engine)
    with TestClient(m.app) as c:
        c.headers['X-Requested-With']='Belancer'
        yield c

def account(client,name='player1'):
    r=client.post('/api/auth/register',json={'username':name,'password':'longpassword'})
    assert r.status_code==200

def test_accounts_and_csrf(client):
    assert client.get('/api/auth/me').status_code==401
    account(client)
    assert client.get('/api/auth/me').json()['username']=='player1'
    assert client.post('/api/auth/logout',json={},headers={'origin':'https://evil.example'}).status_code==403
    assert client.get('/api/admin/overview').status_code==403
    client.post('/api/auth/logout',json={})
    assert client.get('/api/auth/me').status_code==401
    assert client.post('/api/auth/login',json={'username':'player1','password':'wrongpassword'}).status_code==401

def test_score_idempotency_and_limit(client):
    account(client)
    session=client.post('/api/sessions',json={'game':'math-sprint'}).json()
    body={'answers':[sum(r) for r in session['payload']['rounds']],'duration':1}
    first=client.post(f"/api/sessions/{session['id']}/submit",json=body)
    assert first.status_code==200 and first.json()['score']==1000
    assert client.post(f"/api/sessions/{session['id']}/submit",json=body).json()==first.json()
    assert len(client.get('/api/history').json())==1
    assert client.get('/api/leaderboards/math-sprint').json()[0]['rank']==1
    for _ in range(2): assert client.post('/api/sessions',json={'game':'math-sprint'}).status_code==200
    assert client.post('/api/sessions',json={'game':'math-sprint'}).status_code==409

def test_expiry_and_malformed(client):
    account(client)
    item=client.post('/api/sessions',json={'game':'math-sprint'}).json()
    url=f"/api/sessions/{item['id']}/submit"
    assert client.post(url,json={'answers':['bad']*10,'duration':1}).status_code==422
    with Session(m.engine) as db:
        stored=db.get(m.GameSession,item['id']);stored.issued-=601;db.commit()
    assert client.post(url,json={'answers':[1]*10,'duration':1}).status_code==410

@pytest.mark.parametrize('game',['memory-grid','quick-match','focus-finder','math-sprint','pair-finder'])
def test_known_answers(game):
    p=m.generate(game)
    if game=='memory-grid': answers=p['rounds']
    elif game=='quick-match': answers=[int(a==b) for a,b in p['rounds']]
    elif game=='math-sprint': answers=[sum(x) for x in p['rounds']]
    elif game=='focus-finder': answers=p['rounds']
    else: answers=[i for symbol in range(8) for i,v in enumerate(p['cards']) if v==symbol]
    score,correct,errors,done=m.evaluate(game,p,answers)
    assert score>0 and errors==0 and done

def test_cross_account_session(client):
    account(client)
    item=client.post('/api/sessions',json={'game':'math-sprint'}).json()
    account(client,'player2')
    assert client.post(f"/api/sessions/{item['id']}/submit",json={'answers':[1]*10,'duration':1}).status_code==404

def test_two_players_rank_and_best_attempt(client):
    account(client,'alice')
    first=client.post('/api/sessions',json={'game':'math-sprint'}).json()
    assert client.post(f"/api/sessions/{first['id']}/submit",json={'answers':[sum(x) for x in first['payload']['rounds']],'duration':1}).status_code==200
    second=client.post('/api/sessions',json={'game':'math-sprint'}).json()
    client.post(f"/api/sessions/{second['id']}/submit",json={'answers':[0]*10,'duration':1})
    account(client,'bob')
    third=client.post('/api/sessions',json={'game':'math-sprint'}).json()
    client.post(f"/api/sessions/{third['id']}/submit",json={'answers':[0]*10,'duration':1})
    rows=client.get('/api/leaderboards/math-sprint').json()
    assert [(r['username'],r['rank'],r['score']) for r in rows]==[('alice',1,1000),('bob',2,0)]
