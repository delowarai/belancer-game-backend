import json
from datetime import date
import pytest
from sqlalchemy.orm import Session
from app import main as m, standings as board
from app.admin import DEFAULTS
from test_api import client, clock, account

@pytest.fixture
def day(monkeypatch):
    monkeypatch.setattr(board,'today',lambda:date(2026,10,8))

def add(uid,name,day='2026-10-08',game='math-sprint',score=100,status='validated',configuration=None,attempt=1,active=True,**overrides):
    cid=f'{day}:{game}:v2';sid=f'{uid}:{cid}:{attempt}'
    r={'version':'v2','score':score,'accuracy':100,'correct':score//100,'errors':0,'duration':1000,'completed':True,'status':status,'submitted':1000+attempt,'attempt':attempt,'reviewReason':'private note','reviewer':'private operator',**overrides}
    with Session(m.engine) as db:
        if not db.get(m.User,uid):db.add(m.User(id=uid,username=name,password='unused',active=active))
        db.add(m.GameSession(id=sid,user_id=uid,game=game,challenge=cid,attempt=attempt,issued=1000,payload=json.dumps({'content':{'_config':configuration or DEFAULTS[game]}}),result=json.dumps(r)))
        db.commit()

def test_daily_weekly_alltime_dates_and_best(client,day):
    add('a','alice',score=100);add('a','alice',score=200,attempt=2)
    add('a','alice',day='2026-10-06',score=300)
    add('b','bob',day='2026-10-04',score=400)
    add('c','carol',day='2026-10-09',score=900)
    daily=client.get('/api/standings/math-sprint').json()
    assert daily['total']==1 and daily['items'][0]['score']==200
    weekly=client.get('/api/standings/math-sprint?period=weekly').json()
    assert weekly['start']=='2026-10-05' and weekly['endExclusive']=='2026-10-12'
    # Future challenge results must never be counted, including later days in the current week.
    assert weekly['total']==1 and weekly['items'][0]['score']==300
    alltime=client.get('/api/standings/math-sprint?period=all-time').json()
    assert [x['username'] for x in alltime['items']]==['bob','alice']
    old=client.get('/api/standings/math-sprint?day=2026-10-04').json()
    assert old['items'][0]['username']=='bob'

def test_separate_configuration_and_version(client,day):
    custom={'durationSeconds':10,'rounds':2}
    add('a','alice',score=100)
    add('b','bob',score=200,configuration=custom)
    add('c','carol',score=999,version='v1')
    r=client.get('/api/standings/math-sprint').json()
    assert [x['username'] for x in r['items']]==['alice'] and len(r['profiles'])==2
    customid=board.profile(custom)
    r=client.get('/api/standings/math-sprint?rules='+customid).json()
    assert r['configuration']==custom and r['items'][0]['username']=='bob'
    assert client.get('/api/standings/math-sprint?rules=missing').status_code==422

def test_moderation_suspension_paging_and_privacy(client,day):
    account(client,'viewer')
    with Session(m.engine) as db:uid=db.query(m.User).filter_by(username='viewer').one().id
    add(uid,'viewer',score=100)
    add('a','alice',score=500,status='invalidated')
    add('a','alice',score=200,attempt=2)
    add('b','bob',score=900,active=False)
    add('c','carol',score=300,flagged=True)
    r=client.get('/api/standings/math-sprint?limit=1&offset=1').json()
    assert r['total']==3 and r['items'][0]['rank']==2 and r['yourRank']==3
    assert r['items'][0]['username']=='alice'
    assert 'private' not in json.dumps(r)
    r=client.get('/api/standings/math-sprint?offset=2').json()
    assert r['items'][0]['isYou']

@pytest.mark.parametrize('game,first,second',[
    ('memory-grid',{'correctRounds':3,'score':90,'errors':2},{'correctRounds':2,'score':100,'errors':0}),
    ('pair-finder',{'completed':True,'duration':3000},{'completed':False,'duration':1000}),
    ('focus-finder',{'score':100,'accuracy':100},{'score':100,'accuracy':90}),
    ('quick-match',{'score':200},{'score':100}),
    ('math-sprint',{'score':100,'submitted':1000},{'score':100,'submitted':2000}),
])
def test_game_specific_rank_rules(client,day,game,first,second):
    add('a','alice',game=game,**first);add('b','bob',game=game,**second)
    assert client.get('/api/standings/'+game).json()['items'][0]['username']=='alice'

@pytest.mark.parametrize('query',['period=monthly','day=bad','day=2026-10-09','offset=-1','limit=101','limit=0'])
def test_invalid_filters(client,day,query):
    assert client.get('/api/standings/math-sprint?'+query).status_code==422
    assert client.get('/api/standings/unknown').status_code==404
