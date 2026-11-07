"""COMBO SLM: Render-side orchestration core, not an embedded/trained language model.

No WordPress bootstrap on import, public listener, new scheduler, or financial action.
External effects require deployment-owned adapters with authenticated read-back.
"""
from __future__ import annotations
import colorsys
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

VERSION = "0.1.0"
SCHEMA = "msw.combo-slm.v1"
MIN_PRODUCTS, MIN_ARTICLES = 50, 30
EXCLUDED_HOSTS = frozenset({"dom-ai.pl"})
STAGES = ("domain.verify", "hosting.prepare", "wordpress.bootstrap", "combo.enroll",
          "brand.design", "theme.stage", "media.prepare", "products.build",
          "articles.build", "shop.configure", "paypal.verify", "telegram.verify",
          "quality.verify", "portal.release", "portal.verify")
# A model cannot change these operations or turn a recommendation into a shell command.
RELEASE_CHECKS = ("dns", "tls", "backup", "theme_mobile", "links", "media_rights",
                  "privacy", "tax_configuration", "checkout_sandbox", "download_entitlement",
                  "paypal_merchant", "paypal_webhook", "telegram_delivery", "combo_roundtrip")

class FactoryError(RuntimeError):
    pass


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def domain(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 253:
        raise FactoryError("invalid_domain")
    try:
        name = value.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise FactoryError("invalid_domain") from None
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", name):
        raise FactoryError("invalid_domain")
    if name in EXCLUDED_HOSTS:
        raise FactoryError("excluded_domain")
    return name


def same_https(url: str, host: str) -> bool:
    try:
        u = urlsplit(url)
        return u.scheme == "https" and u.hostname == host and not u.username and not u.password and u.port in (None, 443)
    except ValueError:
        return False


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", value):
        raise FactoryError("invalid_identifier")
    return value


@dataclass(frozen=True)
class Brief:
    """Created by the authenticated command adapter, not by model output."""
    project_id: str
    domain: str
    business: str
    audience: str
    installation_id: str
    language: str = "pl"
    products: int = MIN_PRODUCTS
    articles: int = MIN_ARTICLES

    def __post_init__(self):
        identifier(self.project_id)
        identifier(self.installation_id)
        if domain(self.domain) != self.domain:
            raise FactoryError("normalized_domain_required")
        for value in (self.business, self.audience):
            if not isinstance(value, str) or not 3 <= len(value) <= 500:
                raise FactoryError("business_brief_required")
        if self.language not in {"pl", "en", "de"}:
            raise FactoryError("unsupported_language")
        if type(self.products) is not int or not MIN_PRODUCTS <= self.products <= 500:
            raise FactoryError("product_target_invalid")
        if type(self.articles) is not int or not MIN_ARTICLES <= self.articles <= 300:
            raise FactoryError("article_target_invalid")


def rgb(hue: int, sat: float, light: float) -> str:
    channels = colorsys.hls_to_rgb((hue % 360) / 360, light, sat)
    return "#" + "".join(f"{round(c * 255):02x}" for c in channels)


def luminance(color: str) -> float:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise FactoryError("invalid_color")
    c = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in c]
    return sum(a*b for a, b in zip(c, (.2126, .7152, .0722)))


def contrast(a: str, b: str) -> float:
    x, y = sorted((luminance(a), luminance(b)))
    return (y + .05) / (x + .05)


def design_candidate(brief: Brief, attempt: int = 0) -> dict:
    """Deterministic design diversity; this function is explicitly NOT an SLM."""
    seed = hashlib.sha256(f"{brief.domain}|{brief.installation_id}|{attempt}".encode()).digest()
    text = brief.business.casefold()
    family = ("wellness" if any(x in text for x in ("spa", "jacuzzi", "saun")) else
              "editorial" if any(x in text for x in ("eduk", "poradnik", "kurs")) else
              "studio" if any(x in text for x in ("design", "premium", "luksus")) else "technical")
    layouts = {
        "wellness": ("calm-split", "panorama", "organic-grid", "story-led"),
        "editorial": ("magazine", "knowledge-hub", "journal", "course-led"),
        "studio": ("atelier", "gallery-led", "minimal-split", "portfolio-led"),
        "technical": ("workbench", "specification-grid", "service-led", "diagnostic-hub"),
    }
    hue = int.from_bytes(seed[:2], "big") % 360
    primary = rgb(hue, .42 + seed[2] / 1000, .26 + seed[3] / 2500)
    on_primary = "#ffffff" if contrast(primary, "#ffffff") >= contrast(primary, "#000000") else "#000000"
    background = rgb(hue + 25, .12, .975)
    foreground = rgb(hue, .2, .10)
    result = {"family": family, "layout": layouts[family][seed[4] % 4],
              "palette": {"primary": primary, "on_primary": on_primary,
                          "accent": rgb(hue+55+seed[5] % 100, .55, .42),
                          "background": background, "foreground": foreground},
              "typography": ("system-sans", "editorial-serif", "technical-sans", "humanist-sans")[seed[6] % 4],
              "density": ("airy", "balanced", "compact")[seed[7] % 3],
              "card_style": ("flat", "outlined", "soft-shadow", "editorial-lines")[seed[8] % 4],
              "hero": ("split-media", "wide-media", "asymmetric", "text-led")[seed[9] % 4],
              "radius_px": (0, 6, 12, 20)[seed[10] % 4],
              "business": brief.business, "installation_id": brief.installation_id,
              "generated_by": "deterministic_design_allocator_not_language_model"}
    result["signature"] = digest({k: v for k, v in result.items() if k not in {"business", "installation_id"}})
    return result


def similarity(a: dict, b: dict) -> float:
    # Reject clones even when only the color has changed.
    axes = ("family", "layout", "typography", "density", "card_style", "hero", "radius_px")
    return sum(a[k] == b[k] for k in axes) / len(axes)


@dataclass(frozen=True)
class Grant:
    """Only a trusted COMBO/hosting adapter supplies this value; never deserialize model text here."""
    host: str
    observed_at: int
    authenticated: bool
    owner_authorized: bool
    quarantined: bool
    allowed_operations: frozenset[str]
    roster_revision: str

    def permits(self, host: str, action: str, now: int) -> bool:
        return (self.host == host and self.authenticated is True and self.owner_authorized is True
                and self.quarantined is False and 0 <= now - self.observed_at <= 900
                and bool(self.roster_revision) and action in self.allowed_operations)


@dataclass(frozen=True)
class Adapter:
    """Deployment-owned callables. No callable names, URLs or code supplied by the model."""
    run: Callable[[Brief, str, str], dict]
    readback: Callable[[Brief, str, str, dict], dict]


def validate_receipt(receipt: dict, brief: Brief, stage: str, request_id: str) -> dict:
    if not isinstance(receipt, dict) or any(receipt.get(k) is not True for k in ("authenticated", "verified")):
        raise FactoryError("receipt_unverified")
    if (receipt.get("domain"), receipt.get("stage"), receipt.get("request_id")) != (brief.domain, stage, request_id):
        raise FactoryError("receipt_identity_mismatch")
    if receipt.get("status") != "done":
        raise FactoryError("queued_is_not_completed")
    artifact = receipt.get("artifact_sha256")
    if not isinstance(artifact, str) or not re.fullmatch(r"[a-f0-9]{64}", artifact):
        raise FactoryError("receipt_digest_missing")
    # Keep a compact receipt, never opaque provider/customer bodies or credentials.
    return {k: receipt[k] for k in ("authenticated", "verified", "domain", "stage", "request_id", "status", "artifact_sha256")}


class Store:
    def __init__(self, root: Path):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise FactoryError("symlink_storage_rejected")
        self.path = root / "combo-slm.sqlite3"
        if self.path.is_symlink():
            raise FactoryError("symlink_storage_rejected")
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, brief TEXT NOT NULL, fingerprint TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS designs(project TEXT PRIMARY KEY, signature TEXT UNIQUE NOT NULL, design TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(project TEXT NOT NULL, position INTEGER NOT NULL, stage TEXT NOT NULL,
                request_id TEXT UNIQUE NOT NULL, state TEXT NOT NULL, receipt TEXT, error TEXT,
                PRIMARY KEY(project,position));
            CREATE TABLE IF NOT EXISTS documents(project TEXT NOT NULL, id TEXT NOT NULL, text TEXT NOT NULL,
                source TEXT NOT NULL, sha TEXT NOT NULL, PRIMARY KEY(project,id));
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(project UNINDEXED, id UNINDEXED, text);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self, brief: Brief) -> dict:
        raw = canonical(brief.__dict__)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT fingerprint FROM projects WHERE id=?", (brief.project_id,)).fetchone()
            if old:
                if old[0] != digest(brief.__dict__):
                    raise FactoryError("project_idempotency_conflict")
                return {"created": False, "project_id": brief.project_id}
            previous = [json.loads(r[0]) for r in db.execute("SELECT design FROM designs")]
            selected = None
            for attempt in range(256):
                candidate = design_candidate(brief, attempt)
                if all(similarity(candidate, other) < .86 and candidate["palette"]["primary"] != other["palette"]["primary"] for other in previous):
                    selected = candidate
                    break
            if selected is None:
                raise FactoryError("design_diversity_exhausted_requires_new_family")
            db.execute("INSERT INTO projects VALUES(?,?,?)", (brief.project_id, raw, digest(brief.__dict__)))
            db.execute("INSERT INTO designs VALUES(?,?,?)", (brief.project_id, selected["signature"], canonical(selected)))
            for i, stage in enumerate(STAGES):
                request_id = "combo-" + digest([brief.project_id, brief.installation_id, stage])
                db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", (brief.project_id, i, stage, request_id, "pending", None, None))
        return {"created": True, "project_id": brief.project_id, "stages": len(STAGES), "design": selected,
                "products_created": 0, "articles_created": 0, "model_inference_verified": False}

    def brief(self, project: str) -> Brief:
        with self.connect() as db:
            row = db.execute("SELECT brief FROM projects WHERE id=?", (identifier(project),)).fetchone()
        if row is None:
            raise FactoryError("project_not_found")
        return Brief(**json.loads(row[0]))

    def status(self, project: str) -> dict:
        self.brief(project)
        with self.connect() as db:
            jobs = [{"stage": a, "state": b, "error": c} for a, b, c in db.execute(
                "SELECT stage,state,error FROM jobs WHERE project=? ORDER BY position", (project,))]
        return {"project_id": project, "jobs": jobs,
                "workflow_receipts_complete": all(j["state"] == "done" for j in jobs),
                "portal_ready": False, "portal_ready_requires_fresh_acceptance_evidence": True}

    def remember(self, project: str, doc_id: str, text: str, source: str, *, approved: bool):
        self.brief(project)
        if approved is not True or not isinstance(text, str) or not 1 <= len(text) <= 20000:
            raise FactoryError("memory_review_required")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", doc_id) or not 3 <= len(source) <= 500:
            raise FactoryError("memory_source_required")
        if re.search(r"(?i)(?:api[_ -]?key|password|authorization|client_secret)\s*[:=]|\bsk-[A-Za-z0-9]{12,}|[\w.+-]+@[\w.-]+\.[a-z]{2,}", text):
            raise FactoryError("memory_secrets_or_personal_data_rejected")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)", (project, doc_id, text, source, digest(text)))
            db.execute("DELETE FROM memory_fts WHERE project=? AND id=?", (project, doc_id))
            db.execute("INSERT INTO memory_fts VALUES(?,?,?)", (project, doc_id, text))
        return {"stored": True, "sha256": digest(text), "model_weights_trained": False}

    def retrieve(self, project: str, query: str) -> list[dict]:
        self.brief(project)
        tokens = re.findall(r"\w{2,30}", query, re.UNICODE)[:12]
        if not tokens:
            return []
        expression = " OR ".join('"' + token + '"' for token in tokens)
        with self.connect() as db:
            rows = db.execute("""SELECT d.id,d.text,d.source,d.sha FROM memory_fts f JOIN documents d
                ON d.project=f.project AND d.id=f.id WHERE memory_fts MATCH ? AND f.project=?
                ORDER BY bm25(memory_fts) LIMIT 4""", (expression, project)).fetchall()
        return [{"id": a, "text": b[:3000], "source": c, "sha256": d} for a, b, c, d in rows]


class Orchestrator:
    def __init__(self, store: Store, grant_provider: Callable[[Brief], Grant], adapters: Mapping[str, Adapter],
                 verifier: Callable[[Brief], dict] | None = None):
        if set(adapters) - set(STAGES):
            raise FactoryError("undeclared_adapter")
        self.store, self.grants, self.adapters = store, grant_provider, dict(adapters)
        self.verifier = verifier

    def tick(self, project: str) -> dict:
        """At most one external operation. Called by the existing Render scheduler."""
        brief = self.store.brief(project)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE state='inflight' LIMIT 1").fetchone():
                return {"state": "busy_or_reconciliation_required", "executed": False}
            row = db.execute("SELECT position,stage,request_id,state FROM jobs WHERE project=? AND state!='done' ORDER BY position LIMIT 1", (project,)).fetchone()
            if row is None:
                return {"state": "workflow_receipts_complete", "portal_ready": False, "executed": False}
            pos, stage, rid, state = row
            if state != "pending":
                return {"state": state, "stage": stage, "executed": False}
            adapter = self.adapters.get(stage)
            if adapter is None:
                return {"state": "adapter_missing", "stage": stage, "executed": False}
            grant = self.grants(brief)
            if not isinstance(grant, Grant) or not grant.permits(brief.domain, stage, int(time.time())):
                return {"state": "fresh_owner_grant_required", "stage": stage, "executed": False}
            if stage == "portal.release":
                if self.verifier is None:
                    return {"state": "release_verifier_missing", "executed": False}
                verdict = acceptance(brief, self.verifier(brief))
                if verdict["portal_ready"] is not True:
                    return {"state": "quality_hold", "issues": verdict["issues"], "executed": False}
            db.execute("UPDATE jobs SET state='inflight' WHERE project=? AND position=?", (project, pos))
        # Reservation is durable before touching production. A crash is never blindly replayed.
        try:
            result = adapter.run(brief, stage, rid)
            ack = validate_receipt(result, brief, stage, rid)
            native_read = validate_receipt(adapter.readback(brief, stage, rid, ack), brief, stage, rid)
            if ack["artifact_sha256"] != native_read["artifact_sha256"]:
                raise FactoryError("independent_readback_mismatch")
        except Exception as exc:
            code = str(exc) if isinstance(exc, FactoryError) else "adapter_outcome_unknown"
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET state='reconcile',error=? WHERE project=? AND position=?", (code, project, pos))
            return {"state": "reconcile", "stage": stage, "error": code, "executed": None, "retry_automatically": False}
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET state='done',receipt=?,error=NULL WHERE project=? AND position=? AND state='inflight'", (canonical(native_read), project, pos))
            saved = db.execute("SELECT state,receipt FROM jobs WHERE project=? AND position=?", (project, pos)).fetchone()
            if saved != ("done", canonical(native_read)):
                raise FactoryError("durable_readback_failed")
        return {"state": "done", "stage": stage, "executed": True, "readback_verified": True}

    def report(self, project: str) -> dict:
        state = self.store.status(project)
        if not state["workflow_receipts_complete"] or self.verifier is None:
            return state
        brief = self.store.brief(project)
        if not self.grants(brief).permits(brief.domain, "portal.verify", int(time.time())):
            return {**state, "reason": "fresh_owner_grant_required"}
        return {**state, **acceptance(brief, self.verifier(brief))}

    def reconcile(self, project: str) -> dict:
        """Only read back an uncertain operation. Never call its write a second time."""
        brief = self.store.brief(project)
        with self.store.connect() as db:
            row = db.execute("SELECT position,stage,request_id FROM jobs WHERE project=? AND state IN ('inflight','reconcile') ORDER BY position LIMIT 1", (project,)).fetchone()
        if row is None:
            return {"state": "nothing_to_reconcile"}
        pos, stage, rid = row
        if stage not in self.adapters or not self.grants(brief).permits(brief.domain, stage, int(time.time())):
            raise FactoryError("reconciliation_authorization_required")
        receipt = validate_receipt(self.adapters[stage].readback(brief, stage, rid, {}), brief, stage, rid)
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET state='done',receipt=?,error=NULL WHERE project=? AND position=? AND state IN ('inflight','reconcile')", (canonical(receipt), project, pos))
        return {"state": "reconciled", "stage": stage, "write_replayed": False}


def acceptance(brief: Brief, evidence: dict, *, now: int | None = None) -> dict:
    """Call only with a snapshot collected by an authenticated verifier, not SLM text."""
    now = int(time.time()) if now is None else now
    if not isinstance(evidence, dict) or evidence.get("authenticated") is not True or evidence.get("domain") != brief.domain:
        raise FactoryError("acceptance_source_unverified")
    stamp = evidence.get("observed_at")
    if type(stamp) is not int or not 0 <= now - stamp <= 900:
        raise FactoryError("acceptance_stale")
    if not isinstance(evidence.get("checks", {}), dict):
        raise FactoryError("acceptance_checks_invalid")
    issues = ["check:"+k for k in RELEASE_CHECKS if evidence.get("checks", {}).get(k) is not True]
    counts = {}
    for kind, target in (("products", brief.products), ("articles", brief.articles)):
        rows = evidence.get(kind, [])
        if not isinstance(rows, list) or len(rows) > 1000:
            raise FactoryError("acceptance_inventory_invalid")
        ids, hashes = set(), set()
        valid = 0
        for item in rows:
            if not isinstance(item, dict):
                continue
            entity_id, sha = item.get("id"), item.get("content_sha256")
            if type(entity_id) is not int or entity_id < 1 or entity_id in ids or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha) or sha in hashes:
                continue
            if not same_https(str(item.get("url", "")), brief.domain):
                continue
            checks = ["public_readback", "quality_review", "unique_content", "image_readback"]
            if kind == "products":
                checks += ["purchasable", "delivery_verified", "price_verified"]
            if item.get("status") != "publish" or any(item.get(k) is not True for k in checks):
                continue
            ids.add(entity_id); hashes.add(sha); valid += 1
        counts[kind] = valid
        if valid < target:
            issues.append(f"{kind}:{valid}/{target}")
    if not same_https(str(evidence.get("portal_url", "")), brief.domain):
        issues.append("portal_url_unverified")
    return {"schema": SCHEMA, "portal_ready": not issues, "verified_counts": counts,
            "issues": issues, "observed_at": stamp, "snapshot_sha256": digest(evidence),
            "model_weights_trained": False}
