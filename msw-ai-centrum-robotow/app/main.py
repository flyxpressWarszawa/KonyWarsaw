"""MSW AI Centrum Robotów — standalone, real-data API and dashboard.

No WordPress, WP REST, demo metric fixtures, or repair diagnostics.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import re
import secrets
import uuid
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, create_engine, delete, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

ROOT = Path(__file__).resolve().parent
BASE = "/msw-ai-centrum-robotow"
DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./msw_robots.db")
if DB_URL.startswith("postgres://"):
    DB_URL = "postgresql+psycopg://" + DB_URL[len("postgres://"):]
elif DB_URL.startswith("postgresql://"):
    DB_URL = "postgresql+psycopg://" + DB_URL[len("postgresql://"):]
ENGINE = create_engine(DB_URL, pool_pre_ping=True, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="")
    endpoint: Mapped[str | None] = mapped_column(String(300), nullable=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # NEVER imply independently verified
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False)  # gateway | self_report
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    bot: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="other")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=ENGINE)
    yield


app = FastAPI(title="MSW AI Robot Intelligence", version="1.0.0", lifespan=lifespan,
              docs_url=None if os.environ.get("HIDE_API_DOCS") == "1" else "/docs")
app.mount(f"{BASE}/assets", StaticFiles(directory=ROOT / "static"), name="assets")


def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def require_secret(auth: str | None, env_name: str) -> None:
    expected = os.environ.get(env_name)
    if not expected or len(expected) < 24:
        raise HTTPException(status_code=503, detail=f"{env_name} is not configured")
    given = auth[7:].strip() if auth and auth.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(expected, given):
        raise HTTPException(status_code=401, detail="Invalid bearer token")


def check_agent_token(agent: Agent, auth: str | None) -> None:
    token = auth[7:].strip() if auth and auth.lower().startswith("bearer ") else ""
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not token or not hmac.compare_digest(agent.token_digest, digest):
        raise HTTPException(status_code=401, detail="Invalid agent token")


BOT_SIGNATURES = [
    ("GPTBot", "OpenAI", "crawler", re.compile(r"gptbot", re.I)),
    ("ChatGPT-User", "OpenAI", "agent", re.compile(r"chatgpt-user", re.I)),
    ("OAI-SearchBot", "OpenAI", "crawler", re.compile(r"oai-searchbot", re.I)),
    ("ClaudeBot", "Anthropic", "crawler", re.compile(r"claudebot", re.I)),
    ("Claude-User", "Anthropic", "agent", re.compile(r"claude-user", re.I)),
    ("Googlebot", "Google", "crawler", re.compile(r"googlebot", re.I)),
    ("Google-Extended", "Google", "crawler", re.compile(r"google-extended", re.I)),
    ("Bingbot", "Microsoft", "crawler", re.compile(r"bingbot", re.I)),
    ("PerplexityBot", "Perplexity", "crawler", re.compile(r"perplexitybot", re.I)),
    ("Perplexity-User", "Perplexity", "agent", re.compile(r"perplexity-user", re.I)),
    ("Applebot", "Apple", "crawler", re.compile(r"applebot", re.I)),
    ("Meta-ExternalAgent", "Meta", "agent", re.compile(r"meta-externalagent", re.I)),
    ("Bytespider", "ByteDance", "crawler", re.compile(r"bytespider", re.I)),
]


def classify_bot(user_agent: str) -> tuple[str, str, str] | None:
    """UA-based name is a declaration, NOT cryptographic crawler identity verification."""
    for name, provider, category, pattern in BOT_SIGNATURES:
        if pattern.search(user_agent[:300]):
            return name, provider, category
    return None


# Registration abuse protection: ephemeral per instance, minimal privacy retention.
_register_lock = Lock()
_registration_times: dict[str, list[datetime]] = {}


def enforce_registration_limit(request: Request) -> None:
    address = request.client.host if request.client else "unknown"
    # Never retain raw address. Rotates automatically each UTC day.
    day = utcnow().strftime("%Y-%m-%d")
    key = hashlib.sha256(f"{day}:{address}".encode()).hexdigest()
    now = utcnow()
    with _register_lock:
        for k in list(_registration_times):
            _registration_times[k] = [t for t in _registration_times[k] if t > now - timedelta(hours=1)]
            if not _registration_times[k]:
                del _registration_times[k]
        if len(_registration_times.get(key, [])) >= 5:
            raise HTTPException(status_code=429, detail="Registration limit reached; try later")
        _registration_times.setdefault(key, []).append(now)


class RegisterInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    provider: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=500)
    endpoint: str | None = Field(default=None, max_length=300)

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value: str | None) -> str | None:
        if not value:
            return None
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError("Endpoint must be a public HTTPS URL on port 443")
        if parts.hostname in {"localhost", "127.0.0.1", "::1"} or parts.hostname.endswith((".local", ".internal")):
            raise ValueError("Local endpoints are not allowed")
        # Endpoints are stored as declarations. The server never fetches untrusted URLs.
        return value


class BotRequest(BaseModel):
    user_agent: str = Field(min_length=1, max_length=500)
    latency_ms: int | None = Field(default=None, ge=0, le=600000)
    http_status: int | None = Field(default=None, ge=100, le=599)


class AgentEvent(BaseModel):
    latency_ms: int | None = Field(default=None, ge=0, le=600000)
    http_status: int | None = Field(default=None, ge=100, le=599)
    category: Literal["response", "task", "heartbeat"] = "heartbeat"


def csv_safe(value: str) -> str:
    """Prevent spreadsheet formula injection from names registered by outsiders."""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def visible_agent(row: Agent, total_events: int = 0) -> dict:
    return {"id": row.id, "name": row.name, "provider": row.provider,
            "description": row.description,
            "status": row.status, "created_at": iso(row.created_at),
            "last_seen": iso(row.last_seen), "observations": total_events,
            "verification_note": "Samodeklaracja; tożsamość i moc modelu nie zostały zweryfikowane"}


@app.get("/")
@app.get(BASE)
@app.get(BASE + "/")
def homepage():
    return FileResponse(ROOT / "static" / "index.html", media_type="text/html",
                        headers={"Cache-Control": "no-store"})


@app.get(BASE + "/api/v1/health")
def health(session: Session = Depends(db_session)):
    try:
        session.execute(text("SELECT 1"))
        return {"ok": True, "service": "msw-ai-robot-intelligence", "version": "1.0.0", "db": "connected",
                "wordpress_dependency": False, "time": iso(utcnow())}
    except Exception:
        raise HTTPException(status_code=503, detail="Database not available")


@app.get(BASE + "/api/v1/agents")
def agents(session: Session = Depends(db_session), q: str = Query(default="", max_length=100)):
    rows = session.scalars(select(Agent).order_by(Agent.created_at.desc()).limit(500)).all()
    counts = dict(session.execute(select(Event.agent_id, func.count()).where(Event.agent_id.is_not(None))
                                  .group_by(Event.agent_id)).all())
    filtered = [r for r in rows if not q or q.lower() in f"{r.name} {r.provider} {r.description}".lower()]
    return {"items": [visible_agent(r, counts.get(r.id, 0)) for r in filtered], "count": len(filtered),
            "disclaimer": "Agent registration and self-reported telemetry do not verify model power or identity"}


@app.post(BASE + "/api/v1/agents/register", status_code=201)
def register(payload: RegisterInput, request: Request, session: Session = Depends(db_session)):
    enforce_registration_limit(request)
    token = secrets.token_urlsafe(32)
    row = Agent(id=str(uuid.uuid4()), name=payload.name.strip(), provider=payload.provider.strip(),
                description=payload.description.strip(), endpoint=payload.endpoint,
                token_digest=hashlib.sha256(token.encode()).hexdigest(), status="pending")
    session.add(row)
    session.commit()
    return {"agent": visible_agent(row), "agent_token": token,
            "important": "Save this token now. It is shown once and is not stored in plaintext.",
            "next_step": f"POST {BASE}/api/v1/agents/{row.id}/events with Authorization: Bearer <token>"}


@app.post(BASE + "/api/v1/agents/{agent_id}/events", status_code=202)
def agent_event(agent_id: str, payload: AgentEvent,
                authorization: Annotated[str | None, Header()] = None,
                session: Session = Depends(db_session)):
    row = session.get(Agent, agent_id)
    if not row:
        raise HTTPException(status_code=404, detail="Agent not found")
    check_agent_token(row, authorization)
    recent = session.scalar(select(func.count(Event.id)).where(
        Event.agent_id == agent_id, Event.recorded_at >= utcnow() - timedelta(minutes=1))) or 0
    if recent >= 120:
        raise HTTPException(status_code=429, detail="Agent event rate limit exceeded")
    event = Event(id=str(uuid.uuid4()), source="self_report", agent_id=agent_id,
                  bot=row.name, provider=row.provider, category=payload.category,
                  latency_ms=payload.latency_ms, http_status=payload.http_status)
    row.last_seen = utcnow()
    row.status = "self_reported"  # self-submission != independent verification
    session.add(event)
    session.commit()
    return {"accepted": True, "source": "self_report", "model_power_verified": False}


@app.post(BASE + "/api/v1/events/ingest", status_code=202)
def ingest(payload: BotRequest, authorization: Annotated[str | None, Header()] = None,
           session: Session = Depends(db_session)):
    require_secret(authorization, "INGEST_SECRET")
    bot = classify_bot(payload.user_agent)
    if not bot:
        return {"accepted": False, "reason": "User-Agent not in recognized crawler list"}
    name, provider, category = bot
    session.add(Event(id=str(uuid.uuid4()), source="gateway", bot=name,
                      provider=provider, category=category, latency_ms=payload.latency_ms,
                      http_status=payload.http_status))
    session.commit()
    return {"accepted": True, "classification": "User-Agent declaration", "bot": name}


def observations(session: Session, days: int = 7) -> list[Event]:
    # Bound data volume and retention; real observations only, no synthesized metrics.
    return session.scalars(select(Event).where(Event.recorded_at >= utcnow() - timedelta(days=days))
                           .order_by(Event.recorded_at.desc()).limit(20000)).all()


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def overview_data(session: Session):
    """All published KPIs use exact database counts (no silent 20k sample cap)."""
    now = utcnow()
    cutoff = now - timedelta(days=7)
    c24 = now - timedelta(hours=24)
    total7 = session.scalar(select(func.count(Event.id)).where(Event.recorded_at >= cutoff)) or 0
    count24 = session.scalar(select(func.count(Event.id)).where(Event.recorded_at >= c24)) or 0
    n_agents = session.scalar(select(func.count(Agent.id))) or 0
    reporting = session.scalar(select(func.count(Agent.id)).where(Agent.status == "self_reported")) or 0
    gateway = session.scalar(select(func.count(Event.id)).where(
        Event.recorded_at >= cutoff, Event.source == "gateway")) or 0
    by_provider = session.execute(select(Event.provider, func.count(Event.id)).where(
        Event.recorded_at >= cutoff).group_by(Event.provider).order_by(func.count(Event.id).desc()).limit(12)).all()
    by_bot = session.execute(select(Event.bot, func.count(Event.id)).where(
        Event.recorded_at >= cutoff).group_by(Event.bot).order_by(func.count(Event.id).desc()).limit(20)).all()
    by_source = session.execute(select(Event.source, func.count(Event.id)).where(
        Event.recorded_at >= cutoff).group_by(Event.source)).all()
    day_rows = session.execute(select(func.date(Event.recorded_at), Event.source, func.count(Event.id)).where(
        Event.recorded_at >= cutoff).group_by(func.date(Event.recorded_at), Event.source)).all()
    day_counts = defaultdict(Counter)
    for date, source, count in day_rows:
        day_counts[str(date)][source] += count
    daily = [{"date": (now-timedelta(days=i)).date().isoformat(),
              "gateway": day_counts[(now-timedelta(days=i)).date().isoformat()].get("gateway", 0),
              "self_report": day_counts[(now-timedelta(days=i)).date().isoformat()].get("self_report", 0)}
             for i in range(6, -1, -1)]
    with_latency = Event.recorded_at >= cutoff, Event.latency_ms.is_not(None)
    n_latency = session.scalar(select(func.count(Event.id)).where(*with_latency)) or 0
    p95 = session.scalar(select(Event.latency_ms).where(*with_latency).order_by(
        Event.latency_ms.asc()).offset(int((n_latency-1)*0.95)).limit(1)) if n_latency else None
    last = session.scalar(select(Event.recorded_at).order_by(Event.recorded_at.desc()).limit(1))
    return {"generated_at": iso(now), "metrics": {"observations_24h": count24,
            "observations_7d": total7, "registered_agents": n_agents,
            "reporting_agents": reporting, "gateway_observations_7d": gateway,
            "latency_p95_ms": p95},
            "providers": [{"name": name, "count": count} for name, count in by_provider],
            "bots": [{"name": name, "count": count} for name, count in by_bot],
            "daily": daily, "source_counts": dict(by_source), "last_observation": iso(last),
            "notes": ["Only observed gateway events and authenticated, self-reported agent messages are counted.",
                      "Crawler identification from User-Agent is not proof of crawler ownership.",
                      "HTTP volume and latency are not a model intelligence or compute-power benchmark.",
                      "No WordPress APIs are needed for this application."]}


@app.get(BASE + "/api/v1/overview")
def overview(session: Session = Depends(db_session)):
    return overview_data(session)


@app.get(BASE + "/api/v1/timeseries")
def timeseries(hours: int = Query(default=24, ge=1, le=168), session: Session = Depends(db_session)):
    """Only observed traffic. User selectable, bucketed without fabricated OHLC values.

    Public dashboard is intentionally bounded at 100k observations per request.
    When over limit, incomplete=true prevents presenting the counts as exhaustive.
    """
    if hours not in (1, 6, 24, 168):
        raise HTTPException(status_code=422, detail="Allowed time windows: 1, 6, 24, 168 hours")
    bucket_minutes = {1: 5, 6: 15, 24: 60, 168: 360}[hours]
    now = utcnow()
    bucket_start = now.replace(second=0, microsecond=0)
    minute_of_day = bucket_start.hour * 60 + bucket_start.minute
    bucket_start -= timedelta(minutes=minute_of_day % bucket_minutes)
    bins = int(hours * 60 / bucket_minutes)
    first = bucket_start - timedelta(minutes=bucket_minutes * (bins - 1))
    data = [{"timestamp": iso(first + timedelta(minutes=i * bucket_minutes)),
             "gateway": 0, "self_report": 0} for i in range(bins)]
    rows = session.execute(select(Event.recorded_at, Event.source).where(
        Event.recorded_at >= first).order_by(Event.recorded_at.desc()).limit(100001)).all()
    incomplete = len(rows) > 100000
    for stamp, source in rows[:100000]:
        index = int((as_utc(stamp) - first).total_seconds() // (bucket_minutes * 60))
        if 0 <= index < bins and source in ("gateway", "self_report"):
            data[index][source] += 1
    return {"period_hours": hours, "bucket_minutes": bucket_minutes,
            "generated_at": iso(now), "incomplete": incomplete,
            "metric": "observed_event_count_not_market_price_or_ai_power",
            "items": data}


@app.get(BASE + "/api/v1/comparison")
def comparison(a: str = Query(min_length=36, max_length=36),
               b: str = Query(min_length=36, max_length=36), session: Session = Depends(db_session)):
    if a == b:
        raise HTTPException(status_code=422, detail="Select two different agents")
    result = []
    for id_ in (a, b):
        agent = session.get(Agent, id_)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        evts = session.scalars(select(Event).where(Event.agent_id == id_, Event.recorded_at >= utcnow() - timedelta(days=7))).all()
        values = sorted(e.latency_ms for e in evts if e.latency_ms is not None)
        result.append({**visible_agent(agent, len(evts)), "latency_p95_ms": values[int((len(values)-1)*.95)] if values else None})
    return {"items": result, "measurement": "Submitted observations over last 7 days; self-reported, not an intelligence score"}


@app.get(BASE + "/api/v1/export.csv")
def export_csv():
    """Stream all 30 days of actual events in bounded batches, never silently sample."""
    def content():
        with SessionLocal() as session:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["source", "bot", "provider", "category", "recorded_at_utc", "latency_ms", "http_status"])
            yield ("\ufeff" + buffer.getvalue()).encode("utf-8")
            buffer.seek(0); buffer.truncate(0)
            rows = session.scalars(select(Event).where(
                Event.recorded_at >= utcnow() - timedelta(days=30)).order_by(Event.recorded_at.desc())).yield_per(500)
            for i, e in enumerate(rows, 1):
                writer.writerow([e.source, csv_safe(e.bot), csv_safe(e.provider), e.category, iso(e.recorded_at),
                                 e.latency_ms if e.latency_ms is not None else "", e.http_status if e.http_status is not None else ""])
                if i % 250 == 0:
                    yield buffer.getvalue().encode("utf-8")
                    buffer.seek(0); buffer.truncate(0)
            if buffer.tell():
                yield buffer.getvalue().encode("utf-8")
    return StreamingResponse(content(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="msw-ai-telemetria.csv"',
                                      "Cache-Control": "no-store"})


@app.get(BASE + "/api/v1/system")
def system(session: Session = Depends(db_session)):
    try:
        session.execute(text("SELECT 1"))
        database = "online"
    except Exception:
        database = "offline"
    return {"components": [{"id": "app", "name": "MSW Robot Center API", "status": "online"},
                           {"id": "db", "name": "Baza danych", "status": database},
                           {"id": "gateway", "name": "Telemetria z gateway", "status": "configured" if os.environ.get("INGEST_SECRET") else "needs_configuration"},
                           {"id": "registry", "name": "Rejestr agentów", "status": database}],
            "note": "Legacy WP engines are not claimed as connected."}


@app.get(BASE + "/api/v1/bots/catalog")
def bot_catalog():
    return {"items": [{"name": n, "provider": p, "category": c,
                       "identification": "User-Agent declaration, not verified identity"}
                      for n, p, c, _ in BOT_SIGNATURES]}
