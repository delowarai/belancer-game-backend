"""Public period standings, separated by frozen ranked configuration."""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import main as m
from app.admin import DEFAULTS, config

router=APIRouter(prefix='/api')

def today():return datetime.now(timezone.utc).date()

def profile(configuration):
    return hashlib.sha256(json.dumps(configuration,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]

def rank_key(game,r):
    if game=='pair-finder':return (-int(r['completed']),r['duration'] if r['completed'] else -r['correct'],r['correct']+r['errors'],r['submitted'])
    if game=='memory-grid':return (-r.get('correctRounds',r['correct']),r['errors'],r['duration'],r['submitted'])
    return (-r['score'],-r['accuracy'],r['submitted'])

@router.get('/standings/{game}')
def standings(game:str,request:Request,period:str='daily',day:str='',rules:str='',offset:int=0,limit:int=20):
    if game not in DEFAULTS:raise HTTPException(404,'Unknown game')
    if period not in ('daily','weekly','all-time'):raise HTTPException(422,'Unknown period')
    if offset<0 or not 1<=limit<=100:raise HTTPException(422,'Invalid pagination')
    try:anchor=date.fromisoformat(day) if day else today()
    except ValueError:raise HTTPException(422,'Use a YYYY-MM-DD date')
    if anchor>today():raise HTTPException(422,'Future standings are unavailable')
    start=anchor if period=='daily' else anchor-timedelta(days=anchor.weekday()) if period=='weekly' else None
    end=start+timedelta(days=7) if period=='weekly' else anchor+timedelta(days=1)
    with Session(m.engine) as db:
        try:viewer=m.current(request,db).id
        except HTTPException:viewer=None
        current=db.get(m.Challenge,f'{anchor}:{game}:v2')
        selected_config=json.loads(current.payload).get('_config',DEFAULTS[game]) if current else config(db,game)
        profiles={profile(selected_config):selected_config}
        candidates=[]
        rows=db.execute(select(m.GameSession,m.User).join(m.User,m.User.id==m.GameSession.user_id).where(m.GameSession.game==game,m.GameSession.result.is_not(None),m.User.active.is_(True))).all()
        challenges={x.id:json.loads(x.payload) for x in db.scalars(select(m.Challenge).where(m.Challenge.game==game)).all()}
        for item,user in rows:
            parts=item.challenge.split(':')
            if len(parts)!=3 or parts[1]!=game or parts[2]!='v2':continue
            try:challenge_day=date.fromisoformat(parts[0])
            except ValueError:continue
            if challenge_day>today() or challenge_day>=end or (start and challenge_day<start):continue
            result=json.loads(item.result)
            if result.get('status')!='validated' or result.get('version','v2')!='v2':continue
            # The session snapshot is authoritative even if a challenge record is absent.
            state=json.loads(item.payload)
            configuration=state.get('content',{}).get('_config',challenges.get(item.challenge,{}).get('_config',DEFAULTS[game]))
            pid=profile(configuration);profiles[pid]=configuration
            public={k:result[k] for k in ('score','accuracy','duration','correct','errors','correctRounds','completed','submitted','attempt') if k in result}
            candidates.append((pid,user.id,{**public,'username':user.username,'challenge':item.challenge}))
        chosen=rules or profile(selected_config)
        if chosen not in profiles:raise HTTPException(422,'Unknown rules profile for this period')
        best={}
        for pid,uid,r in candidates:
            if pid!=chosen:continue
            if uid not in best or rank_key(game,r)<rank_key(game,best[uid]):best[uid]=r
        ordered=sorted(best.items(),key=lambda pair:(rank_key(game,pair[1]),pair[0]))
        board=[{**r,'rank':i+1,'isYou':uid==viewer} for i,(uid,r) in enumerate(ordered)]
        mine=next((r for r in board if r['isYou']),None)
        return {'period':period,'day':str(anchor),'start':str(start) if start else None,'endExclusive':str(end),
                'version':'v2','rules':chosen,'configuration':profiles[chosen],
                'profiles':[{'id':key,'configuration':value} for key,value in sorted(profiles.items())],
                'items':board[offset:offset+limit],'total':len(board),'offset':offset,'limit':limit,'yourRank':mine['rank'] if mine else None}
