import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, String, Text, Integer, select, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Session

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./belancer.db')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = 'users'
    id = Column(String, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    role = Column(String, default='player')
class Login(Base):
    __tablename__ = 'logins'
    token = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    expires = Column(Integer, nullable=False)
class GameSession(Base):
    __tablename__ = 'game_sessions'
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    game = Column(String, nullable=False)
    challenge = Column(String, nullable=False)
    attempt = Column(Integer, nullable=False)
    issued = Column(Integer, nullable=False)
    payload = Column(Text, nullable=False)
    result = Column(Text)
    __table_args__ = (UniqueConstraint('user_id', 'challenge', 'attempt'),)
GAMES = [
    {'slug':'memory-grid','name':'Memory Grid','category':'Memory','description':'Remember the highlighted cells, then select them.'},
    {'slug':'pair-finder','name':'Pair Finder','category':'Memory','description':'Reveal cards and find all matching pairs.'},
    {'slug':'quick-match','name':'Quick Match','category':'Speed','description':'Decide whether two symbols match.'},
    {'slug':'focus-finder','name':'Focus Finder','category':'Attention','description':'Find the different symbol among distractors.'},
    {'slug':'math-sprint','name':'Math Sprint','category':'Math','description':'Solve ten arithmetic questions accurately.'},
]
app = FastAPI(title='Belancer Game API', version='1.0.0')
def now(): return int(datetime.now(timezone.utc).timestamp())
def challenge(game): return datetime.now(timezone.utc).strftime('%Y-%m-%d') + ':' + game + ':v1'
def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
def current(req, db):
    token = req.cookies.get('session', '')
    login = db.get(Login, hashlib.sha256(token.encode()).hexdigest())
    if not login or login.expires < now(): raise HTTPException(401, 'Please sign in')
    user = db.get(User, login.user_id)
    if not user: raise HTTPException(401, 'Please sign in')
    return user
@app.middleware('http')
async def same_origin(request: Request, call_next):
    if request.method in ('POST','PUT','DELETE','PATCH'):
        origin = request.headers.get('origin')
        expected = os.getenv('PUBLIC_ORIGIN', 'http://localhost:5173')
        if origin and origin != expected: return Response('Origin rejected', status_code=403)
        if request.headers.get('x-requested-with') != 'Belancer': return Response('CSRF header required', status_code=403)
    return await call_next(request)
class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=24, pattern=r'^[a-zA-Z0-9_]+$')
    password: str = Field(min_length=8, max_length=128)
def set_login(user, db, response):
    token = secrets.token_urlsafe(32)
    db.add(Login(token=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, expires=now()+604800))
    db.commit()
    response.set_cookie('session', token, httponly=True, secure=os.getenv('COOKIE_SECURE','false')=='true', samesite='strict', max_age=604800)
    return {'username':user.username,'role':user.role}
@app.get('/api/health')
def health():
    with Session(engine) as db: db.execute(select(User.id).limit(1))
    return {'status':'ok'}
@app.post('/api/auth/register')
def register(body: Credentials, response: Response):
    with Session(engine) as db:
        if db.scalar(select(User).where(User.username==body.username.lower())): raise HTTPException(409,'Username already exists')
        user = User(id=secrets.token_hex(16),username=body.username.lower(),password=password_hash(body.password))
        db.add(user)
        return set_login(user,db,response)
@app.post('/api/auth/login')
def login(body: Credentials, response: Response):
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.username==body.username.lower()))
        if not user or not hmac.compare_digest(password_hash(body.password,user.password.split(':')[0]),user.password): raise HTTPException(401,'Invalid credentials')
        return set_login(user,db,response)
@app.post('/api/auth/logout')
def logout(request: Request,response: Response):
    with Session(engine) as db:
        item = db.get(Login,hashlib.sha256(request.cookies.get('session','').encode()).hexdigest())
        if item: db.delete(item); db.commit()
    response.delete_cookie('session')
    return {'ok':True}
@app.get('/api/auth/me')
def me(request: Request):
    with Session(engine) as db:
        user=current(request,db)
        return {'username':user.username,'role':user.role}
@app.get('/api/games')
def games(): return GAMES
@app.get('/api/challenges')
def challenges(): return [{**g,'id':challenge(g['slug']),'attemptLimit':3,'version':'v1','timezone':'UTC'} for g in GAMES]
def generate(game):
    rand=secrets.SystemRandom()
    if game=='memory-grid': return {'rounds':[rand.sample(range(16),3) for _ in range(10)]}
    if game=='pair-finder':
        cards=list(range(8))*2; rand.shuffle(cards); return {'cards':cards}
    if game=='quick-match': return {'rounds':[[rand.randrange(4),rand.randrange(4)] for _ in range(10)]}
    if game=='focus-finder': return {'rounds':[rand.randrange(16) for _ in range(10)]}
    return {'rounds':[[rand.randrange(1,20),rand.randrange(1,20)] for _ in range(10)]}
class Start(BaseModel):
    game: str
@app.post('/api/sessions')
def start(body: Start,request: Request):
    if body.game not in [g['slug'] for g in GAMES]: raise HTTPException(404,'Unknown game')
    with Session(engine) as db:
        user=current(request,db); cid=challenge(body.game)
        # Lock the player row on PostgreSQL so concurrent requests cannot bypass limits.
        db.execute(select(User).where(User.id==user.id).with_for_update()).scalar_one()
        count=len(db.scalars(select(GameSession).where(GameSession.user_id==user.id,GameSession.challenge==cid)).all())
        if count>=3: raise HTTPException(409,'All three attempts have been used today')
        payload=generate(body.game)
        item=GameSession(id=secrets.token_hex(16),user_id=user.id,game=body.game,challenge=cid,attempt=count+1,issued=now(),payload=json.dumps(payload))
        db.add(item); db.commit()
        return {'id':item.id,'game':item.game,'challenge':cid,'attempt':item.attempt,'version':'v1','payload':payload,'expiresIn':600}
class Submission(BaseModel):
    answers: list = Field(max_length=100)
    duration: int = Field(ge=1,le=600000)
def evaluate(game,payload,answers):
    if game=='pair-finder':
        if any(type(x)!=int or x<0 or x>15 for x in answers): raise HTTPException(422,'Invalid card')
        found=set(); previous=None; moves=0
        for x in answers:
            if x in found or x==previous: raise HTTPException(422,'Illegal reveal')
            if previous is None: previous=x
            else:
                moves+=1
                if payload['cards'][previous]==payload['cards'][x]: found.update([previous,x])
                previous=None
        correct=len(found)//2
        return correct*100,correct,max(0,moves-correct),correct==8
    if len(answers)!=10: raise HTTPException(422,'Exactly ten rounds required')
    correct=0; errors=0
    for expected,answer in zip(payload['rounds'],answers):
        if game=='memory-grid':
            if not isinstance(answer,list) or len(answer)!=3 or any(type(x)!=int or x<0 or x>15 for x in answer) or len(set(answer))!=3: raise HTTPException(422,'Select three unique cells')
            hit=len(set(expected)&set(answer)); correct+=hit; errors+=3-hit
        else:
            if type(answer)!=int: raise HTTPException(422,'Invalid answer')
            target=int(expected[0]==expected[1]) if game=='quick-match' else expected if game=='focus-finder' else sum(expected)
            correct+=int(answer==target); errors+=int(answer!=target)
    return correct*(10 if game=='memory-grid' else 100),correct,errors,True
@app.post('/api/sessions/{sid}/submit')
def submit(sid: str,body: Submission,request: Request):
    with Session(engine) as db:
        user=current(request,db)
        item=db.scalar(select(GameSession).where(GameSession.id==sid).with_for_update())
        if not item or item.user_id!=user.id: raise HTTPException(404,'Session not found')
        if item.result: return json.loads(item.result)
        elapsed=now()-item.issued
        if elapsed>600: raise HTTPException(410,'Session expired')
        if body.duration> (elapsed+2)*1000: raise HTTPException(422,'Invalid duration')
        if item.game=='memory-grid' and elapsed<20: raise HTTPException(422,'Session completed too quickly')
        score,correct,errors,completed=evaluate(item.game,json.loads(item.payload),body.answers)
        result={'session':sid,'game':item.game,'challenge':item.challenge,'score':score,'correct':correct,'errors':errors,'accuracy':round(100*correct/max(1,correct+errors),1),'duration':max(1,elapsed*1000),'completed':completed,'status':'validated','submitted':now(),'attempt':item.attempt}
        item.result=json.dumps(result); db.commit(); return result
@app.get('/api/history')
def history(request: Request):
    with Session(engine) as db:
        user=current(request,db)
        return [json.loads(x.result) for x in db.scalars(select(GameSession).where(GameSession.user_id==user.id,GameSession.result.is_not(None)).order_by(GameSession.issued.desc())).all()]
@app.get('/api/leaderboards/{game}')
def leaderboard(game: str):
    with Session(engine) as db:
        rows=db.execute(select(GameSession,User.username).join(User,User.id==GameSession.user_id).where(GameSession.challenge==challenge(game),GameSession.result.is_not(None))).all()
        best={}
        def key(r):
            if game=='pair-finder': return (-int(r['completed']),r['duration'] if r['completed'] else -r['correct'],r['correct']+r['errors'],r['submitted'])
            if game=='memory-grid': return (-r['correct'],r['errors'],r['duration'],r['submitted'])
            return (-r['score'],-r['accuracy'],r['submitted'])
        for item,name in rows:
            r={**json.loads(item.result),'username':name}
            if name not in best or key(r)<key(best[name]): best[name]=r
        return [{**r,'rank':i+1} for i,r in enumerate(sorted(best.values(),key=key))]
@app.get('/api/admin/overview')
def admin(request: Request):
    with Session(engine) as db:
        if current(request,db).role!='admin': raise HTTPException(403,'Admin role required')
        users=db.scalars(select(User)).all(); sessions=db.scalars(select(GameSession)).all()
        return {'players':len(users),'sessions':len(sessions),'validated':sum(bool(s.result) for s in sessions)}
