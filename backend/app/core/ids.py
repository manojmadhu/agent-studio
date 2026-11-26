"""Prefixed opaque IDs.

IDs are lowercase base36 so they stay unique under case-insensitive collations (MSSQL default
SQL_Latin1_General_CP1_CI_AS). Seeded demo IDs ported from the portal may contain capitals; that is
fine as long as no two differ only by case.
"""
from __future__ import annotations

import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits

PREFIXES = {
    "workspace": "ws", "member": "usr", "api_key": "key", "pipeline": "pl", "version": "plv", "template": "tpl",
    "document": "doc", "run": "run", "run_step": "rst", "review_item": "rv", "connection": "con", "provider": "prv",
    "worker": "wrk", "mcp_server": "mcp", "knowledge_collection": "kc", "skill": "skl", "policy": "pol",
    "webhook": "whk", "webhook_delivery": "whd", "audit_event": "aud", "event": "evt", "secret": "sec",
}


def new_id(prefix: str, length: int = 12) -> str:
    """new_id("con") -> "con_k3v9x0q1z2ab". Accepts a raw prefix or a key of PREFIXES."""
    p = PREFIXES.get(prefix, prefix)
    return f"{p}_" + "".join(secrets.choice(_ALPHABET) for _ in range(length))


def new_secret(prefix: str = "", nbytes: int = 32) -> str:
    """URL-safe random secret, e.g. new_secret("dik_live_")."""
    return prefix + secrets.token_urlsafe(nbytes)
