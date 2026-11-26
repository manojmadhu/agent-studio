"""Operator CLI (owned by BE1).

    python -m app.cli create-api-key --workspace ws_default --name "Portal dev" --scopes admin [--expires-in-days 90]
    python -m app.cli dev-token --sub usr_admin --role admin [--workspace ws_default] [--ttl 3600]

`create-api-key` prints the secret once (only its sha256 is stored).
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta


def _create_api_key(args: argparse.Namespace) -> int:
    from app.core.security import ALL_SCOPES, api_key_prefix, hash_api_key, new_api_key_secret
    from app.core.time import utcnow
    from app.db.models import ApiKey, Workspace
    from app.db.session import session_scope
    from app.services import audit

    scopes = [s.strip() for s in args.scopes.replace(" ", ",").split(",") if s.strip()]
    bad = [s for s in scopes if s not in ALL_SCOPES]
    if not scopes or bad:
        print(f"error: scopes must be a comma-separated subset of {', '.join(sorted(ALL_SCOPES))}", file=sys.stderr)
        return 2
    secret = new_api_key_secret(live=args.live)
    with session_scope() as db:
        if db.get(Workspace, args.workspace) is None:
            print(f"error: workspace {args.workspace} does not exist", file=sys.stderr)
            return 1
        now = utcnow()
        key = ApiKey(workspace_id=args.workspace, name=args.name, prefix=api_key_prefix(secret),
                     key_hash=hash_api_key(secret), scopes=scopes, created_by="cli",
                     expires_at=now + timedelta(days=args.expires_in_days) if args.expires_in_days else None,
                     created_at=now, updated_at=now)
        db.add(key)
        db.flush()
        audit.record(db, None, "api_key.created", "api_key", key.id, workspace_id=args.workspace,
                     summary=f"Created API key {args.name} (CLI)", details={"scopes": scopes})
        key_id = key.id
    print(f"id:     {key_id}")
    print(f"scopes: {','.join(scopes)}")
    print(f"secret: {secret}")
    print("Store the secret now — it is shown only once.", file=sys.stderr)
    return 0


def _dev_token(args: argparse.Namespace) -> int:
    from app.core.security import issue_dev_token

    print(issue_dev_token(args.sub, ws=args.workspace, role=args.role, email=args.email, name=args.name, ttl_s=args.ttl))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("create-api-key", help="Create a workspace API key and print its secret once")
    k.add_argument("--workspace", default="ws_default")
    k.add_argument("--name", required=True)
    k.add_argument("--scopes", required=True, help="comma-separated, e.g. admin or ingest:write,runs:read")
    k.add_argument("--expires-in-days", type=int, default=None)
    k.add_argument("--live", action="store_true", help="dik_live_ prefix (default: per DI_ENV)", default=None)
    k.set_defaults(fn=_create_api_key)
    t = sub.add_parser("dev-token", help="Print an HS256 dev bearer token (dev_auth must be on)")
    t.add_argument("--sub", default="usr_admin")
    t.add_argument("--workspace", default="ws_default")
    t.add_argument("--role", default="admin", choices=["admin", "builder", "reviewer", "viewer"])
    t.add_argument("--email", default=None)
    t.add_argument("--name", default=None)
    t.add_argument("--ttl", type=int, default=3600)
    t.set_defaults(fn=_dev_token)
    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
