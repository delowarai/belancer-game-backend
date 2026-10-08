"""Version 2: server-owned rounds, reveal events, deadlines and replay-safe actions."""
import json
import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter()
LIMITS = {'memory-grid':180000,'pair-finder':180000,'quick-match':60000,'focus-finder':120000,'math-sprint':60000}
def milliseconds(): return int(time.time()*1000)

def content(game,configuration=None):
    from app.admin import DEFAULTS
    configuration=configuration or DEFAULTS[game]
    rounds_count=configuration.get('rounds',0)
    random=secrets.SystemRandom()
    if game=='pair-finder':
        cards=list(range(8))*2; random.shuffle(cards); return {'cards':cards}
    if game=='memory-grid': return {'rounds':[random.sample(range(16),configuration['cells']) for _ in range(rounds_count)]}
    if game=='focus-finder': return {'rounds':[random.randrange(16) for _ in range(rounds_count)]}
    if game=='math-sprint': return {'rounds':[[random.randrange(1,20),random.randrange(1,20)] for _ in range(rounds_count)]}
    # Exactly half the questions match; order/content are shared by every player.
    rounds=[]
    for i in range(rounds_count):
        a=random.randrange(4)
        b=a if i%2==0 else (a+random.randrange(1,4))%4
        rounds.append([a,b])
    random.shuffle(rounds)
    return {'rounds':rounds}

def configured_content(db,game):
    from app.admin import config,catalog
    configuration=config(db,game)
    instructions=next(x['instructions'] for x in catalog(db) if x['slug']==game)
    return {**content(game,configuration),'_config':configuration,'_instructions':instructions}

def create_state(db,game,cid):
    from app.main import Challenge
    shared=db.scalar(select(Challenge).where(Challenge.id==cid).with_for_update())
    if shared is None:
        # A savepoint recovers a concurrent unique-key collision without losing
        # the player's transaction/attempt-allocation lock.
        try:
            with db.begin_nested():
                shared=Challenge(id=cid,game=game,payload=json.dumps(configured_content(db,game)),status='published')
                db.add(shared); db.flush()
        except IntegrityError:
            shared=db.scalar(select(Challenge).where(Challenge.id==cid).with_for_update())
    if shared.status!='published':raise HTTPException(409,'This challenge is '+shared.status)
    timestamp=milliseconds()
    puzzle=json.loads(shared.payload)
    return {'version':'v2','content':puzzle,'started':timestamp,
            'deadline':timestamp+puzzle.get('_config',{}).get('durationSeconds',LIMITS[game]//1000)*1000,'roundIssued':timestamp,'round':0,
            'correct':0,'errors':0,'correctRounds':0,'responseTime':0,
            'found':[],'open':[],'availableAt':timestamp,'events':[]}

def public_state(item,state,timestamp=None):
    timestamp=milliseconds() if timestamp is None else timestamp
    result=json.loads(item.result) if item.result else None
    prompt=None
    if not result and timestamp<state['deadline']:
        if item.game=='pair-finder': prompt={'found':state['found'],'open':state['open'],'availableAt':state['availableAt']}
        elif state['round']<len(state['content']['rounds']):
            question=state['content']['rounds'][state['round']]
            show_until=state['roundIssued']+2000 if item.game=='memory-grid' else None
            prompt={'question':[] if show_until and timestamp>=show_until else question,'showUntil':show_until,'cells':len(question) if item.game=='memory-grid' else None}
    return {'id':item.id,'game':item.game,'challenge':item.challenge,'attempt':item.attempt,
            'version':'v2','serverNow':timestamp,'deadline':state['deadline'],
            'round':state['round'],'totalRounds':len(state['content'].get('rounds',[])),
            'sequence':len(state['events']),'prompt':prompt,'result':result}

class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sequence: int = Field(ge=0,le=1000,strict=True)
    answer: Any

def owned(db,sid,request):
    from app.main import GameSession,current
    user=current(request,db)
    item=db.scalar(select(GameSession).where(GameSession.id==sid).with_for_update())
    if not item or item.user_id!=user.id: raise HTTPException(404,'Session not found')
    state=json.loads(item.payload)
    if state.get('version')!='v2': raise HTTPException(409,'Legacy session; start a new challenge')
    return item,state

def make_result(item,state,timestamp,completed):
    total=state['correct']+state['errors']
    elapsed=min(timestamp,state['deadline'])-state['started']
    # Memory ranks answer response time excluding the compulsory pattern display.
    duration=state['responseTime'] if item.game=='memory-grid' else elapsed
    return {'session':item.id,'game':item.game,'challenge':item.challenge,'version':'v2',
            'score':state['correct']*(10 if item.game=='memory-grid' else 100),
            'correct':state['correct'],'errors':state['errors'],'correctRounds':state['correctRounds'],
            'accuracy':round(100*state['correct']/max(1,total),1),'duration':max(1,duration),
            'playDuration':max(1,elapsed),'completed':completed,'status':'validated',
            'submitted':timestamp,'attempt':item.attempt}

def save(db,item,old,state,result=None):
    from app.main import GameSession
    values={'payload':json.dumps(state)}
    if result is not None: values['result']=json.dumps(result)
    # The comparison also prevents stale concurrent writes under local SQLite.
    changed=db.execute(update(GameSession).where(GameSession.id==item.id,GameSession.payload==old,GameSession.result.is_(None)).values(**values),execution_options={'synchronize_session':False})
    if changed.rowcount!=1:
        db.rollback(); raise HTTPException(409,'Session changed; reload the current session')
    db.commit(); db.refresh(item)

@router.get('/api/sessions/{sid}')
def get_session(sid:str,request:Request):
    from app.main import engine
    with Session(engine) as db:
        item,state=owned(db,sid,request)
        return public_state(item,state)

@router.post('/api/sessions/{sid}/actions')
def action(sid:str,body:Action,request:Request):
    from app.main import engine
    with Session(engine) as db:
        item,state=owned(db,sid,request); old=item.payload; timestamp=milliseconds()
        seq=body.sequence
        if seq<len(state['events']):
            previous=state['events'][seq]
            if json.dumps(previous['answer'])!=json.dumps(body.answer): raise HTTPException(409,'Retry answer differs from original action')
            # Replay is a read of the latest state, never a rewind of the clock
            # or a second opportunity to view an expired memory pattern.
            response=public_state(item,state,timestamp)
            response['feedback']=previous['feedback']
            return response
        if seq!=len(state['events']): raise HTTPException(409,'Out-of-order action')
        if item.result: raise HTTPException(409,'Session is complete')
        if timestamp>=state['deadline']: raise HTTPException(410,'Time is up; finish this session')
        if len(state['events'])>=1000: raise HTTPException(409,'Action limit reached')
        answer=body.answer; feedback={}; completed=False
        if item.game=='pair-finder':
            if type(answer)!=int or not 0<=answer<16: raise HTTPException(422,'Invalid card')
            if timestamp<state['availableAt']: raise HTTPException(409,'Wait for the cards to hide')
            if answer in state['found'] or any(x['index']==answer for x in state['open']): raise HTTPException(422,'Card already revealed')
            revealed={'index':answer,'symbol':state['content']['cards'][answer]}
            state['open'].append(revealed); feedback={'revealed':revealed}
            if len(state['open'])==2:
                matched=state['open'][0]['symbol']==state['open'][1]['symbol']
                feedback['matched']=matched
                if matched:
                    state['found'].extend(x['index'] for x in state['open']); state['correct']+=1
                else: state['errors']+=1
                state['open']=[]; state['availableAt']=timestamp+(350 if matched else 850)
            completed=len(state['found'])==16
        else:
            expected=state['content']['rounds'][state['round']]
            if item.game=='memory-grid':
                if timestamp<state['roundIssued']+2000: raise HTTPException(409,'Pattern is still visible')
                cells=len(expected)
                if not isinstance(answer,list) or len(answer)!=cells or any(type(x)!=int or not 0<=x<16 for x in answer) or len(set(answer))!=cells: raise HTTPException(422,f'Select {cells} unique cells')
                correct=len(set(expected)&set(answer)); errors=cells-correct
                state['correctRounds']+=int(correct==cells)
                state['responseTime']+=timestamp-state['roundIssued']-2000
            else:
                if type(answer)!=int: raise HTTPException(422,'Invalid answer')
                if item.game=='quick-match' and answer not in (0,1): raise HTTPException(422,'Answer must be Same or Different')
                if item.game=='focus-finder' and not 0<=answer<16: raise HTTPException(422,'Invalid cell')
                target=int(expected[0]==expected[1]) if item.game=='quick-match' else sum(expected) if item.game=='math-sprint' else expected
                correct=int(answer==target); errors=1-correct
            state['correct']+=correct; state['errors']+=errors
            feedback={'correct':correct,'errors':errors}
            state['round']+=1; state['roundIssued']=timestamp
            completed=state['round']==len(state['content']['rounds'])
        if completed: item.result=json.dumps(make_result(item,state,timestamp,True))
        response=public_state(item,state,timestamp); response['feedback']=feedback
        state['events'].append({'answer':answer,'at':timestamp,'feedback':feedback})
        response['sequence']=len(state['events'])
        result=json.loads(item.result) if completed else None
        # Restore ORM result before the atomic persisted update (avoid autoflush).
        if completed: item.result=None
        save(db,item,old,state,result)
        return response

class Finish(BaseModel):
    model_config=ConfigDict(extra='forbid')

@router.post('/api/sessions/{sid}/finish')
def finish(sid:str,body:Finish,request:Request):
    from app.main import engine
    with Session(engine) as db:
        item,state=owned(db,sid,request)
        if item.result: return public_state(item,state)
        timestamp=milliseconds()
        if timestamp<state['deadline']: raise HTTPException(409,'Challenge time has not ended')
        old=item.payload
        result=make_result(item,state,timestamp,False)
        save(db,item,old,state,result)
        return public_state(item,state,timestamp)
