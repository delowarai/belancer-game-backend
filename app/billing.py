"""Hosted payment checkout. Browser redirects never grant access."""
import hashlib
import hmac
import json
import os
import secrets
import time
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import parse_qs, urlparse
import httpx
from fastapi.responses import RedirectResponse
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, String, Integer, Text, Boolean, UniqueConstraint, select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.main import Base
from app.product import Entitlement

router=APIRouter(prefix='/api',tags=['Billing'])

class Order(Base):
    __tablename__='orders'
    id=Column(String,primary_key=True)
    user_id=Column(String,nullable=False,index=True)
    request_key=Column(String,nullable=False)
    product=Column(String,nullable=False)
    provider=Column(String,nullable=False)
    amount=Column(Integer,nullable=False)  # minor units, never browser supplied
    currency=Column(String,nullable=False)
    days=Column(Integer,nullable=False,default=0)
    status=Column(String,nullable=False,default='created')
    sandbox=Column(Boolean,nullable=False,default=True)
    provider_session=Column(String)
    validation_id=Column(String,unique=True)
    checkout_url=Column(Text)
    created=Column(Integer,nullable=False)
    paid=Column(Integer)
    __table_args__=(UniqueConstraint('user_id','request_key'),)

DEFAULT_SETTINGS={'sslcommerzEnabled':False,'stripeEnabled':False,'passEnabled':False,
                  'passPriceBdt':0,'passPriceUsd':0,'passDays':30,
                  'supportEmail':'','refundPolicy':'Premium sales are not enabled.'}

def settings(db):
    from app.main import SiteContent
    row=db.get(SiteContent,'monetization')
    return {**DEFAULT_SETTINGS,**(json.loads(row.value) if row else {})}

def provider_ready(provider):
    required=['SSLCOMMERZ_STORE_ID','SSLCOMMERZ_STORE_PASSWORD'] if provider=='sslcommerz' else ['STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET']
    origin=os.getenv('PAYMENT_PUBLIC_ORIGIN','')
    return all(os.getenv(key) for key in required) and urlparse(origin).scheme=='https' and bool(urlparse(origin).netloc)

def provider_test(provider):
    return os.getenv('SSLCOMMERZ_SANDBOX','true').lower()=='true' if provider=='sslcommerz' else not os.getenv('STRIPE_SECRET_KEY','').startswith('sk_live_')

def public_settings(db):
    value=settings(db)
    return {**value,'providers':[{'id':p,'enabled':value[p+'Enabled'],'ready':provider_ready(p),
                                'sandbox':provider_test(p)} for p in ['sslcommerz','stripe']]}

@router.get('/billing/settings')
def billing_settings():
    from app.main import engine
    with Session(engine) as db:return public_settings(db)

class SettingsChange(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    sslcommerzEnabled:bool=Field(strict=True)
    stripeEnabled:bool=Field(strict=True)
    passEnabled:bool=Field(strict=True)
    passPriceBdt:int=Field(ge=0,le=50000000,strict=True)
    passPriceUsd:int=Field(ge=0,le=10000000,strict=True)
    passDays:int=Field(ge=1,le=365,strict=True)
    supportEmail:str=Field(max_length=254)
    refundPolicy:str=Field(min_length=10,max_length=2000)
    reason:str=Field(min_length=3,max_length=500)

@router.get('/admin/monetization')
def admin_settings(request:Request):
    from app.main import engine
    from app.admin import authorize
    with Session(engine) as db:authorize(request,db);return public_settings(db)

@router.post('/admin/monetization')
def edit_settings(body:SettingsChange,request:Request):
    from app.main import engine,SiteContent
    from app.admin import authorize,audit
    with Session(engine) as db:
        actor=authorize(request,db);before=settings(db);after=body.model_dump(exclude={'reason'})
        if (body.sslcommerzEnabled or body.stripeEnabled) and ('@' not in body.supportEmail or len(body.refundPolicy.strip())<20):
            raise HTTPException(422,'Configure support email and a refund policy before enabling payments')
        for provider in ['sslcommerz','stripe']:
            if after[provider+'Enabled'] and not provider_ready(provider):raise HTTPException(422,f'{provider} environment credentials and HTTPS callback origin are required')
        row=db.get(SiteContent,'monetization')
        if row is None:row=SiteContent(key='monetization');db.add(row)
        row.value=json.dumps(after);audit(db,actor.username,'monetization.update','billing',{'before':before,'after':after,'reason':body.reason});db.commit()
        return public_settings(db)

class Checkout(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    product:str=Field(min_length=1,max_length=80)
    provider:Literal['sslcommerz','stripe']
    requestKey:str=Field(min_length=16,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    name:str=Field(min_length=2,max_length=100)
    email:str=Field(min_length=5,max_length=254,pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    phone:str=Field(min_length=6,max_length=30,pattern=r'^\+?[0-9 ()-]+$')
    address:str=Field(min_length=2,max_length=200)
    city:str=Field(min_length=2,max_length=80)

def order_info(row):
    return {'id':row.id,'product':row.product,'provider':row.provider,'amount':row.amount,
            'currency':row.currency,'status':row.status,'sandbox':row.sandbox,'created':row.created,
            'paid':row.paid,'checkoutUrl':row.checkout_url if row.status=='pending' else None}

def ssl_base():return 'https://sandbox.sslcommerz.com' if provider_test('sslcommerz') else 'https://securepay.sslcommerz.com'

def create_checkout(row,body,title):
    origin=os.environ['PAYMENT_PUBLIC_ORIGIN'].rstrip('/')
    success=origin+'/billing?order='+row.id
    if row.provider=='stripe':
        response=httpx.post('https://api.stripe.com/v1/checkout/sessions',headers={
            'Authorization':'Bearer '+os.environ['STRIPE_SECRET_KEY'],'Idempotency-Key':row.id},data={
            'mode':'payment','success_url':success,'cancel_url':success+'&cancelled=1',
            'client_reference_id':row.id,'customer_email':body.email,
            'metadata[order_id]':row.id,'line_items[0][quantity]':'1',
            'line_items[0][price_data][currency]':'usd','line_items[0][price_data][unit_amount]':str(row.amount),
            'line_items[0][price_data][product_data][name]':title},timeout=20)
        response.raise_for_status();data=response.json();url=data.get('url','');session=data.get('id','')
        expected={'checkout.stripe.com'}
    else:
        response=httpx.post(ssl_base()+'/gwprocess/v4/api.php',data={
            'store_id':os.environ['SSLCOMMERZ_STORE_ID'],'store_passwd':os.environ['SSLCOMMERZ_STORE_PASSWORD'],
            'total_amount':str(Decimal(row.amount)/100),'currency':'BDT','tran_id':row.id,
            'success_url':origin+'/api/billing/sslcommerz/return','fail_url':origin+'/api/billing/sslcommerz/return','cancel_url':origin+'/api/billing/sslcommerz/return',
            'ipn_url':origin+'/api/billing/sslcommerz/ipn','shipping_method':'NO',
            'product_name':title,'product_category':'Digital games','product_profile':'non-physical-goods',
            'cus_name':body.name,'cus_email':body.email,'cus_phone':body.phone,
            'cus_add1':body.address,'cus_city':body.city,'cus_country':'Bangladesh'},timeout=20)
        response.raise_for_status();data=response.json()
        if data.get('status')!='SUCCESS':raise ValueError('Gateway rejected checkout')
        url=data.get('GatewayPageURL','');session=data.get('sessionkey','');expected={urlparse(ssl_base()).hostname}
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in expected or not session:raise ValueError('Invalid checkout destination')
    return session,url

@router.post('/billing/checkout')
def checkout(body:Checkout,request:Request):
    from app.main import engine,current,now
    from app.admin import catalog
    from app.product import owns
    with Session(engine) as db:
        user=current(request,db);cfg=settings(db)
        prior=db.scalar(select(Order).where(Order.user_id==user.id,Order.request_key==body.requestKey))
        if prior:
            if prior.product!=body.product or prior.provider!=body.provider:raise HTTPException(409,'Checkout key belongs to another purchase')
            return order_info(prior)
        if not cfg[body.provider+'Enabled'] or not provider_ready(body.provider):raise HTTPException(409,'This payment method is not enabled yet')
        currency='BDT' if body.provider=='sslcommerz' else 'USD';suffix='Bdt' if currency=='BDT' else 'Usd'
        if body.product=='premium-pass':
            if not cfg['passEnabled']:raise HTTPException(409,'Premium pass is not available')
            if not any(g['available'] and g['access']=='premium' for g in catalog(db)):raise HTTPException(409,'No premium games are available yet')
            amount=cfg['passPrice'+suffix];days=cfg['passDays'];title=f'Belancer Premium · {days} days'
        else:
            game=next((g for g in catalog(db) if g['slug']==body.product),None)
            if not game or not game['available'] or game['access']!='premium':raise HTTPException(409,'This game is not available for purchase')
            if owns(db,user,body.product):raise HTTPException(409,'You already have access to this game')
            amount=game['price'+suffix];days=0;title=game['name']
        if amount<(1000 if currency=='BDT' else 50):raise HTTPException(409,'A valid price has not been configured')
        row=Order(id=secrets.token_hex(16),user_id=user.id,request_key=body.requestKey,product=body.product,
                  provider=body.provider,amount=amount,currency=currency,days=days,status='created',sandbox=provider_test(body.provider),created=now());db.add(row)
        try:db.commit()
        except IntegrityError:db.rollback();raise HTTPException(409,'Checkout is already being created; retry the same request')
        try:row.provider_session,row.checkout_url=create_checkout(row,body,title);row.status='pending';db.commit()
        except (httpx.HTTPError,ValueError,KeyError):
            row.status='failed';db.commit();raise HTTPException(502,'Payment provider is unavailable. No access was purchased; retry with a new checkout.')
        return order_info(row)

def mark_paid(db,row,validation_id):
    from app.main import now,User
    from app.admin import audit
    if row.status=='paid':return
    duplicate=db.scalar(select(Order).where(Order.validation_id==validation_id))
    if duplicate and duplicate.id!=row.id:raise HTTPException(409,'Payment already used')
    if not db.scalar(select(User).where(User.id==row.user_id).with_for_update()):raise HTTPException(409,'Order owner no longer exists')
    expiry=None;game=row.product
    if game=='premium-pass':
        game='*';latest=max([now()]+[e.expires or now() for e in db.scalars(select(Entitlement).where(Entitlement.user_id==row.user_id,Entitlement.game=='*',Entitlement.revoked.is_(False))).all()])
        expiry=latest+row.days*86400
    row.validation_id=validation_id;row.status='paid';row.paid=now()
    if not row.sandbox:
        db.add(Entitlement(id=row.id,user_id=row.user_id,game=game,expires=expiry,source='order:'+row.id,revoked=False))
    audit(db,'payment-provider','payment.validated',row.id,{'provider':row.provider,'amount':row.amount,'currency':row.currency,'sandbox':row.sandbox})
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,'Payment has already been processed; retry notification')

@router.post('/billing/sslcommerz/ipn')
async def ssl_ipn(request:Request):
    from app.main import engine
    raw=await request.body()
    if len(raw)>16384:raise HTTPException(413,'Notification too large')
    try:values=parse_qs(raw.decode('utf-8',errors='strict'))
    except UnicodeDecodeError:raise HTTPException(400,'Invalid notification encoding')
    val_id=values.get('val_id',[''])[0]
    if not val_id or len(val_id)>200:raise HTTPException(400,'Missing validation identifier')
    try:
        async with httpx.AsyncClient() as gateway:
            response=await gateway.get(ssl_base()+'/validator/api/validationserverAPI.php',params={
            'val_id':val_id,'store_id':os.environ['SSLCOMMERZ_STORE_ID'],
            'store_passwd':os.environ['SSLCOMMERZ_STORE_PASSWORD'],'format':'json','v':'1'},timeout=20)
        response.raise_for_status();verified=response.json()
    except (httpx.HTTPError,ValueError,KeyError):raise HTTPException(502,'Provider validation unavailable; retry notification')
    if not isinstance(verified,dict):raise HTTPException(400,'Invalid validation response')
    if verified.get('status') not in {'VALID','VALIDATED'} or str(verified.get('risk_level','0'))!='0':raise HTTPException(400,'Payment was not validated')
    with Session(engine) as db:
        row=db.scalar(select(Order).where(Order.id==verified.get('tran_id')).with_for_update())
        if not row or row.provider!='sslcommerz' or row.sandbox!=provider_test('sslcommerz'):raise HTTPException(400,'Unknown payment order')
        try:amount=Decimal(str(verified.get('amount')))*100
        except InvalidOperation:raise HTTPException(400,'Invalid payment amount')
        if amount!=row.amount or verified.get('currency')!=row.currency or verified.get('val_id')!=val_id:raise HTTPException(400,'Payment details do not match the order')
        mark_paid(db,row,'ssl:'+val_id);return {'ok':True}

def verify_stripe(raw,signature):
    secret=os.getenv('STRIPE_WEBHOOK_SECRET','')
    if not secret:raise HTTPException(503,'Stripe webhook is not configured')
    try:
        values=parse_qs(signature.replace(',', '&'));stamp=int(values['t'][0]);signatures=values['v1']
    except (KeyError,ValueError):raise HTTPException(400,'Invalid webhook signature')
    if abs(time.time()-stamp)>300:raise HTTPException(400,'Expired webhook signature')
    expected=hmac.new(secret.encode(),str(stamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected,value) for value in signatures):raise HTTPException(400,'Invalid webhook signature')
    try:
        event=json.loads(raw)
        if not isinstance(event,dict) or not isinstance(event.get('data',{}),dict) or not isinstance(event.get('data',{}).get('object',{}),dict):raise ValueError()
        return event
    except (ValueError,UnicodeDecodeError):raise HTTPException(400,'Invalid webhook payload')

@router.post('/billing/stripe/webhook')
async def stripe_webhook(request:Request):
    from app.main import engine
    raw=await request.body()
    if len(raw)>262144:raise HTTPException(413,'Notification too large')
    event=verify_stripe(raw,request.headers.get('stripe-signature',''))
    if event.get('type') not in {'checkout.session.completed','checkout.session.async_payment_succeeded'}:return {'received':True}
    session=event.get('data',{}).get('object',{})
    if session.get('payment_status')!='paid':return {'received':True}
    with Session(engine) as db:
        row=db.scalar(select(Order).where(Order.id==session.get('client_reference_id')).with_for_update())
        if not row or row.provider!='stripe':raise HTTPException(400,'Unknown payment order')
        if row.provider_session!=session.get('id') or session.get('amount_total')!=row.amount or str(session.get('currency','')).upper()!=row.currency or bool(event.get('livemode'))==row.sandbox:raise HTTPException(400,'Payment details do not match the order')
        mark_paid(db,row,'stripe:'+session['id']);return {'received':True}

@router.get('/billing/orders')
def my_orders(request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
    from app.main import engine,current
    with Session(engine) as db:
        user=current(request,db);query=select(Order).where(Order.user_id==user.id)
        return {'items':[order_info(row) for row in db.scalars(query.order_by(Order.created.desc(),Order.id).offset(offset).limit(limit)).all()],
                'total':db.scalar(select(func.count()).select_from(query.subquery()))}

@router.get('/billing/orders/{oid}')
def my_order(oid:str,request:Request):
    from app.main import engine,current
    with Session(engine) as db:
        user=current(request,db);row=db.get(Order,oid)
        if not row or row.user_id!=user.id:raise HTTPException(404,'Order not found')
        return order_info(row)

@router.get('/admin/orders')
def admin_orders(request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
    from app.main import engine,User
    from app.admin import authorize
    with Session(engine) as db:
        authorize(request,db);query=select(Order,User.username).join(User,User.id==Order.user_id)
        return {'items':[{**order_info(row),'username':name} for row,name in db.execute(query.order_by(Order.created.desc(),Order.id).offset(offset).limit(limit)).all()],
                'total':db.scalar(select(func.count()).select_from(query.subquery()))}

@router.post('/billing/sslcommerz/return')
async def ssl_return(request:Request):
    # Provider redirects carry no authority. Only the verified IPN grants access.
    raw=await request.body()
    if len(raw)>16384:raise HTTPException(413,'Notification too large')
    return RedirectResponse(os.environ['PAYMENT_PUBLIC_ORIGIN'].rstrip('/')+'/billing',status_code=303)
