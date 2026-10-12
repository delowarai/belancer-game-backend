"""Catalog access, account practice history and audited premium policies."""
import json
import secrets
from typing import Literal
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, String, Integer, Text, Boolean, select, func, UniqueConstraint
from sqlalchemy.orm import Session
from app.main import Base

router = APIRouter(prefix='/api', tags=['Product'])

class GamePolicy(Base):
    __tablename__ = 'game_policies'
    slug = Column(String, primary_key=True)
    access = Column(String, nullable=False, default='free')
    price_bdt = Column(Integer, nullable=False, default=0)
    price_usd = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=1)

class Entitlement(Base):
    __tablename__ = 'entitlements'
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    game = Column(String, nullable=False)
    expires = Column(Integer)  # null means permanent single-game ownership
    source = Column(String, nullable=False)
    revoked = Column(Boolean, nullable=False, default=False)

class PracticeHistory(Base):
    __tablename__ = 'practice_history'
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    game = Column(String, nullable=False)
    run_id = Column(String, nullable=False)
    outcome = Column(String, nullable=False)
    created = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('user_id', 'run_id'),)

SAVE_KEYS={'bottle-garden':['belancer-bottle-garden-v1'],'bird-launch':['belancer-bird-progress-v1'],'nuts-bolts':['belancer-bolts-v1','belancer-bolts-spent-v1'],
           'highway-rush':['belancer-racing-v1'],'snake-arena':['belancer-snake-v1']}
class PracticeSave(Base):
    __tablename__='practice_saves'
    user_id=Column(String,primary_key=True)
    game=Column(String,primary_key=True)
    data=Column(Text,nullable=False)
    revision=Column(Integer,nullable=False)
    updated=Column(Integer,nullable=False)

class SaveChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=0,strict=True)
    data:dict[str,str|None]

@router.get('/practice/saves/{game}')
def get_save(game:str,request:Request):
    from app.main import engine,current
    if game not in SAVE_KEYS:raise HTTPException(404,'This game does not support campaign saves')
    with Session(engine) as db:
        user=current(request,db);require_access(db,user,game);row=db.get(PracticeSave,(user.id,game))
        return {'revision':row.revision if row else 0,'data':json.loads(row.data) if row else {},'updated':row.updated if row else None,'authority':'unranked-cosmetic-progress'}

@router.post('/practice/saves/{game}')
def set_save(game:str,body:SaveChange,request:Request):
    from app.main import engine,current,now
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError
    if game not in SAVE_KEYS:raise HTTPException(404,'This game does not support campaign saves')
    if set(body.data)-set(SAVE_KEYS[game]):raise HTTPException(422,'Unsupported save keys')
    encoded=json.dumps(body.data)
    if len(encoded.encode())>60000:raise HTTPException(413,'Save is too large')
    with Session(engine) as db:
        user=current(request,db);require_access(db,user,game)
        if body.revision==0:
            row=PracticeSave(user_id=user.id,game=game,data=encoded,revision=1,updated=now());db.add(row)
            try:db.commit()
            except IntegrityError:db.rollback();raise HTTPException(409,'Progress changed on another device. Reload before saving.')
        else:
            result=db.execute(update(PracticeSave).where(PracticeSave.user_id==user.id,PracticeSave.game==game,PracticeSave.revision==body.revision).values(data=encoded,revision=body.revision+1,updated=now()))
            if result.rowcount!=1:db.rollback();raise HTTPException(409,'Progress changed on another device. Reload before saving.')
            db.commit()
        return {'revision':body.revision+1}

def policy(db, slug):
    row = db.get(GamePolicy, slug)
    return {'access': row.access if row else 'free', 'priceBdt': row.price_bdt if row else 0,
            'priceUsd': row.price_usd if row else 0, 'policyVersion': row.version if row else 1}

def owns(db, user, slug):
    from app.main import now
    if not user:return False
    if user.role == 'admin':return True
    return any(not e.revoked and (e.expires is None or e.expires > now()) for e in db.scalars(
        select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.game.in_([slug, '*']))).all())

def require_access(db, user, slug):
    from app.admin import catalog
    game = next((g for g in catalog(db) if g['slug'] == slug), None)
    if not game:raise HTTPException(404, 'Game not found')
    if not game['available']:raise HTTPException(409, 'This game is currently unavailable')
    if game['access'] == 'premium' and not owns(db, user, slug):
        raise HTTPException(403, 'Premium access required')
    return game

@router.get('/games/{slug}/access')
def game_access(slug: str, request: Request):
    from app.main import engine, current
    from app.admin import catalog
    with Session(engine) as db:
        game = next((g for g in catalog(db) if g['slug'] == slug), None)
        if not game:raise HTTPException(404, 'Game not found')
        try:user=current(request, db)
        except HTTPException as exc:
            if exc.status_code != 401:raise
            user=None
        return {'slug':slug, 'available':game['available'], 'access':game['access'],
                'allowed':game['available'] and (game['access']=='free' or owns(db,user,slug)),
                'signedIn':user is not None,'accountId':user.id if user else None, 'priceBdt':game['priceBdt'], 'priceUsd':game['priceUsd']}

class PolicyChange(BaseModel):
    model_config=ConfigDict(extra='forbid', str_strip_whitespace=True)
    access:Literal['free','premium']
    priceBdt:int=Field(ge=0,le=50000000,strict=True)
    priceUsd:int=Field(ge=0,le=10000000,strict=True)
    reason:str=Field(min_length=3,max_length=500)

@router.post('/admin/games/{slug}/policy')
def change_policy(slug:str, body:PolicyChange, request:Request):
    from app.main import engine, GAMES
    from app.admin import authorize, audit
    if slug not in {g['slug'] for g in GAMES}:raise HTTPException(404,'Game not found')
    # Ranked competition remains free; premium is for future practice releases.
    if body.access=='premium' and not next(g['practiceOnly'] for g in GAMES if g['slug']==slug):
        raise HTTPException(422,'Ranked games must remain free')
    with Session(engine) as db:
        actor=authorize(request,db);before=policy(db,slug)
        row=db.scalar(select(GamePolicy).where(GamePolicy.slug==slug).with_for_update())
        if row is None:row=GamePolicy(slug=slug,version=0);db.add(row)
        row.access=body.access;row.price_bdt=body.priceBdt;row.price_usd=body.priceUsd;row.version+=1
        audit(db,actor.username,'game.policy',slug,{'before':before,'after':body.model_dump(exclude={'reason'}),'reason':body.reason})
        db.commit();return policy(db,slug)

class Grant(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    username:str=Field(min_length=3,max_length=24)
    game:str=Field(min_length=1,max_length=80)
    days:int=Field(default=30,ge=1,le=3650,strict=True)
    reason:str=Field(min_length=3,max_length=500)

@router.post('/admin/entitlements')
def grant(body:Grant,request:Request):
    from app.main import engine,User,GAMES,now
    from app.admin import authorize,audit
    if body.game!='*' and body.game not in {g['slug'] for g in GAMES}:raise HTTPException(404,'Game not found')
    with Session(engine) as db:
        actor=authorize(request,db);user=db.scalar(select(User).where(User.username==body.username.lower()))
        if not user:raise HTTPException(404,'Player not found')
        row=Entitlement(id=secrets.token_hex(16),user_id=user.id,game=body.game,expires=now()+body.days*86400,source='admin',revoked=False)
        db.add(row);audit(db,actor.username,'access.grant',row.id,{'username':user.username,'game':body.game,'days':body.days,'reason':body.reason});db.commit()
        return {'id':row.id,'expires':row.expires}

@router.get('/admin/entitlements')
def access_list(request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
    from app.main import engine,User
    from app.admin import authorize
    with Session(engine) as db:
        authorize(request,db);query=select(Entitlement,User.username).join(User,User.id==Entitlement.user_id)
        return {'items':[{'id':e.id,'username':name,'game':e.game,'expires':e.expires,'revoked':e.revoked,'source':e.source}
                         for e,name in db.execute(query.order_by(Entitlement.id).offset(offset).limit(limit)).all()],
                'total':db.scalar(select(func.count()).select_from(query.subquery()))}

class Reason(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    reason:str=Field(min_length=3,max_length=500)

@router.post('/admin/entitlements/{eid}/revoke')
def revoke(eid:str,body:Reason,request:Request):
    from app.main import engine
    from app.admin import authorize,audit
    with Session(engine) as db:
        actor=authorize(request,db);row=db.get(Entitlement,eid)
        if not row:raise HTTPException(404,'Access record not found')
        row.revoked=True;audit(db,actor.username,'access.revoke',eid,{'reason':body.reason});db.commit();return {'ok':True}

class PracticeRun(BaseModel):
    model_config=ConfigDict(extra='forbid')
    runId:str=Field(min_length=8,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    game:str=Field(min_length=1,max_length=80)
    outcome:Literal['completed','played']

@router.post('/practice/history')
def record_practice(body:PracticeRun,request:Request):
    from app.main import engine,current,now
    from sqlalchemy.exc import IntegrityError
    with Session(engine) as db:
        user=current(request,db);require_access(db,user,body.game)
        prior=db.scalar(select(PracticeHistory).where(PracticeHistory.user_id==user.id,PracticeHistory.run_id==body.runId))
        if prior:
            if prior.game!=body.game:raise HTTPException(409,'Run identifier already belongs to another game')
            if body.outcome=='completed':prior.outcome='completed';db.commit()
            return {'id':prior.id}
        row=PracticeHistory(id=secrets.token_hex(16),user_id=user.id,game=body.game,run_id=body.runId,outcome=body.outcome,created=now());db.add(row)
        try:db.commit()
        except IntegrityError:db.rollback();raise HTTPException(409,'Practice run already recorded; retry')
        return {'id':row.id}

@router.get('/practice/history')
def practice_history(request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
    from app.main import engine,current
    with Session(engine) as db:
        user=current(request,db);query=select(PracticeHistory).where(PracticeHistory.user_id==user.id)
        return {'items':[{'id':p.id,'game':p.game,'outcome':p.outcome,'created':p.created}
                         for p in db.scalars(query.order_by(PracticeHistory.created.desc(),PracticeHistory.id).offset(offset).limit(limit)).all()],
                'total':db.scalar(select(func.count()).select_from(query.subquery())), 'scoreAuthority':'unranked-browser-activity'}

@router.get('/account/access')
def my_access(request:Request):
    from app.main import engine,current,now
    with Session(engine) as db:
        user=current(request,db)
        return [{'game':e.game,'expires':e.expires,'source':e.source} for e in db.scalars(select(Entitlement).where(Entitlement.user_id==user.id,Entitlement.revoked.is_(False))).all() if e.expires is None or e.expires>now()]
