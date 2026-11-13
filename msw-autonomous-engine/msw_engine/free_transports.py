"""Fixed, free-only candidates. No credential export or generic URL input."""
from __future__ import annotations

import hashlib
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPSHandler, build_opener

from .client import NoRedirect
from .free_fallback import ProviderFault, canonical, retry_after_at
from .recovery import OriginPaused, protected_open

HORDE_URL = "https://oai.aihorde.net/v1/chat/completions"
HORDE_MODEL = "koboldcpp/Phi-3.5-mini-instruct"


def _http_fault(status, retry_header=None):
    until = retry_after_at(retry_header)
    if until is None:
        return ProviderFault("blocked", "provider_retry_after_invalid")
    if status == 401:
        return ProviderFault("auth_invalid", "provider_http_401", retry_at=until)
    if status == 402:
        return ProviderFault("quota_exhausted", "provider_http_402", retry_at=until)
    if status == 429:
        return ProviderFault("rate_limit", "provider_http_429", retry_at=until)
    if status >= 500 or status in {408, 409}:
        return ProviderFault("unknown", "provider_http_" + str(status), retry_at=until)
    # A safety/policy rejection or invalid request is never a reason to shop
    # for a more permissive model. Redirects also fail instead of forwarding.
    return ProviderFault("blocked", "provider_http_" + str(status), retry_at=until)


def ai_horde(values, max_tokens, public_digest):
    if not 1 <= max_tokens <= 512:
        raise ProviderFault("blocked", "horde_output_budget")
    body = {"model": HORDE_MODEL,
            "messages": [{"role": "user", "content": values["text"]}],
            "max_tokens": max_tokens, "stream": False, "temperature": 0}
    request = Request(HORDE_URL, data=canonical(body), method="POST", headers={
        # This literal is the documented public anonymous identifier, not an
        # MSW secret or a registered account rotated to bypass limits.
        "Authorization": "Bearer 0000000000", "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "MSW-Hermes-FreeFallback/1.0 (+https://mobilny-serwis-warszawa.pl/)"})
    try:
        opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        with protected_open(opener, request, timeout=45) as response:
            raw = response.read(131073)
        if len(raw) > 131072:
            raise ProviderFault("unknown", "horde_response_limit")
        answer = json.loads(raw)
    except OriginPaused as error:
        import time
        raise ProviderFault("not_sent", "horde_origin_circuit_open", retry_at=int(time.time()) + error.retry_after) from None
    except HTTPError as error:
        raise _http_fault(error.code, error.headers.get("Retry-After") if error.headers else None) from None
    except (URLError, TimeoutError, OSError, ValueError):
        # The sync chat endpoint does not promise idempotency. A lost response
        # may be accepted by a worker; no second POST or next-provider call.
        raise ProviderFault("unknown", "horde_transport_unknown") from None
    choices = answer.get("choices") if isinstance(answer, dict) else None
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
    message = choice.get("message") if isinstance(choice, dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    if (not isinstance(answer, dict) or answer.get("model") != HORDE_MODEL or not isinstance(text, str)
            or not text.strip() or len(text.encode()) > 100000 or message.get("tool_calls")):
        raise ProviderFault("unknown", "horde_response_contract")
    if choice.get("finish_reason") != "stop":
        # The observed 16-token smoke response ended in length. It proved
        # inference, but is not evidence of a complete production answer.
        raise ProviderFault("blocked", "horde_incomplete_or_policy_response")
    return {"state": "completed", "text": text, "model": HORDE_MODEL,
            "provider": "ai_horde_anonymous", "answer_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "api_text_verified": True, "cost_usd": "0", "public_input_sha256": public_digest,
            "model_artifact_attested": False, "quality_review_required": True}


def openrouter_wp(values, max_tokens, public_digest):
    """Consumes a versioned extension of the existing signed native callback.

    That extension is absent from the observed WP 1.0.3. Capability negotiation
    deliberately returns NOT_SENT now; it must never call a paid default model.
    """
    from .runtime_bridge import call
    try:
        status = call("status")
    except Exception:
        raise ProviderFault("not_sent", "openrouter_native_status_unavailable") from None
    capability = status.get("free_fallback", {}) if isinstance(status, dict) else {}
    spec = capability.get("providers", {}).get("openrouter_free", {}) if isinstance(capability, dict) else {}
    if (capability.get("schema") != "msw.free.callback.v1" or capability.get("enabled") is not True
            or spec.get("configured") is not True or spec.get("cost_ceiling_usd") != "0"
            or spec.get("public_input_grants") is not True or spec.get("durable_receipts") is not True
            or spec.get("retry_after") is not True):
        raise ProviderFault("not_sent", "openrouter_native_free_contract_absent")
    try:
        result = call("generate", {**values, "max_tokens": max_tokens,
                     "provider": "openrouter_free", "public_input_sha256": public_digest})
    except Exception:
        raise ProviderFault("unknown", "openrouter_native_submit_unknown") from None
    if (not isinstance(result, dict) or result.get("request_id") != values["request_id"]
            or result.get("public_input_sha256") != public_digest
            or result.get("storage_readback_verified") is not True):
        raise ProviderFault("unknown", "openrouter_native_receipt_contract")
    state = result.get("state")
    if state == "provider_not_sent":
        raise ProviderFault("not_sent", "openrouter_native_not_sent", retry_at=result.get("retry_at", 0))
    if state == "provider_failed" and result.get("accepted") is False:
        kind = result.get("failure_kind")
        if kind in {"rate_limit", "quota_exhausted", "auth_invalid"}:
            raise ProviderFault(kind, "openrouter_native_" + kind, retry_at=result.get("retry_at", 0))
        raise ProviderFault("blocked", "openrouter_native_rejection")
    if state != "completed":
        raise ProviderFault("unknown", "openrouter_native_outcome_unknown")
    text = result.get("text")
    if (not isinstance(text, str) or not text.strip() or len(text.encode()) > 100000
            or result.get("answer_sha256") != hashlib.sha256(text.encode()).hexdigest()
            or result.get("cost_usd") != "0" or result.get("free_model_verified") is not True
            or result.get("paid_fallback_allowed") is not False
            or not isinstance(result.get("model"), str) or not result["model"]):
        raise ProviderFault("unknown", "openrouter_native_price_or_answer_unverified")
    return {**result, "provider": "openrouter_free_via_existing_wordpress", "quality_review_required": True}
