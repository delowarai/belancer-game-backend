import json
from datetime import datetime,timezone,timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import main as m
from test_api import client,clock,account,start,act,finish

def admin(c):
    account(c,'operator')
    with Session(m.engine) as db:
        user=db.scalar(select(m.User).where(m.User.username=='operator'));user.role='admin';db.commit()
def user_id(name):
    with Session(m.engine) as db:return db.scalar(select(m.User.id).where(m.User.username==name))
def game_body(c,slug='math-sprint'):
    value=next(x for x in c.get('/api/admin/games').json() if x['slug']==slug)
    return {k:value[k] for k in ['name','description','instructions','available','configuration']}|{'reason':'Test configuration edit'}

@pytest.mark.parametrize('path',['overview','users','games','challenges','results','audit','content'])
def test_admin_reads_require_role(client,path):
    assert client.get('/api/admin/'+path).status_code==401
    account(client)
    assert client.get('/api/admin/'+path).status_code==403

def test_player_cannot_change_roles(client):
    account(client)
    assert client.post('/api/admin/users/'+user_id('player1'),json={'active':True,'role':'admin','reason':'Unauthorized upgrade'}).status_code==403
    with Session(m.engine) as db:assert db.get(m.User,user_id('player1')).role=='player'

def test_suspend_reactivate_and_session_revocation(client,clock):
    account(client,'player');sid=start(client,'math-sprint')
    # Preserve the player's cookie for checking an already authenticated session.
    token=client.cookies.get('session');admin(client)
    uid=user_id('player')
    body={'active':False,'role':'player','reason':'Suspicious activity review'}
    assert client.post('/api/admin/users/'+uid,json=body).status_code==200
    with Session(m.engine) as db:
        assert not db.get(m.User,uid).active
        assert db.scalar(select(m.Login).where(m.Login.user_id==uid)) is None
    headers={'cookie':'session='+token}
    assert client.get('/api/auth/me',headers=headers).status_code==401
    body['active']=True;assert client.post('/api/admin/users/'+uid,json=body).status_code==200
    assert client.post('/api/auth/login',json={'username':'player','password':'longpassword'}).status_code==200

def test_self_lockout_and_secret_redaction(client):
    admin(client);uid=user_id('operator')
    assert client.post('/api/admin/users/'+uid,json={'active':False,'role':'admin','reason':'Attempt lockout'}).status_code==409
    assert client.post('/api/admin/users/'+uid,json={'active':True,'role':'player','reason':'Attempt demotion'}).status_code==409
    rows=client.get('/api/admin/users').json()['items']
    assert set(rows[0])=={'id','username','active','role'}
    assert client.post('/api/admin/users/'+uid,json={'active':True,'role':'admin','reason':' '}).status_code==422

def test_promotion_and_user_search_pagination(client):
    account(client,'target');admin(client)
    uid=user_id('target')
    assert client.post('/api/admin/users/'+uid,json={'active':True,'role':'admin','reason':'Trusted administrator'}).status_code==200
    assert client.get('/api/admin/users?q=target').json()['total']==1
    assert client.get('/api/admin/users?limit=1&offset=1').json()['total']==2
    assert len(client.get('/api/admin/users?limit=1&offset=1').json()['items'])==1

def test_game_disable_and_published_rules_immutable(client,clock):
    account(client,'player');s=start(client,'math-sprint');old_deadline=s['deadline'];old_content=None
    with Session(m.engine) as db:old_content=json.loads(db.get(m.Challenge,s['challenge']).payload)
    admin(client);body=game_body(client);body['configuration']={'durationSeconds':10,'rounds':2};body['instructions']='New instructions for future challenges'
    assert client.post('/api/admin/games/math-sprint',json=body).status_code==200
    new=start(client,'math-sprint')
    assert new['deadline']-new['serverNow']==60000 and new['totalRounds']==120
    with Session(m.engine) as db:assert json.loads(db.get(m.Challenge,s['challenge']).payload)==old_content
    body['available']=False;assert client.post('/api/admin/games/math-sprint',json=body).status_code==200
    assert client.post('/api/sessions',json={'game':'math-sprint'}).status_code==409
    assert next(g for g in client.get('/api/games').json() if g['slug']=='math-sprint')['available'] is False
    # An admin's own already-started session can still finish/actions after disable.
    assert act(client,new,sum(new['prompt']['question'])).status_code==200

@pytest.mark.parametrize('configuration',[{'durationSeconds':1,'rounds':10},{'durationSeconds':60,'rounds':999},{'durationSeconds':60,'rounds':True},{'durationSeconds':60,'rounds':10,'unsupported':1}])
def test_invalid_configuration(client,configuration):
    admin(client);body=game_body(client);body['configuration']=configuration
    assert client.post('/api/admin/games/math-sprint',json=body).status_code==422
    assert client.get('/api/admin/audit').json()['total']==0

def test_draft_publish_close_and_frozen_custom_configuration(client,clock):
    admin(client);body=game_body(client,'memory-grid');body['configuration']={'durationSeconds':30,'rounds':2,'cells':4};body['instructions']='Remember four cells'
    assert client.post('/api/admin/games/memory-grid',json=body).status_code==200
    day=datetime.now(timezone.utc).date().isoformat()
    r=client.post('/api/admin/challenges',json={'game':'memory-grid','day':day,'reason':'Prepare competition'});assert r.status_code==200,r.text
    cid=r.json()['id'];assert r.json()['status']=='draft'
    assert client.post('/api/sessions',json={'game':'memory-grid'}).status_code==409
    assert client.post('/api/admin/challenges/'+cid,json={'status':'published','reason':'Approved competition'}).status_code==200
    s=start(client,'memory-grid');assert s['totalRounds']==2 and s['deadline']-s['serverNow']==30000
    assert len(s['prompt']['question'])==4
    first=s['prompt']['question'];clock[0]+=2100;s=act(client,s,first).json()
    assert client.post('/api/admin/challenges/'+cid,json={'status':'draft','reason':'Attempt rule reset'}).status_code==409
    assert client.post('/api/admin/challenges/'+cid,json={'status':'closed','reason':'Close competition'}).status_code==200
    assert client.post('/api/sessions',json={'game':'memory-grid'}).status_code==409
    clock[0]+=2100;completed=act(client,s,s['prompt']['question']);assert completed.status_code==200 and completed.json()['result']['correct']==8
    assert client.post('/api/admin/challenges/'+cid,json={'status':'published','reason':'Attempt reopen'}).status_code==409
    assert next(x for x in client.get('/api/challenges').json() if x['slug']=='memory-grid')['instructions']=='Remember four cells'

def test_future_challenge_duplicate_and_date_rules(client):
    admin(client);today=datetime.now(timezone.utc).date();body={'game':'focus-finder','day':(today+timedelta(days=1)).isoformat(),'reason':'Tomorrow schedule'}
    assert client.post('/api/admin/challenges',json=body).status_code==200
    assert client.post('/api/admin/challenges',json=body).status_code==409
    body['day']=(today-timedelta(days=1)).isoformat();assert client.post('/api/admin/challenges',json=body).status_code==422

def test_result_review_updates_leaderboard_and_audit(client,clock):
    account(client,'player');a=start(client,'math-sprint');a=act(client,a,sum(a['prompt']['question'])).json();clock[0]=a['deadline'];finish(client,a)
    b=start(client,'math-sprint');clock[0]=b['deadline'];finish(client,b)
    admin(client);sid=a['id'];url='/api/admin/results/'+sid
    assert client.get(url).json()['events'][0]['feedback']['correct']==1
    assert client.post(url,json={'action':'flag','reason':'Timing needs review'}).status_code==200
    assert client.get('/api/admin/results?status=flagged').json()['total']==1
    assert client.post(url,json={'action':'invalidate','reason':'Invalid timing evidence'}).status_code==200
    assert client.get('/api/leaderboards/math-sprint').json()[0]['score']==0
    assert client.post(url,json={'action':'restore','reason':'Appeal evidence accepted'}).status_code==200
    assert client.get('/api/leaderboards/math-sprint').json()[0]['score']==100
    assert client.post(url,json={'action':'clear_flag','reason':'Review complete'}).status_code==200
    logs=client.get('/api/admin/audit').json();assert logs['total']==4
    assert all(x['actor']=='operator' and x['details']['reason'] for x in logs['items'])
    assert client.post(url,json={'action':'invalidate','reason':'Invalid score','score':9999}).status_code==422

def test_suspended_player_removed_from_ranking(client,clock):
    account(client,'player');s=start(client,'math-sprint');clock[0]=s['deadline'];finish(client,s)
    admin(client);uid=user_id('player')
    assert client.get('/api/leaderboards/math-sprint').json()
    client.post('/api/admin/users/'+uid,json={'active':False,'role':'player','reason':'Suspend ranking eligibility'})
    assert client.get('/api/leaderboards/math-sprint').json()==[]
    assert client.post('/api/auth/login',json={'username':'player','password':'longpassword'}).status_code==403

def test_content_changes_are_public_and_audited(client):
    admin(client);body=client.get('/api/admin/content').json();body.update(headline='Play Belancer',contact='support@example.test',reason='Update homepage copy')
    assert client.post('/api/admin/content',json=body).status_code==200
    assert client.get('/api/content').json()['headline']=='Play Belancer'
    assert client.get('/api/admin/audit').json()['items'][0]['action']=='content.update'

def test_player_cannot_publish_or_review(client):
    account(client)
    assert client.post('/api/admin/results/missing',json={'action':'invalidate','reason':'Unauthorized review'}).status_code==403
    assert client.post('/api/admin/challenges/missing',json={'status':'closed','reason':'Unauthorized closure'}).status_code==403
