"""Bounded operational analysis of authenticated MSW plugin metadata.

No customer content, source code, credentials, remote calls or mutations.
The input is an observed snapshot, never a claim of successful publication.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re
import time

from .tasks import TaskError


def analyze(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "msw.suite.snapshot.v1" or snapshot.get("provenance") != "authenticated_wordpress_plugin_metadata":
        raise TaskError("suite_snapshot_provenance")
    origin = snapshot.get("origin")
    allowed = {"https://mobilny-serwis-warszawa.pl", "https://e-deweloperski.pl", "https://naprawasauny.pl", "https://slusarz-zamki.pl", "https://slusarz-warszawa-premium.com", "https://zamki-szyfrowe.pl"}
    if origin not in allowed:
        raise TaskError("suite_origin_not_allowed")
    observed = snapshot.get("observed_at")
    if type(observed) is not int or observed < 1 or observed > time.time() + 60:
        raise TaskError("suite_observation_time")
    modules = snapshot.get("modules")
    if not isinstance(modules, list) or len(modules) > 200:
        raise TaskError("suite_module_limit")
    issues, counts, seen = [], Counter(), set()
    for row in modules:
        if not isinstance(row, dict):
            raise TaskError("suite_module_schema")
        ident = row.get("plugin")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9_-]+/[a-zA-Z0-9_.-]+\.php", ident) or ident in seen:
            raise TaskError("suite_plugin_identifier")
        seen.add(ident)
        if type(row.get("installed")) is not bool or type(row.get("active")) is not bool:
            raise TaskError("suite_module_flags")
        if not row["installed"]:
            counts["not_installed"] += 1
            continue
        counts["installed"] += 1
        if not row["active"]:
            counts["intentionally_inactive_or_unverified"] += 1
            continue
        counts["active"] += 1
        if row.get("contract_version") == "1.0.0":
            counts["diagnostics_integrated"] += 1
        else:
            issues.append({"plugin": ident, "code": "upgrade_contract_missing", "severity": "review"})
        for dependency in row.get("missing_dependencies", []):
            if not isinstance(dependency, str) or not re.fullmatch(r"[a-z0-9_-]{1,100}", dependency):
                raise TaskError("suite_dependency_schema")
            issues.append({"plugin": ident, "code": "dependency_inactive", "severity": "high", "dependency": dependency})
        if row.get("php_compatible") is False or row.get("wordpress_compatible") is False:
            issues.append({"plugin": ident, "code": "runtime_incompatible", "severity": "high"})
        for item in row.get("cron", []):
            if not isinstance(item, dict) or len(str(item.get("hook", ""))) > 120:
                raise TaskError("suite_cron_schema")
            timestamp = item.get("next_at")
            if type(timestamp) is int and timestamp < observed - 900:
                issues.append({"plugin": ident, "code": "cron_overdue", "severity": "high", "hook": item["hook"], "overdue_seconds": observed - timestamp})
        state = row.get("state", {})
        if not isinstance(state, dict):
            raise TaskError("suite_state_schema")
        if state.get("enabled") is False:
            counts["paused"] += 1
        if state.get("error_present") is True:
            issues.append({"plugin": ident, "code": "recorded_error_requires_review", "severity": "review"})
        age = row.get("lock_age_seconds")
        if type(age) is int and age > 1800:
            issues.append({"plugin": ident, "code": "long_running_or_stale_lock", "severity": "review", "age_seconds": age, "automatic_deletion": False})
    stale = time.time() - observed > 900
    if stale:
        issues.append({"code": "snapshot_stale", "severity": "review"})
    issues.sort(key=lambda item: 0 if item["severity"] == "high" else 1)
    return {"schema": "msw.suite.analysis.v1", "origin": origin, "observed_at": observed,
            "analyzed_at": datetime.now(timezone.utc).isoformat(), "snapshot_stale": stale,
            "counts": dict(counts), "issues": issues[:200], "issue_count": len(issues),
            "execution_verified": False, "publication_verified": False,
            "actions_performed": [], "purpose": "Operational checks of installed plugin metadata, scheduling and dependencies"}
