import hashlib
import hmac
import json
import time
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import main as m, billing as b, product as p
from test_api import client, clock, account
from test_admin import admin

def test_catalog_free_and_ranked_separate(client):
    games=client.get('/api/games').json()
    assert len(games)==34 and all(g['access']=='free' for g in games)
    assert client.get('/api/games/nuts-bolts/access').json()['allowed']
    assert len(client.get('/api/challenges').json())==5

def test_policy_roles_and_ranked_protection(client):
    body={'access':'premium','priceBdt':10000,'priceUsd':500,'reason':'Test premium release'}
    account(client)
    assert client.post('/api/admin/games/nuts-bolts/policy',json=body).status_code==403
    admin(client)
    assert client.post('/api/admin/games/math-sprint/policy',json=body).status_code==422
    assert client.post('/api/admin/games/nuts-bolts/policy',json=body).json()['policyVersion']==1
    client.post('/api/auth/logout',json={})
    assert not client.get('/api/games/nuts-bolts/access').json()['allowed']

def test_save_revision_and_account_isolation(client):
    account(client,'first')
    data={'revision':0,'data':{'belancer-bolts-v1':'{"level":3}'}}
    assert client.post('/api/practice/saves/nuts-bolts',json=data).json()['revision']==1
    assert client.post('/api/practice/saves/nuts-bolts',json=data).status_code==409
    assert client.post('/api/practice/saves/nuts-bolts',json={'revision':1,'data':{'secret':'bad'}}).status_code==422
    account(client,'second')
    assert client.get('/api/practice/saves/nuts-bolts').json()['data']=={}

def seed_order(client,sandbox=False):
    account(client)
    with Session(m.engine) as db:
        uid=db.scalar(select(m.User.id).where(m.User.username=='player1'))
        row=b.Order(id='order1',user_id=uid,request_key='request-000000001',product='nuts-bolts',provider='stripe',amount=500,currency='USD',days=0,status='pending',sandbox=sandbox,provider_session='cs_example',created=1)
        db.add(row);db.commit()
    return {'type':'checkout.session.completed','livemode':not sandbox,'data':{'object':{'id':'cs_example','client_reference_id':'order1','payment_status':'paid','amount_total':500,'currency':'usd'}}}

def deliver(client,event,monkeypatch,signature=None):
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','test-secret')
    raw=json.dumps(event).encode();stamp=int(time.time())
    digest=hmac.new(b'test-secret',str(stamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    return client.post('/api/billing/stripe/webhook',content=raw,headers={'stripe-signature':signature or f't={stamp},v1={digest}'})

def test_verified_payment_once(client,monkeypatch):
    event=seed_order(client)
    assert deliver(client,event,monkeypatch).status_code==200
    assert deliver(client,event,monkeypatch).status_code==200
    with Session(m.engine) as db:
        assert len(db.scalars(select(p.Entitlement)).all())==1
        assert db.get(b.Order,'order1').status=='paid'

def test_sandbox_does_not_unlock_access(client,monkeypatch):
    assert deliver(client,seed_order(client,True),monkeypatch).status_code==200
    with Session(m.engine) as db:assert not db.scalars(select(p.Entitlement)).all()

@pytest.mark.parametrize('field,value',[('amount_total',1),('currency','bdt'),('id','other')])
def test_mismatched_payment_rejected(client,monkeypatch,field,value):
    event=seed_order(client);event['data']['object'][field]=value
    assert deliver(client,event,monkeypatch).status_code==400
    with Session(m.engine) as db:assert not db.scalars(select(p.Entitlement)).all()

def test_bad_signature_and_payload(client,monkeypatch):
    event=seed_order(client)
    assert deliver(client,event,monkeypatch,'t=1,v1=bad').status_code==400
    assert deliver(client,[],monkeypatch).status_code==400
    assert deliver(client,{'data':[]},monkeypatch).status_code==400

def test_orders_private(client):
    seed_order(client);account(client,'other')
    assert client.get('/api/billing/orders/order1').status_code==404
    assert client.get('/api/billing/orders').json()['items']==[]

def test_payment_settings_fail_closed(client):
    settings=client.get('/api/billing/settings').json()
    assert not settings['stripeEnabled'] and not settings['sslcommerzEnabled']
    assert client.get('/api/admin/monetization').status_code==401

def test_checkout_uses_server_price_and_reuses_order(client,monkeypatch):
    account(client)
    with Session(m.engine) as db:
        db.add(p.GamePolicy(slug='nuts-bolts',access='premium',price_bdt=10000,price_usd=500,version=1))
        db.add(m.SiteContent(key='monetization',value=json.dumps({'stripeEnabled':True})));db.commit()
    monkeypatch.setattr(b,'provider_ready',lambda _:True)
    calls=[]
    def hosted(row,body,title):
        calls.append(row.amount);return 'cs_checkout','https://checkout.stripe.com/example'
    monkeypatch.setattr(b,'create_checkout',hosted)
    body={'product':'nuts-bolts','provider':'stripe','requestKey':'unique-checkout-0001','name':'Test Customer','email':'test@example.com','phone':'0123456789','address':'Test street','city':'Dhaka'}
    one=client.post('/api/billing/checkout',json=body)
    assert one.status_code==200 and one.json()['amount']==500
    assert client.post('/api/billing/checkout',json=body).json()['id']==one.json()['id']
    assert calls==[500]
    assert client.post('/api/billing/checkout',json={**body,'amount':1}).status_code==422

def test_ssl_ipn_requires_server_validation(client,monkeypatch):
    seed_order(client,True)
    with Session(m.engine) as db:
        row=db.get(b.Order,'order1');row.provider='sslcommerz';row.currency='BDT';row.amount=10000;db.commit()
    monkeypatch.setenv('SSLCOMMERZ_STORE_ID','test-store');monkeypatch.setenv('SSLCOMMERZ_STORE_PASSWORD','test-password')
    monkeypatch.setenv('SSLCOMMERZ_SANDBOX','true')
    verified={'status':'VALID','risk_level':'0','tran_id':'order1','amount':'100','currency':'BDT','val_id':'validation1'}
    class Gateway:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,*args,**kwargs):
            return b.httpx.Response(200,json=verified,request=b.httpx.Request('GET','https://sandbox.sslcommerz.com'))
    monkeypatch.setattr(b.httpx,'AsyncClient',Gateway)
    verified['amount']='1'
    assert client.post('/api/billing/sslcommerz/ipn',content='val_id=validation1').status_code==400
    verified['amount']='100'
    assert client.post('/api/billing/sslcommerz/ipn',content='val_id=validation1').status_code==200
    with Session(m.engine) as db:assert db.get(b.Order,'order1').status=='paid'

