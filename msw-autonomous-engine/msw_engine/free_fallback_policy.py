"""MSW/Hermes free-tier fallback decision engine (offline policy only).

Never invokes an API or reads credentials. The existing authenticated execution
engine is responsible for sending requests, persisting receipts, quota ledgers,
readbacks, and enforcing provider terms. Do not automatically deploy this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Literal, Mapping, Optional, Sequence

PreviousState = Literal[
    "new", "confirmed_quota_exceeded", "confirmed_budget_exhausted",
    "confirmed_not_executed", "in_flight", "accepted", "uncertain",
    "completed",
]

@dataclass(frozen=True)
class Provider:
    provider_id: str
    model: str
    auth: Literal["anonymous", "existing_key", "local"]
    requires_existing_key: bool
    zero_price_verified: bool
    terms_verified: bool
    endpoint_verified: bool
    inference_verified: bool
    enabled_by_owner: bool
    private_data_allowed: bool
    capabilities: FrozenSet[str]
    requests_per_day: Optional[int] = None
    requests_per_minute: Optional[int] = None
    priority: int = 100

@dataclass(frozen=True)
class Meter:
    day_count: int = 0
    minute_count: int = 0
    cooldown_until_epoch: int = 0
    credential_available: bool = False

@dataclass(frozen=True)
class Decision:
    action: Literal["execute_once", "hold", "already_complete"]
    provider_id: Optional[str]
    model: Optional[str]
    reason: str

SAFE_SWITCH_STATES = frozenset({
    "new", "confirmed_quota_exceeded", "confirmed_budget_exhausted", "confirmed_not_executed"
})
HOLD_STATES = frozenset({"in_flight", "accepted", "uncertain"})

def choose_free_provider(
    providers: Sequence[Provider],
    meters: Mapping[str, Meter],
    *,
    now_epoch: int,
    previous_state: PreviousState,
    required_capabilities: FrozenSet[str],
    contains_private_data: bool,
    free_only: bool = True,
    excluded_provider_ids: FrozenSet[str] = frozenset(),
) -> Decision:
    """Plan a single zero-price request; no HTTP call, reservation or quota write.

    The caller MUST atomically reserve one request ID and persist quota changes.
    Never duplicate uncertain accepted provider requests.
    """
    if previous_state == "completed":
        return Decision("already_complete", None, None, "verified_previous_completion")
    if previous_state in HOLD_STATES:
        return Decision("hold", None, None, "uncertain_effect_requires_receipt")
    if previous_state not in SAFE_SWITCH_STATES:
        return Decision("hold", None, None, "unknown_previous_state")
    if not free_only:
        return Decision("hold", None, None, "free_only_policy_required")
    if not isinstance(now_epoch, int) or now_epoch < 0:
        return Decision("hold", None, None, "invalid_clock")
    if not required_capabilities:
        return Decision("hold", None, None, "capabilities_required")
    # A failed provider MUST be excluded even when quota counters have not synced.
    # Without identifying it we cannot safely rotate after a known 429 / budget refusal.
    if (previous_state in {"confirmed_quota_exceeded", "confirmed_budget_exhausted"}
            and not excluded_provider_ids):
        return Decision("hold", None, None, "failed_provider_identity_required")

    for candidate in sorted(providers, key=lambda item: (item.priority, item.provider_id)):
        if candidate.provider_id in excluded_provider_ids:
            continue
        # A mistaken auth flag must never allow an authenticated model through.
        if (candidate.auth == "existing_key") != candidate.requires_existing_key:
            continue
        if candidate.auth not in {"anonymous", "existing_key", "local"}:
            continue
        if not (candidate.enabled_by_owner and candidate.zero_price_verified
                and candidate.terms_verified and candidate.endpoint_verified
                and candidate.inference_verified):
            continue
        if not required_capabilities.issubset(candidate.capabilities):
            continue
        if contains_private_data and not candidate.private_data_allowed:
            continue
        meter = meters.get(candidate.provider_id, Meter())
        if candidate.requires_existing_key and not meter.credential_available:
            continue
        if meter.cooldown_until_epoch > now_epoch:
            continue
        if candidate.requests_per_day is not None and meter.day_count >= candidate.requests_per_day:
            continue
        if candidate.requests_per_minute is not None and meter.minute_count >= candidate.requests_per_minute:
            continue
        return Decision("execute_once", candidate.provider_id, candidate.model, "eligible_zero_cost_model")
    return Decision("hold", None, None, "no_verified_free_provider_available")
