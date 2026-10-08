"""Role-protected administration. All mutations have a reason and audit record."""
import json
import secrets
from datetime import date, datetime, timezone, timedelta
from typing import Literal
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select,func,delete
from sqlalchemy.orm import Session

router=APIRouter(prefix='/api/admin',tags=['Administration'])
DEFAULTS={'memory-grid':{'durationSeconds':180,'rounds':10,'cells':3},'pair-finder':{'durationSeconds':180},'quick-match':{'durationSeconds':60,'rounds':120},'focus-finder':{'durationSeconds':120,'rounds':10},'math-sprint':{'durationSeconds':60,'rounds':120}}
def authorize(request,db):
    from app.main import current
    actor=current(request,db)
    if actor.role!='admin':raise HTTPException(403,'Admin role required')
    return actor
def audit(db,actor,action,target,details):
    from app.main import AuditLog,now
    db.add(AuditLog(id=secrets.token_hex(16),actor=actor,action=action,target=target,details=json.dumps(details),created=now()))
def catalog(db):
    from app.main import GAMES,GameSettings
    settings={x.slug:x for x in db.scalars(select(GameSettings)).all()}
    return [{**g,**({'name':settings[g['slug']].name,'description':settings[g['slug']].description,'instructions':settings[g['slug']].instructions,'available':settings[g['slug']].available} if g['slug'] in settings else {'instructions':'','available':True})} for g in GAMES]
def config(db,game):
    from app.main import GameSettings
    settings=db.get(GameSettings,game)
    return {**DEFAULTS[game],**(json.loads(settings.configuration) if settings else {})}

DEFAULT_CONTENT={'headline':'A little sharper.\nA little every day.','description':'Give your curiosity a place to play. Five quick games to challenge your memory, focus, and thinking.','contact':'Contact details must be supplied by the deployment operator before public release.','faq':[{'question':'Can I play for free?','answer':'Yes. Practice is unlimited. Create an account for daily ranked challenges.'},{'question':'What do scores mean?','answer':'Scores measure your performance in a particular game. They are not IQ scores or medical assessments.'},{'question':'How do challenges work?','answer':'Each game has a UTC daily challenge with three attempts. Your best validated result appears in its leaderboard.'}]}
def site_content(db):
    from app.main import SiteContent
    value=db.get(SiteContent,'public')
    return json.loads(value.value) if value else DEFAULT_CONTENT

class FAQ(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    question:str=Field(min_length=1,max_length=200)
    answer:str=Field(min_length=1,max_length=1000)
class ContentChange(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    headline:str=Field(min_length=1,max_length=120)
    description:str=Field(min_length=1,max_length=500)
    contact:str=Field(min_length=1,max_length=1000)
    faq:list[FAQ]=Field(min_length=1,max_length=20)
    reason:str=Field(min_length=3,max_length=500)
@router.get('/content')
def content_settings(request:Request):
    from app.main import engine
    with Session(engine) as db:authorize(request,db);return site_content(db)
@router.post('/content')
def update_content(body:ContentChange,request:Request):
    from app.main import engine,SiteContent
    with Session(engine) as db:
        actor=authorize(request,db);before=site_content(db);value=body.model_dump(exclude={'reason'})
        item=db.get(SiteContent,'public')
        if item is None:item=SiteContent(key='public');db.add(item)
        item.value=json.dumps(value)
        audit(db,actor.username,'content.update','public',{'before':before,'after':value,'reason':body.reason});db.commit()
        return value
def validate_config(game,value):
    allowed=set(DEFAULTS[game])
    if set(value)!=allowed:raise HTTPException(422,'Unsupported configuration keys')
    if any(type(x)!=int for x in value.values()):raise HTTPException(422,'Configuration values must be integers')
    if not 10<=value['durationSeconds']<=600:raise HTTPException(422,'Duration must be 10–600 seconds')
    if 'rounds' in value:
        maximum=10 if game=='memory-grid' else 50 if game=='focus-finder' else 120
        if not 1<=value['rounds']<=maximum:raise HTTPException(422,f'Rounds must be 1–{maximum}')
    if 'cells' in value and not 3<=value['cells']<=8:raise HTTPException(422,'Memory cells must be 3–8')
    if game=='memory-grid' and value['durationSeconds']*1000<value['rounds']*2100:raise HTTPException(422,'Allow at least 2.1 seconds per memory round')

class Mutation(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    reason:str=Field(min_length=3,max_length=500)
class UserChange(Mutation):
    active:bool=Field(strict=True)
    role:Literal['player','admin']
def user_info(user):return {'id':user.id,'username':user.username,'role':user.role,'active':user.active}
@router.get('/overview')
def overview(request:Request):
    from app.main import engine,User,GameSession,Challenge,AuditLog
    with Session(engine) as db:
        authorize(request,db)
        results=db.scalars(select(GameSession.result).where(GameSession.result.is_not(None))).all()
        return {'players':db.scalar(select(func.count()).select_from(User)),
                'activePlayers':db.scalar(select(func.count()).select_from(User).where(User.active.is_(True))),
                'sessions':db.scalar(select(func.count()).select_from(GameSession)),
                'validated':sum(json.loads(x).get('status')=='validated' for x in results),
                'flagged':sum(bool(json.loads(x).get('flagged')) for x in results),
                'invalidated':sum(json.loads(x).get('status')=='invalidated' for x in results),
                'challenges':db.scalar(select(func.count()).select_from(Challenge)),
                'auditEntries':db.scalar(select(func.count()).select_from(AuditLog))}
@router.get('/users')
def users(request:Request,q:str=Query('',max_length=100),offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
    from app.main import engine,User
    with Session(engine) as db:
        authorize(request,db);query=select(User).where(User.username.ilike('%'+q+'%'))
        total=db.scalar(select(func.count()).select_from(query.subquery()))
        return {'items':[user_info(x) for x in db.scalars(query.order_by(User.username).offset(offset).limit(limit)).all()],'total':total}
@router.post('/users/{uid}')
def update_user(uid:str,body:UserChange,request:Request):
    from app.main import engine,User,Login
    with Session(engine) as db:
        actor=authorize(request,db)
        # Lock all admin rows in a consistent order for the last-admin invariant.
        admins=db.scalars(select(User).where(User.role=='admin',User.active.is_(True)).order_by(User.id).with_for_update()).all()
        user=db.scalar(select(User).where(User.id==uid).with_for_update())
        if not user:raise HTTPException(404,'User not found')
        if user.id==actor.id and (not body.active or body.role!='admin'):raise HTTPException(409,'You cannot suspend or demote yourself')
        if user.role=='admin' and user.active and (not body.active or body.role!='admin') and len(admins)<=1:raise HTTPException(409,'At least one active admin must remain')
        before=user_info(user);user.active=body.active;user.role=body.role
        if not body.active:db.execute(delete(Login).where(Login.user_id==uid))
        audit(db,actor.username,'user.update',uid,{'before':before,'after':user_info(user),'reason':body.reason});db.commit()
        return user_info(user)

class GameChange(Mutation):
    name:str=Field(min_length=1,max_length=80)
    description:str=Field(min_length=1,max_length=500)
    instructions:str=Field(max_length=2000)
    available:bool=Field(strict=True)
    configuration:dict
@router.get('/games')
def get_games(request:Request):
    from app.main import engine
    with Session(engine) as db:
        authorize(request,db);return [{**g,'configuration':config(db,g['slug'])} for g in catalog(db)]
@router.post('/games/{game}')
def update_game(game:str,body:GameChange,request:Request):
    from app.main import engine,GameSettings
    if game not in DEFAULTS:raise HTTPException(404,'Game not found')
    validate_config(game,body.configuration)
    with Session(engine) as db:
        actor=authorize(request,db);before=next(g for g in catalog(db) if g['slug']==game);before['configuration']=config(db,game)
        settings=db.get(GameSettings,game)
        if settings is None:settings=GameSettings(slug=game);db.add(settings)
        settings.name=body.name;settings.description=body.description;settings.instructions=body.instructions;settings.available=body.available;settings.configuration=json.dumps(body.configuration)
        audit(db,actor.username,'game.update',game,{'before':before,'after':body.model_dump(exclude={'reason'}),'reason':body.reason});db.commit()
        return {'ok':True}

class ChallengeCreate(Mutation):
    game:Literal['memory-grid','pair-finder','quick-match','focus-finder','math-sprint']
    day:date
class ChallengeChange(Mutation):
    status:Literal['draft','published','closed']
def challenge_info(item):
    payload=json.loads(item.payload)
    return {'id':item.id,'game':item.game,'day':item.id.split(':')[0],'status':item.status,'configuration':payload.get('_config',DEFAULTS[item.game])}
@router.get('/challenges')
def challenges(request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
    from app.main import engine,Challenge
    with Session(engine) as db:
        authorize(request,db)
        return {'items':[challenge_info(x) for x in db.scalars(select(Challenge).order_by(Challenge.id.desc()).offset(offset).limit(limit)).all()],
                'total':db.scalar(select(func.count()).select_from(Challenge))}
@router.post('/challenges')
def create_challenge(body:ChallengeCreate,request:Request):
    from app.main import engine,Challenge
    from app.ranked import configured_content
    from sqlalchemy.exc import IntegrityError
    today=datetime.now(timezone.utc).date()
    if not today<=body.day<=today+timedelta(days=365):raise HTTPException(422,'Choose today or a date within the next year')
    with Session(engine) as db:
        actor=authorize(request,db);cid=f'{body.day.isoformat()}:{body.game}:v2'
        if db.get(Challenge,cid):raise HTTPException(409,'A challenge already exists for this game/date')
        item=Challenge(id=cid,game=body.game,payload=json.dumps(configured_content(db,body.game)),status='draft');db.add(item)
        audit(db,actor.username,'challenge.create',cid,{'reason':body.reason,'status':'draft'})
        try:db.commit()
        except IntegrityError:db.rollback();raise HTTPException(409,'A challenge already exists for this game/date')
        return challenge_info(item)
@router.post('/challenges/{cid}')
def change_challenge(cid:str,body:ChallengeChange,request:Request):
    from app.main import engine,Challenge
    with Session(engine) as db:
        actor=authorize(request,db);item=db.scalar(select(Challenge).where(Challenge.id==cid).with_for_update())
        if not item:raise HTTPException(404,'Challenge not found')
        if item.status=='closed' and body.status!='closed':raise HTTPException(409,'Closed challenges cannot reopen')
        if item.status=='published' and body.status=='draft':raise HTTPException(409,'Published challenges cannot return to draft')
        before=item.status;item.status=body.status
        audit(db,actor.username,'challenge.status',cid,{'before':before,'after':item.status,'reason':body.reason});db.commit()
        return challenge_info(item)

@router.get('/results')
def results(request:Request,q:str=Query('',max_length=100),status:Literal['all','validated','invalidated','flagged']='all',offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
    from app.main import engine,GameSession,User
    with Session(engine) as db:
        authorize(request,db)
        query=select(GameSession,User.username).join(User,User.id==GameSession.user_id).where(GameSession.result.is_not(None),User.username.ilike('%'+q+'%')).order_by(GameSession.issued.desc(),GameSession.id)
        # Result JSON is legacy text; filter the normalized status before paging.
        items=[]
        for item,name in db.execute(query).all():
            value=json.loads(item.result)
            if status=='all' or status=='flagged' and value.get('flagged') or value.get('status')==status:items.append({**value,'username':name})
        return {'items':items[offset:offset+limit],'total':len(items)}
@router.get('/results/{sid}')
def result_detail(sid:str,request:Request):
    from app.main import engine,GameSession,User
    with Session(engine) as db:
        authorize(request,db);item=db.get(GameSession,sid)
        if not item or not item.result:raise HTTPException(404,'Result not found')
        return {'result':json.loads(item.result),'username':db.get(User,item.user_id).username,'events':json.loads(item.payload).get('events',[])}
class Review(Mutation):
    action:Literal['flag','clear_flag','invalidate','restore']
@router.post('/results/{sid}')
def review(sid:str,body:Review,request:Request):
    from app.main import engine,GameSession
    with Session(engine) as db:
        actor=authorize(request,db);item=db.scalar(select(GameSession).where(GameSession.id==sid).with_for_update())
        if not item or not item.result:raise HTTPException(404,'Result not found')
        result=json.loads(item.result);before=dict(result)
        if body.action=='flag':result['flagged']=True;result['flagReason']=body.reason
        elif body.action=='clear_flag':result['flagged']=False;result.pop('flagReason',None)
        elif body.action=='invalidate':result['status']='invalidated';result['invalidationReason']=body.reason
        else:
            if result.get('status')!='invalidated':raise HTTPException(409,'Only invalidated results can be restored')
            result['status']='validated';result.pop('invalidationReason',None)
        result['reviewedBy']=actor.username;result['reviewReason']=body.reason
        item.result=json.dumps(result)
        audit(db,actor.username,'result.'+body.action,sid,{'before':before,'after':result,'reason':body.reason});db.commit()
        return result

@router.get('/audit')
def logs(request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
    from app.main import engine,AuditLog
    with Session(engine) as db:
        authorize(request,db)
        rows=db.scalars(select(AuditLog).order_by(AuditLog.created.desc(),AuditLog.id.desc()).offset(offset).limit(limit)).all()
        return {'items':[{'id':x.id,'actor':x.actor,'action':x.action,'target':x.target,'created':x.created,'details':json.loads(x.details)} for x in rows],'total':db.scalar(select(func.count()).select_from(AuditLog))}
