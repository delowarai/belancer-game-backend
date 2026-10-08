import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import main as m
from test_api import client, clock, account

def test_account_requires_login(client):
    assert client.get('/api/progress').status_code==401
    for url,body in [('/account/username',{'username':'newname','currentPassword':'longpassword'}),('/account/password',{'currentPassword':'longpassword','newPassword':'anotherpassword'}),('/account/logout-all',{})]:
        assert client.post('/api'+url,json=body).status_code==401

def test_rename_preserves_identity_and_conflicts(client):
    account(client,'otherplayer');client.post('/api/auth/logout',json={});account(client)
    with Session(m.engine) as db:uid=db.scalar(select(m.User).where(m.User.username=='player1')).id
    body={'username':'renamed','currentPassword':'wrongpassword'}
    assert client.post('/api/account/username',json=body).status_code==401
    body['currentPassword']='longpassword';body['username']='OTHERPLAYER'
    assert client.post('/api/account/username',json=body).status_code==409
    body['username']='New_Name'
    assert client.post('/api/account/username',json=body).json()['username']=='new_name'
    with Session(m.engine) as db:
        assert db.get(m.User,uid).username=='new_name'
        assert db.scalar(select(m.AuditLog)).actor=='player1'
    client.post('/api/auth/logout',json={})
    assert client.post('/api/auth/login',json={'username':'player1','password':'longpassword'}).status_code==401
    assert client.post('/api/auth/login',json={'username':'new_name','password':'longpassword'}).status_code==200

def test_password_revokes_all_tokens_and_never_logs_secrets(client):
    account(client)
    client.post('/api/auth/login',json={'username':'player1','password':'longpassword'})
    body={'currentPassword':'wrongpassword','newPassword':'newsecretpassword'}
    assert client.post('/api/account/password',json=body).status_code==401
    assert client.get('/api/auth/me').status_code==200
    body['currentPassword']='longpassword';body['newPassword']='longpassword'
    assert client.post('/api/account/password',json=body).status_code==422
    body['newPassword']='newsecretpassword'
    assert client.post('/api/account/password',json=body).status_code==200
    assert client.get('/api/auth/me').status_code==401
    with Session(m.engine) as db:
        assert not db.scalars(select(m.Login)).all()
        notes=db.scalar(select(m.AuditLog)).details
        assert 'longpassword' not in notes and 'newsecretpassword' not in notes
    assert client.post('/api/auth/login',json={'username':'player1','password':'longpassword'}).status_code==401
    assert client.post('/api/auth/login',json={'username':'player1','password':'newsecretpassword'}).status_code==200

def test_logout_all_other_users_unchanged(client):
    account(client,'otherplayer');account(client)
    assert client.post('/api/account/logout-all',json={}).status_code==200
    assert client.get('/api/auth/me').status_code==401
    with Session(m.engine) as db:
        tokens=db.scalars(select(m.Login)).all()
        assert len(tokens)==1
        assert db.get(m.User,tokens[0].user_id).username=='otherplayer'

def test_progress_privacy_moderation_partial_and_streak(client):
    account(client)
    today=datetime.now(timezone.utc).date()
    with Session(m.engine) as db:
        uid=db.scalar(select(m.User)).id
        for i,(day,status,complete) in enumerate([(today,'validated',True),(today-timedelta(days=1),'validated',False),(today-timedelta(days=2),'invalidated',True)]):
            issued=int(datetime.combine(day,datetime.min.time(),timezone.utc).timestamp())
            r={'session':str(i),'game':'math-sprint','challenge':f'{day}:math-sprint:v2','score':100,'correct':1,'errors':1,'accuracy':50,'duration':10000,'completed':complete,'status':status,'attempt':1,'reviewReason':'private operator note'}
            db.add(m.GameSession(id=str(i),user_id=uid,game='math-sprint',challenge=r['challenge'],attempt=1,issued=issued,payload='{}',result=json.dumps(r)))
        db.add(m.GameSession(id='unfinished',user_id=uid,game='focus-finder',challenge='pending',attempt=1,issued=1,payload='{}'))
        other=m.User(id='other',username='other',password='hash',active=True)
        db.add(other);db.add(m.GameSession(id='private',user_id='other',game='math-sprint',challenge='private',attempt=1,issued=2,payload='{}',result='{}'));db.commit()
    r=client.get('/api/progress?game=math-sprint&limit=1').json()
    assert (r['attempts'],r['submitted'],r['activeDays'],r['streakDays'])==(4,3,2,2)
    g=next(x for x in r['games'] if x['game']=='math-sprint')
    assert (g['validated'],g['completed'],g['accuracy'])==(2,1,50)
    assert r['history']['total']==3 and len(r['history']['items'])==1
    assert 'reviewReason' not in r['history']['items'][0]
    assert client.get('/api/progress?offset=2').json()['history']['items'][0]['status']=='invalidated'
    assert client.get('/api/progress?game=unknown').status_code==422
    assert client.get('/api/progress?limit=101').status_code==422
    assert client.get('/api/progress?offset=-1').status_code==422

def test_settings_reject_role_injection_and_csrf(client):
    account(client)
    assert client.post('/api/account/username',json={'username':'newname','currentPassword':'longpassword','role':'admin'}).status_code==422
    assert client.post('/api/account/password',json={'currentPassword':'longpassword','newPassword':'newpassword'},headers={'origin':'https://evil.example'}).status_code==403
