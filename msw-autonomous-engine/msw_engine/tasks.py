"""Portable compute handlers. They never execute code supplied in a job."""
from __future__ import annotations

from collections import Counter
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener

WORD = re.compile(r"[^\W_]+", re.UNICODE)
STOP = frozenset("oraz jest się nie dla jako przez tego przy czyli które także można jego jej aby the and with this that from are your".split())


class TaskError(RuntimeError):
    pass


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headings, self.images, self.links = Counter(), [], []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag in {"h1", "h2", "h3"}:
            self.headings[tag] += 1
        elif tag == "img":
            self.images.append(bool((attrs.get("alt") or "").strip()))
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])


def seo_analyze(payload: dict, _: Path) -> dict:
    title, content, html = (str(payload.get(key, "")) for key in ("title", "content", "html"))
    if len(content) > 100_000 or len(html) > 140_000:
        raise TaskError("document_limit")
    parser = PageParser()
    parser.feed(html)
    words = WORD.findall(content.casefold())
    counts = Counter(word for word in words if len(word) > 3 and word not in STOP)
    host = urlsplit(str(payload.get("url", ""))).hostname
    internal = sum(urlsplit(urljoin(str(payload.get("url", "")), link)).hostname == host for link in parser.links)
    suggestions = []
    if len(words) < 300:
        suggestions.append({"code": "short_body", "priority": "review", "note": "Oceń, czy tekst odpowiada na całą intencję klienta; sama długość nie przesądza o jakości."})
    if len(title) > 75:
        suggestions.append({"code": "long_title", "priority": "review", "note": "Sprawdź czytelność tytułu i najważniejsze słowa na początku."})
    if html and parser.headings["h2"] == 0:
        suggestions.append({"code": "no_h2", "priority": "review", "note": "Rozważ śródtytuły opisujące konkretne pytania klientów."})
    if parser.images and not all(parser.images):
        suggestions.append({"code": "image_alt_missing", "priority": "fix", "count": parser.images.count(False)})
    return {"word_count": len(words), "title_characters": len(title), "headings": dict(parser.headings),
            "images": len(parser.images), "images_with_alt": sum(parser.images), "internal_links": internal,
            "external_links": len(parser.links) - internal, "terms": [{"term": key, "count": value} for key, value in counts.most_common(20)],
            "suggestions": suggestions, "measurement": "Static content observations; not a search ranking or traffic prediction."}


OWNER_CORPUS = "owner_packages_20261105"


def corpus_directory(payload: dict, data_dir: Path) -> tuple[str, Path]:
    corpus = payload.get("corpus", "approved")
    if corpus == "approved":
        return corpus, data_dir
    if corpus == OWNER_CORPUS and payload.get("owner_authorized") is True:
        return corpus, data_dir / "owner-packages-20261105"
    raise TaskError("unknown_or_unauthorized_knowledge_corpus")


def database(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(data_dir / "knowledge.sqlite3", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,url TEXT NOT NULL,digest TEXT NOT NULL,provenance TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS document_digest ON documents(digest)")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(id UNINDEXED,title,body,tokenize='unicode61 remove_diacritics 2')")
    return db


def knowledge_ingest(payload: dict, data_dir: Path) -> dict:
    corpus, directory = corpus_directory(payload, data_dir)
    records = payload.get("records", [])
    if not isinstance(records, list) or len(records) > 100:
        raise TaskError("record_limit")
    validated, skipped_empty = [], []
    for row in records:
        if not isinstance(row, dict):
            raise TaskError("record_invalid")
        if corpus == OWNER_CORPUS:
            if row.get("approved") is not False or row.get("classification") != "owner_supplied_unverified":
                raise TaskError("owner_source_classification_required")
        elif row.get("approved") is not True:
            raise TaskError("record_not_approved")
        ident = row.get("id", "")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_:.-]{1,200}", ident):
            raise TaskError("record_id_invalid")
        text, title, url = (str(row.get(key, "")) for key in ("text", "title", "url"))
        if len(text) > 100_000 or len(title) > 1000 or len(url) > 2048:
            raise TaskError("record_invalid")
        if not text.strip():
            skipped_empty.append({"id": ident, "reason": "empty_source_body"})
            continue
        if corpus == OWNER_CORPUS and row.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest():
            raise TaskError("owner_source_digest_mismatch")
        digest = hashlib.sha256((title + "\0" + text).encode()).hexdigest()
        validated.append((ident, title, text, url, digest, str(row.get("provenance", "operator_approved"))[:900]))
    db = database(directory)
    stored, unchanged = 0, 0
    try:
        with db:
            for row in validated:
                previous = db.execute("SELECT digest FROM documents WHERE id=?", (row[0],)).fetchone()
                if previous and previous[0] == row[4]:
                    unchanged += 1
                    continue
                db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,url=excluded.url,digest=excluded.digest,provenance=excluded.provenance", row)
                db.execute("DELETE FROM knowledge_fts WHERE id=?", (row[0],))
                db.execute("INSERT INTO knowledge_fts VALUES(?,?,?)", row[:3])
                stored += 1
        # Read each committed digest through a separate connection before acknowledging ingestion.
        with sqlite3.connect(directory / "knowledge.sqlite3", timeout=10) as fresh:
            verified = sum(fresh.execute("SELECT digest FROM documents WHERE id=?", (row[0],)).fetchone() == (row[4],) for row in validated)
            total = fresh.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        if verified != len(validated):
            raise TaskError("knowledge_readback_mismatch")
        return {"stored": stored, "unchanged": unchanged, "accepted_records": len(validated), "readback_verified_records": verified,
                "records_total": total, "corpus": corpus, "classification": "owner_supplied_unverified" if corpus == OWNER_CORPUS else "operator_approved",
                "skipped_empty": skipped_empty, "training_performed": False, "weights_modified": False,
                "purpose": "Persistent source memory and lexical retrieval; ingestion does not verify source claims"}
    finally:
        db.close()


def knowledge_search(payload: dict, data_dir: Path) -> dict:
    corpus = payload.get("corpus", "approved")
    if corpus == "legacy_malta":
        from .legacy_memory import search
        return search(payload, data_dir)
    corpus, directory = corpus_directory(payload, data_dir)
    query = str(payload.get("query", ""))[:2000]
    words = WORD.findall(query.casefold())[:20]
    limit = max(1, min(20, int(payload.get("limit", 5))))
    if not words:
        return {"items": []}
    match = payload.get("match", "any")
    if match not in {"any", "all"}:
        raise TaskError("query_match_invalid")
    expression = (" AND " if match == "all" else " OR ").join('"' + word.replace('"', '') + '"' for word in words)
    db = database(directory)
    try:
        rows = db.execute("SELECT d.id,d.title,d.body,d.url,d.digest,d.provenance,snippet(knowledge_fts,2,'','',' … ',64) AS excerpt,bm25(knowledge_fts) AS score FROM knowledge_fts JOIN documents d ON d.id=knowledge_fts.id WHERE knowledge_fts MATCH ? ORDER BY score LIMIT ?", (expression, limit)).fetchall()
        return {"items": [{"id": row["id"], "title": row["title"], "excerpt": row["excerpt"][:1800], "url": row["url"], "sha256": row["digest"], "provenance": row["provenance"],
                            "classification": "owner_supplied_unverified" if corpus == OWNER_CORPUS else "operator_approved"} for row in rows],
                "corpus": corpus, "records_total": db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "training_performed": False, "measurement": "Lexical BM25 retrieval; source claims are not independently verified"}
    finally:
        db.close()


def research_rank(payload: dict, _: Path) -> dict:
    ideas = payload.get("ideas", [])
    if not isinstance(ideas, list) or len(ideas) > 1000:
        raise TaskError("idea_limit")
    results = []
    for idea in ideas:
        if not isinstance(idea, dict):
            raise TaskError("idea_invalid")
        metrics = {}
        for key in ("demand", "competition", "margin", "delivery_effort"):
            number = idea.get(key)
            metrics[key] = max(0.0, min(100.0, float(number))) if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) else None
        complete = all(value is not None for value in metrics.values())
        evidence = [str(url)[:2048] for url in idea.get("sources", [])[:20] if isinstance(url, str) and url.startswith("https://")]
        score = None
        if complete and evidence:
            score = round(metrics["demand"] * .35 + (100 - metrics["competition"]) * .25 + metrics["margin"] * .25 + (100 - metrics["delivery_effort"]) * .15, 2)
        results.append({"id": str(idea.get("id", ""))[:100], "title": str(idea.get("title", ""))[:300], "score": score,
                        "state": "scored_from_supplied_evidence" if score is not None else "insufficient_evidence", "sources": evidence})
    results.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0), row["id"]))
    return {"ideas": results, "measurement": "Explicit weighted decision rule over supplied metrics; not a profit forecast."}


def llm_generate(payload: dict, _: Path) -> dict:
    from .federation import configured, generate
    if configured():
        return generate(payload, _)
    model = os.environ.get("MSW_LLM_MODEL", "")
    base = os.environ.get("MSW_LLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urlsplit(base)
    if not model or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.scheme not in {"http", "https"}:
        raise TaskError("model_not_configured")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise TaskError("insecure_remote_model_endpoint")
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 16000:
        raise TaskError("prompt_limit")
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "options": {"temperature": .2, "num_predict": min(2048, max(64, int(payload.get("max_tokens", 512))))}}, ensure_ascii=False).encode()
    request = Request(base + "/api/generate", data=body, headers={"Content-Type": "application/json"}, method="POST")
    from .client import NoRedirect
    with build_opener(NoRedirect()).open(request, timeout=180) as response:
        raw = response.read(524289)
    if len(raw) > 524288:
        raise TaskError("model_response_limit")
    result = json.loads(raw)
    if result.get("done") is not True or not isinstance(result.get("response"), str):
        raise TaskError("model_incomplete_result")
    return {"text": result["response"], "model": result.get("model", model), "generated_tokens": result.get("eval_count"), "training_performed": False}


def legacy_root() -> Path:
    return Path(os.environ.get("MSW_LEGACY_ROOT", Path(__file__).resolve().parents[1] / "legacy")).resolve()


def legacy_discovery(payload: dict, _: Path) -> dict:
    corpus = os.environ.get("MSW_DISCOVERY_CORPUS", "")
    if not corpus or not Path(corpus).is_dir():
        raise TaskError("discovery_corpus_not_configured")
    module_dir = legacy_root() / "msw-discovery-analytics-10x/worker"
    sys.path.insert(0, str(module_dir))
    import topic_analytics
    import ingest_snapshot
    manifest = Path(corpus) / '_msw_manifest.sqlite3'
    if not manifest.is_file():
        raise TaskError('discovery_manifest_missing')
    con = ingest_snapshot.connect(manifest)
    try:
        topic_analytics.setup(con)
        return topic_analytics.run(Path(corpus), con, max(1, min(30, int(payload.get("max_shards", 4)))))
    finally:
        con.close()


def legacy_dataset(payload: dict, _: Path) -> dict:
    source, output = os.environ.get("MSW_LEGACY_DATA_DB", ""), os.environ.get("MSW_LEGACY_DATA_OUTPUT", "")
    if not source or not output or not Path(source).is_file():
        raise TaskError("legacy_dataset_not_configured")
    sys.path.insert(0, str(legacy_root() / "msw-contact-pro-360/server"))
    import daily_pipeline
    day = payload.get("day")
    if day is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)):
        raise TaskError("dataset_day_invalid")
    return daily_pipeline.process(source, output, day)


def legacy_train(payload: dict, data_dir: Path) -> dict:
    if os.environ.get("MSW_ENABLE_TRAINING", "") != "1":
        raise TaskError("training_not_enabled")
    mode, dataset = payload.get("mode", "tiny"), payload.get("dataset_id", "")
    modes = os.environ.get("MSW_TRAINING_MODES", "tiny").split(",")
    if mode not in modes or mode not in {"tiny", "lora", "scratch_large"}:
        raise TaskError("training_mode_not_enabled")
    sys.path.insert(0, str(legacy_root() / "msw-portal-combo/tools/llm80"))
    import training
    _, digest = training.load_dataset(dataset)
    if digest != payload.get("dataset_sha256"):
        raise TaskError("approved_dataset_hash_mismatch")
    steps = int(payload.get("steps", 0))
    if not 1 <= steps <= int(os.environ.get("MSW_TRAINING_MAX_STEPS", "100")):
        raise TaskError("training_step_budget")
    run_key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    output = data_dir / "training" / run_key
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    return training.train(mode, steps, dataset, output, lambda _: None)


BASE_HANDLERS = {"seo.analyze": seo_analyze, "knowledge.ingest": knowledge_ingest, "knowledge.search": knowledge_search, "research.rank": research_rank}


def runtime_catalog(payload: dict, data_dir: Path) -> dict:
    from . import __version__
    try:
        status = json.loads((data_dir / "runtime-status.json").read_text())
    except (OSError, ValueError):
        status = {"state": "not_configured", "modules": []}
    from .legacy_memory import status as legacy_status
    result = {"state": status["state"], "worker_version": __version__, "pack_sha256": status.get("pack_sha256"), "legacy_source_memory": legacy_status(data_dir),
            "modules": [{key: item[key] for key in ("id", "version", "source_sha256") if key in item}
                        for item in status.get("modules", [])], "capabilities": list(handlers())}
    if "suite_snapshot" in payload:
        from .suite import analyze
        result["suite"] = analyze(payload["suite_snapshot"])
    from .federation import configured, gateway
    result["federation"] = {"configured": configured(), "provider": os.environ.get("MSW_LLM_PROVIDER", "ollama"),
                            "model": os.environ.get("MSW_LLM_MODEL", ""), "provider_credentials_retained_in_wordpress": True}
    if payload.get("include_network") is True:
        result["network"] = gateway("snapshot")
    return result


def supreme_observe(payload: dict, data_dir: Path) -> dict:
    from .runtime_pack import module_root
    root = module_root("supreme-server")
    if root is None:
        raise TaskError("supreme_module_not_installed")
    if payload.get("origin") != "https://mobilny-serwis-warszawa.pl" or payload.get("provenance") != "authenticated_wordpress_inventory":
        raise TaskError("supreme_inventory_provenance")
    inventory = payload.get("inventory")
    if not isinstance(inventory, dict) or set(inventory) != {"installed", "active", "inactive"}:
        raise TaskError("supreme_inventory_schema")
    if any(type(value) is not int or not 0 <= value <= 100_000 for value in inventory.values()):
        raise TaskError("supreme_inventory_bounds")
    if inventory["installed"] != inventory["active"] + inventory["inactive"]:
        raise TaskError("supreme_inventory_total")
    import importlib.util
    spec = importlib.util.spec_from_file_location("msw_private_supreme_store", root / "msw_v5_client/msw_master_engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = data_dir / "supreme"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    observation = {"observed_status": "authenticated_snapshot_received", "plugin_count": inventory["installed"],
                   "active_count": inventory["active"], "inactive_count": inventory["inactive"], "native_v5_ack": False,
                   "source_sha256": source_digest}
    db = module.Store(state / "msw-supreme-state.sqlite3")
    try:
        existing = db.search("msw-main", "unified-engine", source_digest, limit=1)
        ident = existing[0]["id"] if existing and existing[0]["payload"] == observation else db.remember(
            "msw-main", "unified-engine", "wp_status_observation", observation, {"source_sha256": source_digest}, ttl=86400)
        found = db.search("msw-main", "unified-engine", source_digest, limit=25)
        if not any(item["id"] == ident for item in found):
            raise TaskError("supreme_durable_readback_failed")
    finally:
        db.close()
    return {"module": "supreme-server", "module_version": "3.0.0", "record_id": ident, "source_sha256": source_digest,
            "durable_local_readback_verified": True, "inventory": inventory, "transport": "scoped_unified_queue",
            "v5_native_ack": False, "v5_signed_pairing": False}


def handlers() -> dict:
    result = dict(BASE_HANDLERS)
    result["runtime.catalog"] = runtime_catalog
    if os.environ.get('MSW_RUNTIME_MODE') == 'render-free':
        return {kind: result[kind] for kind in ('seo.analyze', 'research.rank')}
    from .runtime_pack import module_root
    if module_root("supreme-server") is not None:
        result["supreme.observe"] = supreme_observe
    from .private_adapters import available_handlers
    result.update(available_handlers())
    from .own_model import available_handlers as own_handlers
    if os.environ.get("MSW_LLM_MODEL") or own_handlers():
        result["llm.generate"] = llm_dispatch
    if os.environ.get("MSW_DISCOVERY_CORPUS"):
        result["legacy.discovery.topics"] = legacy_discovery
    if os.environ.get("MSW_LEGACY_DATA_DB") and importlib.util.find_spec("pyarrow"):
        result["legacy.dataset.prepare"] = legacy_dataset
    if os.environ.get("MSW_ENABLE_TRAINING") == "1" and importlib.util.find_spec("torch"):
        result["legacy.llm80.train"] = legacy_train
    return result


def llm_dispatch(payload: dict, data_dir: Path) -> dict:
    if payload.get('schema') == 'msw.own-model.job.v1':
        from .own_model import handle
        return handle(payload, data_dir)
    from .federation import configured
    if not os.environ.get('MSW_LLM_MODEL') and not configured():
        raise TaskError('legacy_model_not_configured_no_silent_substitution')
    return llm_generate(payload, data_dir)


def dispatch(kind: str, payload: dict, data_dir: Path) -> dict:
    handler = handlers().get(kind)
    if handler is None:
        raise TaskError("unsupported_or_unconfigured_capability")
    return handler(payload, data_dir)

