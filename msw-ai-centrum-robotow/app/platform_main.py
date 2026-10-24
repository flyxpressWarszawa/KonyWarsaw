"""MSW AI Platform — standalone user accounts, technical agents and marketplace.
This module extends the independent FastAPI app. No WordPress integration.
Customer routes are unavailable until persistent DATABASE_URL is configured,
except explicitly allowed local/staging tests.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import smtplib
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import httpx
from fastapi import Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from .main import app, Base, SessionLocal, db_session
from .platform_core import DOMAINS, ALLOWED_ROLES, classify, benchmark, technical_domain

ROOT=Path(__file__).resolve().parent
COOKIE="msw_session"
CSRF_COOKIE="msw_csrf"
BASE_URL=os.getenv("MSW_PUBLIC_BASE_URL","https://msw-ai.com").rstrip("/")
PRIMARY=os.getenv("MSW_PORTAL_PRIMARY","")=="1"
DURABLE=bool(os.getenv("DATABASE_URL")) and os.getenv("DATABASE_URL","").startswith(("postgres://","postgresql://","postgresql+psycopg://"))
ALLOW_DEV=os.getenv("MSW_ALLOW_EPHEMERAL_ACCOUNTS")=="1"
BOTS_PER_ACCOUNT=5
MAX_RUNS_DAY=20
REG_WINDOW=3600
_ratelimit=defaultdict(list)
def now():return datetime.now(timezone.utc)
def utc(x):
    if not x:return now()
    return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)
def digest(s):return hashlib.sha256(s.encode("utf-8")).hexdigest()
def safe_db():
    if not (DURABLE or ALLOW_DEV):
        raise HTTPException(503,detail="Rejestracja i konta są wstrzymane do podłączenia trwałej bazy PostgreSQL. Dane na darmowym dysku Render mogłyby zniknąć.")
def limit(request:Request, action:str, maximum:int):
    ip=str(request.client.host if request.client else "anonymous")
    key=(action,digest(os.getenv("MSW_RATE_SALT","local-rate-v1")+ip))
    t=time.time()
    _ratelimit[key]=[x for x in _ratelimit[key] if x>=t-REG_WINDOW]
    if len(_ratelimit[key])>=maximum:raise HTTPException(429,"Limit prób. Spróbuj później.")
    _ratelimit[key].append(t)
    if len(_ratelimit)>6000:_ratelimit.clear()
def origin_guard(request:Request):
    origin=request.headers.get("origin")
    allowed={str(request.base_url).rstrip("/"),BASE_URL,"https://msw-ai-gielda-technologii.onrender.com"}
    if origin and origin not in allowed:raise HTTPException(403,"Origin niedozwolony")
def password_hash(password:str,salt:str):return hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),310000).hex()
def validate_password(password):
    if not 12<=len(password)<=128 or password.lower() in {"password123456","qwerty123456","123456789012"}:
        raise HTTPException(422,"Hasło musi mieć 12–128 znaków i nie może być popularnym hasłem")
def auth_cookie(response:Response, token:str, csrf:str):
    secure=os.getenv("MSW_ALLOW_HTTP_COOKIES")!="1"
    response.set_cookie(COOKIE,token,max_age=604800,httponly=True,secure=secure,samesite="lax",path="/")
    response.set_cookie(CSRF_COOKIE,csrf,max_age=604800,httponly=False,secure=secure,samesite="strict",path="/")
def clear_cookie(response:Response):
    response.delete_cookie(COOKIE,path="/")
    response.delete_cookie(CSRF_COOKIE,path="/")
class Customer(Base):
    __tablename__="portal_customers"
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    email:Mapped[str]=mapped_column(String(190),unique=True,index=True)
    name:Mapped[str]=mapped_column(String(90))
    salt:Mapped[str]=mapped_column(String(32))
    secret:Mapped[str]=mapped_column(String(64))
    verified:Mapped[bool]=mapped_column(Boolean,default=False)
    verify_digest:Mapped[str|None]=mapped_column(String(64),nullable=True)
    verify_expires:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    created:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class CustomerSession(Base):
    __tablename__="portal_sessions"
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    customer_id:Mapped[str]=mapped_column(ForeignKey("portal_customers.id"),index=True)
    csrf_digest:Mapped[str]=mapped_column(String(64))
    expires:Mapped[datetime]=mapped_column(DateTime(timezone=True))
class TechAgent(Base):
    __tablename__="portal_agents"
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    owner_id:Mapped[str]=mapped_column(ForeignKey("portal_customers.id"),index=True)
    name:Mapped[str]=mapped_column(String(90))
    summary:Mapped[str]=mapped_column(String(450),default="")
    domain:Mapped[str]=mapped_column(String(35))
    role:Mapped[str]=mapped_column(String(35))
    engine:Mapped[str]=mapped_column(String(20),default="local")
    public:Mapped[bool]=mapped_column(Boolean,default=False)
    runs:Mapped[int]=mapped_column(Integer,default=0)
    created:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class BuildRequest(Base):
    __tablename__="portal_build_requests"
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    owner_id:Mapped[str]=mapped_column(ForeignKey("portal_customers.id"),index=True)
    title:Mapped[str]=mapped_column(String(120))
    domain:Mapped[str]=mapped_column(String(35))
    details:Mapped[str]=mapped_column(Text)
    status:Mapped[str]=mapped_column(String(30),default="queued_for_quote")
    created:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class AgentRun(Base):
    __tablename__="portal_agent_runs"
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    agent_id:Mapped[str]=mapped_column(ForeignKey("portal_agents.id"),index=True)
    customer_id:Mapped[str]=mapped_column(ForeignKey("portal_customers.id"),index=True)
    latency_ms:Mapped[int]=mapped_column(Integer)
    success:Mapped[bool]=mapped_column(Boolean)
    provider:Mapped[str]=mapped_column(String(20))
    created:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class ArenaRecord(Base):
    __tablename__="portal_arena_records"
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    owner_id:Mapped[str]=mapped_column(ForeignKey("portal_customers.id"),index=True)
    agent_id:Mapped[str]=mapped_column(ForeignKey("portal_agents.id"),index=True)
    passed:Mapped[int]=mapped_column(Integer)
    total:Mapped[int]=mapped_column(Integer)
    created:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class RegisterInput(BaseModel):
    email:str=Field(min_length=5,max_length=190)
    password:str=Field(min_length=12,max_length=128)
    name:str=Field(min_length=2,max_length=90)
    @field_validator("email")
    @classmethod
    def check_email(cls,v):
        s=v.lower().strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,40}",s):raise ValueError("Niepoprawny e-mail")
        return s
class LoginInput(BaseModel):
    email:str=Field(min_length=5,max_length=190)
    password:str
class AgentInput(BaseModel):
    name:str=Field(min_length=3,max_length=90)
    summary:str=Field(default="",max_length=450)
    domain:str
    role:str=Field(default="diagnosta")
    engine:str=Field(default="local")
class AgentEdit(BaseModel):
    public:bool
class RunInput(BaseModel):
    question:str=Field(min_length=3,max_length=2500)
    allow_paid_model:bool=False
class BuildInput(BaseModel):
    title:str=Field(min_length=5,max_length=120)
    domain:str
    details:str=Field(min_length=15,max_length=2400)
def send_verification(address,token):
    host=os.getenv("MSW_SMTP_HOST","")
    if not host:return False
    msg=EmailMessage()
    msg["Subject"]="MSW AI — potwierdzenie adresu e-mail"
    msg["From"]=os.getenv("MSW_SMTP_FROM","no-reply@msw-ai.com")
    msg["To"]=address
    msg.set_content("Potwierdź konto MSW AI:\n"+BASE_URL+"/api/portal/verify?token="+token+"\n\nLink wygasa po 24 godzinach.")
    with smtplib.SMTP(host,int(os.getenv("MSW_SMTP_PORT","587")),timeout=9) as smtp:
        smtp.starttls()
        if os.getenv("MSW_SMTP_USER"):smtp.login(os.environ["MSW_SMTP_USER"],os.environ["MSW_SMTP_PASSWORD"])
        smtp.send_message(msg)
    return True
def login_user(response,db,user):
    token=secrets.token_urlsafe(40)
    csrf=secrets.token_urlsafe(32)
    db.add(CustomerSession(id=digest(token),customer_id=user.id,csrf_digest=digest(csrf),expires=now()+timedelta(days=7)))
    db.commit()
    auth_cookie(response,token,csrf)
    return {"ok":True,"user":{"name":user.name,"email":user.email,"verified":user.verified},"csrf":csrf}
def current(request:Request,db:Session=Depends(db_session)):
    token=request.cookies.get(COOKIE,"")
    if not token:raise HTTPException(401,"Zaloguj się")
    sess=db.get(CustomerSession,digest(token))
    if not sess or utc(sess.expires)<=now():raise HTTPException(401,"Sesja wygasła")
    user=db.get(Customer,sess.customer_id)
    if not user:raise HTTPException(401,"Konto nie istnieje")
    return (user,sess)
def csrf(request:Request,identity=Depends(current)):
    provided=request.headers.get("x-msw-csrf","")
    stored=request.cookies.get(CSRF_COOKIE,"")
    if not provided or not stored or not hmac.compare_digest(provided,stored) or not hmac.compare_digest(digest(provided),identity[1].csrf_digest):
        raise HTTPException(403,"Niepoprawny token CSRF")
    origin_guard(request)
    return identity
def as_agent(x:TechAgent):
    return {"id":x.id,"name":x.name,"summary":x.summary,"domain":x.domain,"role":x.role,"engine":x.engine,"public":x.public,"runs":x.runs,"created":x.created.isoformat(),"model_connected":x.engine=="openai" and bool(os.getenv("OPENAI_API_KEY"))}
@app.middleware("http")
async def portal_root(request:Request,call_next):
    if PRIMARY and request.url.path=="/":
        response=FileResponse(ROOT/"static"/"portal.html",media_type="text/html")
    else:
        response=await call_next(request)
    if request.url.path.startswith(("/api/portal/","/platform")) or (PRIMARY and request.url.path=="/"):
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/portal/") or request.url.path=="/":
            response.headers["Cache-Control"]="no-store"
        if request.url.scheme=="https":
            response.headers["Strict-Transport-Security"]="max-age=31536000"
    return response
@app.get("/platform/")
@app.get("/platform")
def portal_index():return FileResponse(ROOT/"static"/"portal.html",media_type="text/html",headers={"Cache-Control":"no-store"})
@app.get("/platform/{asset_name}")
def portal_assets(asset_name:str):
    mapping={"portal.css":"text/css; charset=utf-8","portal.js":"text/javascript; charset=utf-8"}
    if asset_name not in mapping:raise HTTPException(404)
    return FileResponse(ROOT/"static"/asset_name,media_type=mapping[asset_name],headers={"X-Content-Type-Options":"nosniff","Cache-Control":"public,max-age=120"})
@app.get("/api/portal/status")
def portal_status(db:Session=Depends(db_session)):
    try:
        db.execute(select(func.count()).select_from(Customer)).scalar()
        result="connected"
        bots=db.scalar(select(func.count()).select_from(TechAgent)) or 0
        customers=db.scalar(select(func.count()).select_from(Customer)) or 0
        public=db.scalar(select(func.count()).select_from(TechAgent).where(TechAgent.public==True)) or 0
        orders=db.scalar(select(func.count()).select_from(BuildRequest)) or 0
    except Exception:
        result="unavailable";bots=customers=public=orders=0
    return {"ok":result=="connected","database":result,"durable":DURABLE,"signup_ready":(DURABLE or ALLOW_DEV) and result=="connected",
            "app":"msw-ai.com independent technical AI portal","wordpress":False,
            "bot_instances":bots,"customers":customers,"public_offers":public,"build_requests":orders,
            "ai_provider_connected":bool(os.getenv("OPENAI_API_KEY")) and os.getenv("MSW_ALLOW_PAID_INFERENCE")=="1",
            "local_engine":"ready","model_training":"not_configured",
            "safety":"no device commands, no spam crawlers, no speculative financial trading"}
@app.get("/api/portal/domains")
def domains():return {"domains":[{"id":k,"name":label} for k,(label,_) in DOMAINS.items()]}
@app.post("/api/portal/register")
def register(data:RegisterInput,request:Request,response:Response,db:Session=Depends(db_session)):
    safe_db();origin_guard(request);limit(request,"signup",4);validate_password(data.password)
    if db.scalar(select(Customer.id).where(Customer.email==data.email)):raise HTTPException(409,"E-mail zajęty")
    salt=secrets.token_hex(16);token=secrets.token_urlsafe(32)
    user=Customer(id=str(uuid.uuid4()),email=data.email,name=data.name.strip(),salt=salt,secret=password_hash(data.password,salt),
                  verified=False,verify_digest=digest(token),verify_expires=now()+timedelta(hours=24))
    db.add(user);db.commit()
    mailed=False
    try:mailed=send_verification(user.email,token)
    except Exception:mailed=False
    out=login_user(response,db,user)
    out.update({"verification_sent":mailed,"public_listing_allowed":False,"message":("Sprawdź pocztę, aby aktywować publiczną ofertę." if mailed else "Konto prywatne utworzone. Publiczna oferta będzie dostępna po skonfigurowaniu i potwierdzeniu e-mail.")})
    return out
@app.get("/api/portal/verify")
def verify(token:str,db:Session=Depends(db_session)):
    if len(token)>100 or len(token)<20:raise HTTPException(400,"Niepoprawny link")
    user=db.scalar(select(Customer).where(Customer.verify_digest==digest(token)))
    if not user or not user.verify_expires or utc(user.verify_expires)<now():raise HTTPException(400,"Link nieważny lub wygasł")
    user.verified=True;user.verify_digest=None;user.verify_expires=None;db.commit()
    return RedirectResponse(url="/platform/#konto-potwierdzone",status_code=303)
@app.post("/api/portal/login")
def login(data:LoginInput,request:Request,response:Response,db:Session=Depends(db_session)):
    safe_db();origin_guard(request);limit(request,"login",12)
    user=db.scalar(select(Customer).where(Customer.email==data.email.strip().lower()))
    if not user:
        password_hash(data.password,"1"*32)
        raise HTTPException(401,"Niepoprawny e-mail lub hasło")
    if not hmac.compare_digest(user.secret,password_hash(data.password,user.salt)):raise HTTPException(401,"Niepoprawny e-mail lub hasło")
    return login_user(response,db,user)
@app.get("/api/portal/me")
def me(request:Request,identity=Depends(current)):
    user,_=identity
    return {"user":{"name":user.name,"email":user.email,"verified":user.verified},"csrf":request.cookies.get(CSRF_COOKIE,"")}
@app.post("/api/portal/logout")
def logout(response:Response,identity=Depends(csrf),db:Session=Depends(db_session)):
    db.delete(identity[1]);db.commit();clear_cookie(response);return {"ok":True}
@app.get("/api/portal/my/bots")
def my_bots(identity=Depends(current),db:Session=Depends(db_session)):
    user,_=identity;rows=db.scalars(select(TechAgent).where(TechAgent.owner_id==user.id).order_by(TechAgent.created.desc())).all()
    return {"items":[as_agent(x) for x in rows],"max":BOTS_PER_ACCOUNT}
@app.post("/api/portal/my/bots")
def create_bot(data:AgentInput,identity=Depends(csrf),db:Session=Depends(db_session)):
    user,_=identity;safe_db()
    technical_domain(data.domain)
    if data.role not in ALLOWED_ROLES:raise HTTPException(422,"Wybierz techniczną rolę")
    if data.engine not in ("local","openai"):raise HTTPException(422,"Niedozwolony silnik")
    if db.scalar(select(func.count()).select_from(TechAgent).where(TechAgent.owner_id==user.id))>=BOTS_PER_ACCOUNT:
        raise HTTPException(429,"Limit 5 prywatnych robotów na konto")
    x=TechAgent(id=str(uuid.uuid4()),owner_id=user.id,name=data.name.strip(),summary=data.summary.strip(),domain=data.domain,role=data.role,engine=data.engine,public=False)
    db.add(x);db.commit();return as_agent(x)
@app.post("/api/portal/my/bots/{bot_id}/visibility")
def visibility(bot_id:str,data:AgentEdit,identity=Depends(csrf),db:Session=Depends(db_session)):
    user,_=identity
    bot=db.get(TechAgent,bot_id)
    if not bot or bot.owner_id!=user.id:raise HTTPException(404,"Nie ma takiego bota")
    if data.public and not user.verified:raise HTTPException(403,"Potwierdź adres e-mail przed publikacją oferty")
    bot.public=data.public;db.commit();return as_agent(bot)
@app.get("/api/portal/market")
def market(q:str="",domain:str="",limit:int=20,db:Session=Depends(db_session)):
    size=min(60,max(1,limit))
    st=select(TechAgent).where(TechAgent.public==True)
    if domain:st=st.where(TechAgent.domain==technical_domain(domain))
    if q:
        st=st.where(TechAgent.name.ilike("%"+q[:60].replace("%","")+"%"))
    bots=db.scalars(st.order_by(TechAgent.created.desc()).limit(size)).all()
    return {"items":[as_agent(x) for x in bots],"count":len(bots),"note":"Publiczne oferty klientów, a nie zweryfikowana moc lub aktywność bota"}
async def respond_ai(prompt:str,domain:str,role:str):
    if os.getenv("MSW_ALLOW_PAID_INFERENCE")!="1" or not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(503,"Płatny model API jest wyłączony; wybierz darmowy silnik lokalny.")
    model=os.getenv("MSW_OPENAI_MODEL","gpt-4.1-mini")
    system=("Jesteś agentem technicznym MSW AI. Specjalizacja "+DOMAINS[domain][0]+", rola "+role+
            ". Udzielaj tylko bezpiecznych wyjaśnień i diagnozy wstępnej dotyczącej domów, pojazdów, maszyn, elektryki i IT. "
            "Nie wydawaj poleceń urządzeniom, nie zmieniaj PLC ani ECU, nie ujawniaj danych, "
            "nie wykonuj niezaufanego kodu. Przy ryzyku poleć specjalistę. Odrzuć zadania nietechniczne.")
    payload={"model":model,"instructions":system,"input":prompt,"max_output_tokens":320}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(24,connect=6),follow_redirects=False) as client:
            r=await client.post("https://api.openai.com/v1/responses",json=payload,
                headers={"Authorization":"Bearer "+os.environ["OPENAI_API_KEY"],"Content-Type":"application/json"})
        if r.status_code!=200:raise HTTPException(502,"Dostawca AI nie odpowiedział prawidłowo")
        data=r.json();parts=[x.get("text","") for block in data.get("output",[]) for x in block.get("content",[]) if x.get("type")=="output_text"]
        answer="\n".join(parts).strip()
        if not answer:raise HTTPException(502,"Dostawca AI zwrócił pustą odpowiedź")
        return {"answer":answer[:3500],"engine":"openai","model":model,"verified_device_diagnosis":False}
    except httpx.HTTPError:raise HTTPException(503,"Brak połączenia z dostawcą modelu")
@app.post("/api/portal/my/bots/{bot_id}/run")
async def run_bot(bot_id:str,data:RunInput,identity=Depends(csrf),db:Session=Depends(db_session)):
    user,_=identity;bot=db.get(TechAgent,bot_id)
    if not bot or bot.owner_id!=user.id:raise HTTPException(404,"Nie ma takiego bota")
    since=now()-timedelta(days=1)
    count=db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.customer_id==user.id,AgentRun.created>=since)) or 0
    if count>=MAX_RUNS_DAY:raise HTTPException(429,"Dzienne limity testów zostały wykorzystane")
    start=time.perf_counter()
    if bot.engine=="openai":
        if not data.allow_paid_model:raise HTTPException(422,"Wymagana jawna zgoda na płatny model zewnętrzny")
        result=await respond_ai(data.question,bot.domain,bot.role)
    else:
        result={"answer":classify(data.question,bot.role),"engine":"local_rules","verified_device_diagnosis":False}
    elapsed=int((time.perf_counter()-start)*1000)
    bot.runs+=1
    db.add(AgentRun(id=str(uuid.uuid4()),agent_id=bot.id,customer_id=user.id,latency_ms=elapsed,success=True,provider=bot.engine))
    db.commit()
    return {**result,"bot_id":bot.id,"latency_ms":elapsed,"private_prompt_stored":False}
@app.get("/api/portal/arena/overview")
def arena_overview(db:Session=Depends(db_session)):
    rows=db.execute(select(ArenaRecord.agent_id,func.count(ArenaRecord.id),func.sum(ArenaRecord.passed),func.sum(ArenaRecord.total)).group_by(ArenaRecord.agent_id).limit(50)).all()
    ids=[r[0] for r in rows]
    agents={x.id:x for x in db.scalars(select(TechAgent).where(TechAgent.id.in_(ids))).all()} if ids else {}
    items=[{"bot":agents[r[0]].name,"id":r[0],"matches":int(r[1]),"passed":int(r[2] or 0),"total":int(r[3] or 0),
            "accuracy_percent":round(100*int(r[2] or 0)/max(1,int(r[3] or 1)),1)} for r in rows if r[0] in agents and agents[r[0]].public]
    return {"items":sorted(items,key=lambda x:x["accuracy_percent"],reverse=True),"metric":"classification_accuracy_on_same_fixed_technical_tasks_not_general_ai_power"}
@app.post("/api/portal/my/bots/{bot_id}/benchmark")
def run_arena(bot_id:str,identity=Depends(csrf),db:Session=Depends(db_session)):
    user,_=identity;bot=db.get(TechAgent,bot_id)
    if not bot or bot.owner_id!=user.id:raise HTTPException(404,"Nie ma takiego bota")
    if bot.engine!="local":raise HTTPException(422,"Ten benchmark porównuje wyłącznie lokalny silnik regułowy. Testowanie LLM wymaga osobnych testów.")
    r=benchmark(bot.name)
    db.add(ArenaRecord(id=str(uuid.uuid4()),owner_id=user.id,agent_id=bot.id,passed=r["passed"],total=r["trials"]))
    db.commit();return {**r,"bot_id":bot.id}
@app.post("/api/portal/orders")
def order(data:BuildInput,identity=Depends(csrf),db:Session=Depends(db_session)):
    user,_=identity;technical_domain(data.domain)
    prior=db.scalar(select(func.count()).select_from(BuildRequest).where(BuildRequest.owner_id==user.id,BuildRequest.created>now()-timedelta(days=1))) or 0
    if prior>=3:raise HTTPException(429,"Limit trzech zapytań na dobę")
    row=BuildRequest(id=str(uuid.uuid4()),owner_id=user.id,title=data.title.strip(),domain=data.domain,details=data.details.strip(),status="queued_for_quote")
    db.add(row);db.commit();return {"id":row.id,"status":row.status,"saved":True,"notification_sent":False,"message":"Zapytanie zapisano. Czeka na obsługę MSW."}
@app.get("/api/portal/my/orders")
def orders(identity=Depends(current),db:Session=Depends(db_session)):
    user,_=identity;rows=db.scalars(select(BuildRequest).where(BuildRequest.owner_id==user.id).order_by(BuildRequest.created.desc()).limit(50)).all()
    return {"items":[{"id":x.id,"title":x.title,"domain":x.domain,"status":x.status,"created":x.created.isoformat()} for x in rows]}
@app.get("/api/portal/metrics")
def metrics(db:Session=Depends(db_session)):
    count=db.scalar(select(func.count()).select_from(AgentRun)) or 0
    tested=db.scalar(select(func.count()).select_from(ArenaRecord)) or 0
    return {"runs":count,"benchmarks":tested,"source":"real_persisted_database_events","bots_active_in_background":0,
            "comparison_kind":"task_accuracy_not_trading_price","model_weights_trained":False}

# Mount the safe, fixed-origin 300-domain / 500-operation technical microservice.
from . import platform_catalog  # noqa: E402,F401
