"""Prevent personal Alibaba Token Plan credentials from entering backend inference.

Personal subscriptions are for interactive coding/agent tools. This check
does not authenticate the key or activate billing; it only refuses known
plan-specific credential shapes before any network or paid request.
"""
from __future__ import annotations


def personal_plan_key(value: str) -> bool:
    """Return only a boolean; never store or log a credential."""
    if not isinstance(value, str):
        return False
    key = value.strip()
    return key.startswith(("sk-sp-", "o1_"))
