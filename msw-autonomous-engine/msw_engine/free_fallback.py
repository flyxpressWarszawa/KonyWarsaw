"""Opt-in fallback for the existing federation dispatcher, not a new worker.

Only definite, rejected quota/auth/rate failures may choose the next provider.
Uncertain outcomes are durable stop states. Policy and exact public-input grants
are local operator files, never fields accepted from a remote task or web page.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from .tasks import TaskError

SCHEMA = "msw.free.fallback.v1"
SAFE_NEXT = frozenset({"rate_limit", "quota_exhausted", "auth_invalid", "not_sent"})
FREE_PROVIDERS = frozenset({"ai_horde", "openrouter_wp"})


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def public_input_digest(values):
    """Same algorithm is required by an eventual native WordPress callback."""
    return digest([values["request_id"], values["task"], values["text"]])


class ProviderFault(TaskError):
    def __init__(self, kind, code, *, retry_at=0):
        if kind not in SAFE_NEXT | {"unknown", "blocked"}:
            raise ValueError("fault_kind")
        self.kind = kind
        self.code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", code) else "unclassified"
        self.retry_at = retry_at if type(retry_at) is int and retry_at >= 0 else 0
        super().__init__(self.code)


def retry_after_at(value, now=None):
    """Absolute UTC deadline. Never shorten a valid server Retry-After value.

    Zero means no header; None means an invalid header and requires intervention.
    Both delta-seconds and an RFC HTTP date are supported.
    """
    now = time.time() if now is None else now
    if value is None or str(value).strip() == "":
        return 0
    value = str(value).strip()
    if value.isascii() and value.isdigit() and len(value) <= 12:
        return math.ceil(now + int(value))
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        return max(math.ceil(now), math.ceil(parsed.timestamp()))
    except (TypeError, ValueError, OverflowError):
        return None


def retry_suffix(headers, now=None):
    """Safe metadata appended to the existing durable paid-provider error code."""
    until = retry_after_at(headers.get("Retry-After") if headers else None, now)
    return ":retry_at_" + (str(until) if until is not None else "invalid")


def primary_fault(error):
    if isinstance(error, ProviderFault):
        return error
    code = str(error)
    # This prefix is emitted only after an explicit HTTP rejection in the
    # existing direct adapters. Do not infer exhaustion from arbitrary prose.
    match = re.fullmatch(r"(openai|aliyun)_terminal_failure:http_(401|402|403|429)(?::[A-Za-z0-9_.:-]{1,120})?", code)
    native = re.fullmatch(r"native_bridge_terminal_failure:mswar_provider_http_(401|402|403|429)", code)
    if not match and not native:
        return ProviderFault("unknown", "primary_outcome_unknown")
    status = int(match[2] if match else native[1])
    if status == 403:
        # A 403 can be a provider policy denial, not just a broken credential.
        return ProviderFault("blocked", "primary_forbidden_or_policy")
    if status == 401:
        return ProviderFault("auth_invalid", "primary_http_401")
    tail = re.search(r":retry_at_([0-9]{1,15}|invalid)$", code)
    until = int(tail[1]) if tail and tail[1].isdigit() else 0
    kind = "quota_exhausted" if status == 402 or any(x in code.lower() for x in ("insufficient_quota", "billing_hard_limit", "quota_exceeded")) else "rate_limit"
    if not tail or tail[1] == "invalid":
        # Old receipts lost headers. Other independent free providers may run,
        # but this provider stays blocked rather than guessing its reset time.
        return ProviderFault(kind, "primary_retry_metadata_unavailable", retry_at=0)
    return ProviderFault(kind, "primary_http_" + str(status), retry_at=until)


def _private_json(path, limit=65536):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            meta = os.fstat(handle.fileno())
            if (not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o022
                    or meta.st_uid not in {0, os.getuid()} or meta.st_size > limit):
                raise TaskError("free_policy_file_permissions")
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise TaskError("free_policy_size")
        return json.loads(data)
    except TaskError:
        raise
    except (OSError, ValueError, TypeError):
        raise TaskError("free_policy_unreadable") from None


def load_policy(path):
    policy = _private_json(path)
    keys = {"schema", "enabled", "revision", "primary", "providers"}
    if (not isinstance(policy, dict) or set(policy) != keys or policy["schema"] != SCHEMA
            or type(policy["enabled"]) is not bool
            or not isinstance(policy["revision"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", policy["revision"])):
        raise TaskError("free_policy_contract")
    rows = policy["providers"]
    if not isinstance(rows, list) or len(rows) > 2 or any(not isinstance(p, dict) for p in rows):
        raise TaskError("free_policy_providers")
    if len({p.get("id") for p in rows}) != len(rows):
        raise TaskError("free_policy_provider_duplicate")
    for spec in [policy["primary"], *rows]:
        if not isinstance(spec, dict):
            raise TaskError("free_policy_budget")
        required = {"daily_requests", "daily_output_tokens", "min_interval_seconds", "max_tokens"}
        if spec is not policy["primary"]:
            required |= {"id"}
            if spec.get("id") not in FREE_PROVIDERS:
                raise TaskError("free_policy_provider_not_free")
        if set(spec) != required:
            raise TaskError("free_policy_budget_fields")
        for key, ceiling in (("daily_requests", 10000), ("daily_output_tokens", 30000000),
                             ("min_interval_seconds", 86400), ("max_tokens", 3000)):
            if type(spec[key]) is not int or not 1 <= spec[key] <= ceiling:
                raise TaskError("free_policy_budget_value")
        if spec.get("id") == "openrouter_wp" and spec["daily_requests"] > 50:
            raise TaskError("free_policy_openrouter_unproven_quota")
        if spec.get("id") == "ai_horde" and spec["max_tokens"] > 512:
            raise TaskError("free_policy_horde_output_limit")
    return policy


def require_public_grant(values, policy_path, now):
    checksum = public_input_digest(values)
    path = Path(policy_path).parent / "public_grants" / (checksum + ".json")
    try:
        grant = _private_json(path, 8192)
    except TaskError:
        raise ProviderFault("blocked", "public_input_grant_missing") from None
    if (not isinstance(grant, dict)
            or set(grant) != {"schema", "classification", "values_sha256", "approved_by", "expires_at"}
            or grant.get("schema") != "msw.public.input.grant.v1"
            or grant.get("classification") != "public_non_customer"
            or grant.get("values_sha256") != checksum
            or grant.get("approved_by") not in {"owner", "trusted_public_research_admission"}
            or type(grant.get("expires_at")) is not int or grant["expires_at"] <= now):
        raise ProviderFault("blocked", "public_input_grant_invalid")
    # Defense in depth only; the exact local grant is the confidentiality gate.
    if re.search(r"(?i)(?:\b(?:bearer|api[_ -]?key|password)\s*[:=]|\bsk-[A-Za-z0-9]{8,}|[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}|(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w))", values["text"]):
        raise ProviderFault("blocked", "public_input_sensitive_pattern")
    return checksum


class Ledger:
    def __init__(self, directory, clock=time.time):
        self.clock = clock
        root = Path(directory) / "free-fallback"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        self.path = root / "requests.sqlite3"
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, input_sha TEXT NOT NULL, policy_sha TEXT NOT NULL,
                    state TEXT NOT NULL, code TEXT NOT NULL, result_json TEXT, result_sha TEXT);
                CREATE TABLE IF NOT EXISTS budgets (
                    provider TEXT, day TEXT, requests INTEGER NOT NULL, tokens INTEGER NOT NULL,
                    PRIMARY KEY(provider,day));
                CREATE TABLE IF NOT EXISTS gates (
                    provider TEXT PRIMARY KEY, until_at INTEGER NOT NULL DEFAULT 0,
                    last_at REAL NOT NULL DEFAULT 0, blocked TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
                    provider TEXT NOT NULL, state TEXT NOT NULL, code TEXT NOT NULL,
                    at INTEGER NOT NULL, retry_at INTEGER NOT NULL DEFAULT 0);
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def begin(self, request_id, input_sha, policy_sha):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if row:
                if row["input_sha"] != input_sha or row["policy_sha"] != policy_sha:
                    raise TaskError("free_fallback_request_conflict")
                if row["state"] == "completed":
                    if hashlib.sha256(row["result_json"].encode()).hexdigest() != row["result_sha"]:
                        raise TaskError("free_fallback_receipt_integrity")
                    return json.loads(row["result_json"])
                if row["state"] in {"running", "unknown"}:
                    raise TaskError("free_fallback_UNKNOWN_no_reissue")
                raise TaskError(row["code"])
            db.execute("INSERT INTO requests VALUES(?,?,?,'running','',NULL,NULL)", (request_id, input_sha, policy_sha))
        return None

    def finish(self, request_id, state, code, result=None):
        raw = canonical(result).decode() if result is not None else None
        sha = hashlib.sha256(raw.encode()).hexdigest() if raw else None
        with closing(self.connect()) as db, db:
            count = db.execute("UPDATE requests SET state=?,code=?,result_json=?,result_sha=? WHERE id=? AND state='running'",
                               (state, code, raw, sha, request_id)).rowcount
            stored = db.execute("SELECT state,code,result_sha FROM requests WHERE id=?", (request_id,)).fetchone()
            if count != 1 or tuple(stored) != (state, code, sha):
                raise TaskError("free_fallback_receipt_readback")

    def admit(self, request_id, provider, spec, tokens):
        now = self.clock()
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO gates(provider) VALUES(?)", (provider,))
            gate = db.execute("SELECT * FROM gates WHERE provider=?", (provider,)).fetchone()
            if gate["blocked"]:
                return None, "provider_blocked_" + gate["blocked"]
            if gate["until_at"] > now:
                return None, "provider_cooldown"
            if gate["last_at"] and now < gate["last_at"] + spec["min_interval_seconds"]:
                return None, "provider_local_interval"
            db.execute("INSERT OR IGNORE INTO budgets VALUES(?,?,0,0)", (provider, day))
            budget = db.execute("SELECT requests,tokens FROM budgets WHERE provider=? AND day=?", (provider, day)).fetchone()
            if budget["requests"] >= spec["daily_requests"] or budget["tokens"] + tokens > spec["daily_output_tokens"]:
                return None, "provider_daily_budget"
            # Reserve all attempts and their output ceiling, including rejected
            # calls. No refund/extra keys/accounts/models can reset this quota.
            db.execute("UPDATE budgets SET requests=requests+1,tokens=tokens+? WHERE provider=? AND day=?", (tokens, provider, day))
            db.execute("UPDATE gates SET last_at=? WHERE provider=?", (now, provider))
            ticket = db.execute("INSERT INTO attempts(request_id,provider,state,code,at) VALUES(?,?,'in_flight','',?)",
                                (request_id, provider, int(now))).lastrowid
        return ticket, "admitted"

    def attempt_done(self, ticket, provider, fault=None):
        now = self.clock()
        state, code, until = "completed", "", 0
        if fault:
            state, code = fault.kind, fault.code
            default = 86400 if fault.kind == "quota_exhausted" else 60
            until = max(fault.retry_at, math.ceil(now + default))
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("UPDATE attempts SET state=?,code=?,retry_at=? WHERE id=? AND state='in_flight'",
                          (state, code, until, ticket)).rowcount != 1:
                raise TaskError("free_fallback_attempt_readback")
            if fault:
                blocked = "auth_invalid" if fault.kind == "auth_invalid" else ""
                if code == "primary_retry_metadata_unavailable":
                    blocked = "retry_metadata_unavailable"
                if fault.kind == "blocked":
                    blocked = "policy_or_contract"
                if code == "openrouter_native_price_or_answer_unverified":
                    blocked = "price_or_contract"
                if fault.kind == "unknown":
                    until = max(until, math.ceil(now + 900))
                db.execute("UPDATE gates SET until_at=max(until_at,?),blocked=? WHERE provider=?", (until, blocked, provider))


def _primary_group(transport):
    if transport.startswith("direct_aliyun"):
        return "paid:aliyun"
    return "paid:openai"  # direct, native runtime and legacy WP share one account budget.


def _checked_values(values):
    if (not isinstance(values, dict) or set(values) != {"request_id", "task", "text", "max_tokens"}
            or not isinstance(values["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", values["request_id"])
            or values["task"] not in {"technical", "business", "seo", "article", "review", "voice"}
            or not isinstance(values["text"], str) or not 1 <= len(values["text"].encode()) <= 10000
            or type(values["max_tokens"]) is not int or not 64 <= values["max_tokens"] <= 3000):
        raise TaskError("free_fallback_values_contract")


class Router:
    def __init__(self, directory, policy, policy_path, *, transports=None, clock=time.time):
        self.policy, self.policy_path, self.clock = policy, Path(policy_path), clock
        self.ledger = Ledger(directory, clock)
        if transports is None:
            from .free_transports import ai_horde, openrouter_wp
            transports = {"ai_horde": ai_horde, "openrouter_wp": openrouter_wp}
        self.transports = transports

    def run(self, saved, primary):
        values = saved["values"]
        _checked_values(values)
        transport = saved.get("transport", "wordpress_gateway")
        request_id = digest(values["request_id"])
        replay = self.ledger.begin(request_id, digest([transport, values]), digest(self.policy))
        if replay is not None:
            return replay
        events = []

        def attempt(provider, spec, operation, *, free=False):
            tokens = min(values["max_tokens"], spec["max_tokens"]) if free else values["max_tokens"]
            if not free and tokens > spec["max_tokens"]:
                events.append({"provider": provider, "state": "not_sent", "code": "primary_output_budget"})
                return None
            ticket, reason = self.ledger.admit(request_id, provider, spec, tokens)
            if ticket is None:
                events.append({"provider": provider, "state": "not_sent", "code": reason})
                return None
            try:
                answer = operation(tokens)
                if (not isinstance(answer, dict) or answer.get("state", "completed") != "completed"
                        or not isinstance(answer.get("text"), str) or not answer["text"].strip()
                        or not isinstance(answer.get("model"), str) or not answer["model"]):
                    raise ProviderFault("unknown", "invalid_or_pending_answer")
            except Exception as error:
                fault = (error if isinstance(error, ProviderFault) else ProviderFault("unknown", "free_outcome_unknown")) if free else primary_fault(error)
                self.ledger.attempt_done(ticket, provider, fault)
                events.append({"provider": provider, "state": fault.kind, "code": fault.code, "retry_at": fault.retry_at})
                if fault.kind not in SAFE_NEXT:
                    state = "unknown" if fault.kind == "unknown" else "blocked"
                    code = "free_fallback_UNKNOWN_no_reissue" if state == "unknown" else "free_fallback_BLOCKED:" + fault.code
                    self.ledger.finish(request_id, state, code)
                    raise TaskError(code) from None
                return None
            self.ledger.attempt_done(ticket, provider)
            events.append({"provider": provider, "state": "completed", "output_token_ceiling": tokens})
            answer = {**answer, "routing": {"schema": SCHEMA, "policy_revision": self.policy["revision"],
                       "used_free_fallback": free, "paid_fallback_allowed": False,
                       "events": events, "public_input_sha256": public_input_digest(values) if free else None}}
            self.ledger.finish(request_id, "completed", "", answer)
            return answer

        result = attempt(_primary_group(transport), self.policy["primary"], lambda _tokens: primary())
        if result is not None:
            return result
        try:
            require_public_grant(values, self.policy_path, self.clock())
        except ProviderFault as fault:
            code = "free_fallback_BLOCKED:" + fault.code
            self.ledger.finish(request_id, "blocked", code)
            raise TaskError(code) from None
        for spec in self.policy["providers"]:
            provider = spec["id"]
            operation = self.transports.get(provider)
            if operation is None:
                continue
            result = attempt(provider, spec, lambda tokens, operation=operation: operation(values, tokens, public_input_digest(values)), free=True)
            if result is not None:
                return result
        code = "free_fallback_EXHAUSTED_no_paid_retry"
        self.ledger.finish(request_id, "exhausted", code)
        raise TaskError(code)


def maybe_generate(saved, directory, primary):
    policy_path = os.environ.get("MSW_FREE_FALLBACK_POLICY", "")
    if not policy_path:
        path = Path(directory) / "free-fallback" / "requests.sqlite3"
        if path.exists():
            with closing(sqlite3.connect(path)) as db:
                if db.execute("SELECT 1 FROM requests WHERE id=?", (digest(saved["values"]["request_id"]),)).fetchone():
                    raise TaskError("free_fallback_policy_required_for_resume")
        return primary()
    policy = load_policy(policy_path)
    if not policy["enabled"]:
        # Disabled does not unlock an already accepted/uncertain fallback job.
        path = Path(directory) / "free-fallback" / "requests.sqlite3"
        if path.exists():
            with closing(sqlite3.connect(path)) as db:
                if db.execute("SELECT 1 FROM requests WHERE id=?", (digest(saved["values"]["request_id"]),)).fetchone():
                    raise TaskError("free_fallback_disabled_resume_blocked")
        return primary()
    return Router(directory, policy, policy_path).run(saved, primary)
