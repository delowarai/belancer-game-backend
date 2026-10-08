"""Authenticated account management and personal ranked activity."""
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app import main as m

router = APIRouter(prefix='/api')

class Rename(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3,max_length=24,pattern=r'^[a-zA-Z0-9_]+$')
    currentPassword: str = Field(min_length=8,max_length=128)

class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    currentPassword: str = Field(min_length=8,max_length=128)
    newPassword: str = Field(min_length=8,max_length=128)

def verify(user,password):
    if not hmac.compare_digest(m.password_hash(password,user.password.split(':')[0]),user.password):
        raise HTTPException(401,'Current password is incorrect')

@router.post('/account/username')
def rename(body:Rename,request:Request):
    with Session(m.engine) as db:
        user=m.current(request,db)
        user=db.scalar(select(m.User).where(m.User.id==user.id).with_for_update())
        verify(user,body.currentPassword)
        before=user.username
        user.username=body.username.lower()
        # The actor records the identity at the time of the change.
        db.add(m.AuditLog(id=m.secrets.token_hex(16),actor=before,action='account.rename',target=user.id,
                         details=json.dumps({'before':before,'after':user.username,'reason':'Player account update'}),created=m.now()))
        try: db.commit()
        except IntegrityError:
            db.rollback();raise HTTPException(409,'Username already exists')
        return {'username':user.username,'role':user.role}

@router.post('/account/password')
def change_password(body:PasswordChange,request:Request,response:Response):
    with Session(m.engine) as db:
        user=m.current(request,db)
        user=db.scalar(select(m.User).where(m.User.id==user.id).with_for_update())
        verify(user,body.currentPassword)
        if body.currentPassword==body.newPassword:raise HTTPException(422,'Choose a different new password')
        user.password=m.password_hash(body.newPassword)
        db.execute(delete(m.Login).where(m.Login.user_id==user.id))
        db.add(m.AuditLog(id=m.secrets.token_hex(16),actor=user.username,action='account.password',target=user.id,
                         details=json.dumps({'reason':'Player changed password; all sessions revoked'}),created=m.now()))
        db.commit()
    response.delete_cookie('session')
    return {'ok':True,'message':'Password changed. Sign in again on each device.'}

@router.post('/account/logout-all')
def logout_all(request:Request,response:Response):
    with Session(m.engine) as db:
        user=m.current(request,db)
        db.execute(delete(m.Login).where(m.Login.user_id==user.id));db.commit()
    response.delete_cookie('session')
    return {'ok':True}

@router.get('/progress')
def progress(request:Request,game:str='',offset:int=0,limit:int=20):
    if game and game not in [g['slug'] for g in m.GAMES]:raise HTTPException(422,'Unknown game')
    if offset<0 or not 1<=limit<=100:raise HTTPException(422,'Invalid pagination')
    with Session(m.engine) as db:
        user=m.current(request,db)
        items=db.scalars(select(m.GameSession).where(m.GameSession.user_id==user.id).order_by(m.GameSession.issued.desc(),m.GameSession.id)).all()
        submitted=[x for x in items if x.result]
        days=set();summaries=[]
        for g in m.GAMES:
            played=[x for x in submitted if x.game==g['slug']]
            valid=[(x,json.loads(x.result)) for x in played if json.loads(x.result).get('status')=='validated']
            correct=sum(r['correct'] for _,r in valid);errors=sum(r['errors'] for _,r in valid)
            for x,_ in valid:days.add(datetime.fromtimestamp(x.issued,timezone.utc).date().isoformat())
            summaries.append({'game':g['slug'],'attempts':sum(x.game==g['slug'] for x in items),
                              'submitted':len(played),'validated':len(valid),
                              'completed':sum(bool(r['completed']) for _,r in valid),
                              'accuracy':round(100*correct/(correct+errors),1) if correct+errors else None})
        selected=[x for x in submitted if not game or x.game==game]
        history=[]
        for x in selected[offset:offset+limit]:
            r=json.loads(x.result)
            # Expose player-facing fields, never moderation notes or admin identities.
            history.append({k:r[k] for k in ('session','game','challenge','version','score','correct','errors','accuracy','duration','completed','status','attempt') if k in r})
            history[-1]['startedAt']=x.issued
        today=datetime.now(timezone.utc).date()
        from datetime import timedelta
        cursor=today if today.isoformat() in days else today-timedelta(days=1)
        streak=0
        while cursor.isoformat() in days:streak+=1;cursor-=timedelta(days=1)
        return {'attempts':len(items),'submitted':len(submitted),'activeDays':len(days),'streakDays':streak,
                'games':summaries,'history':{'items':history,'total':len(selected),'offset':offset,'limit':limit}}
