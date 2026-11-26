"""SecretStore — write-only credential material (owned by BE1).

    from app.services.secret_store import get_secret_store, hint
    store = get_secret_store()
    ref = store.put(db, workspace_id, {"type": "api_key", "key": "sk-…"}, purpose="connection")
    auth = store.get(db, ref)              # → dict (decrypted)
    ref = store.rotate(db, ref, new_payload)
    store.delete(db, ref)
    hint("sk-abcdRk3w")                    # → "••••Rk3w"

Refs are opaque strings ``"<backend>:<id>"`` (``local:sec_…``) stored in ``connections.secret_ref``,
``webhooks.secret_ref``, ``pipelines.ingest_secret_ref``. None of these methods commit — the caller's transaction
does. Plaintext never leaves this module except as the return value of ``get``; never log or audit it.

Backends: ``local`` (implemented) — Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from
``settings.secret_key`` via SHA-256, rows in the ``secrets`` table. ``azure_key_vault``, ``aws_secrets_manager``,
``hashicorp_vault`` are documented stubs: implement :class:`SecretStore` and register in ``_BACKENDS``.
"""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.ids import new_id

MASK = "••••"


def hint(value: Any) -> str | None:
    """`••••` + last four characters (None for empty values)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return MASK + s[-4:]


class SecretStore(Protocol):
    name: str

    def put(self, db: Session, workspace_id: str, payload: dict, *, purpose: str | None = None) -> str: ...

    def get(self, db: Session, ref: str) -> dict: ...

    def rotate(self, db: Session, ref: str, payload: dict) -> str: ...

    def delete(self, db: Session, ref: str) -> None: ...


def _fernet_key(secret_key: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(("docintel-secret-store:" + secret_key).encode()).digest())


def _key_id(secret_key: str) -> str:
    return hashlib.sha256(("kid:" + secret_key).encode()).hexdigest()[:16]


class LocalSecretStore:
    """Fernet-encrypted rows in the `secrets` table."""

    name = "local"

    def __init__(self, secret_key: str | None = None) -> None:
        sk = secret_key or get_settings().secret_key
        self._fernet = Fernet(_fernet_key(sk))
        self._kid = _key_id(sk)

    def _encrypt(self, payload: dict) -> str:
        return self._fernet.encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()

    def _decrypt(self, token: str) -> dict:
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken as exc:
            raise KeyError("Secret cannot be decrypted with the current master key") from exc

    @staticmethod
    def _id(ref: str) -> str:
        backend, _, sid = ref.partition(":")
        if backend != "local" or not sid:
            raise KeyError(f"Not a local secret ref: {ref[:20]}")
        return sid

    def put(self, db: Session, workspace_id: str, payload: dict, *, purpose: str | None = None) -> str:
        from app.db.models import Secret

        row = Secret(id=new_id("sec"), workspace_id=workspace_id, purpose=purpose, ciphertext=self._encrypt(payload),
                     version=1, key_id=self._kid)
        db.add(row)
        db.flush()
        return f"local:{row.id}"

    def get(self, db: Session, ref: str) -> dict:
        from app.db.models import Secret

        row = db.get(Secret, self._id(ref))
        if row is None:
            raise KeyError("Secret not found")
        return self._decrypt(row.ciphertext)

    def rotate(self, db: Session, ref: str, payload: dict) -> str:
        """Replace the material in place (version + 1); the ref stays the same."""
        from app.db.models import Secret

        row = db.get(Secret, self._id(ref))
        if row is None:
            raise KeyError("Secret not found")
        row.ciphertext = self._encrypt(payload)
        row.version = (row.version or 1) + 1
        row.key_id = self._kid
        db.flush()
        return ref

    def delete(self, db: Session, ref: str) -> None:
        from app.db.models import Secret

        try:
            row = db.get(Secret, self._id(ref))
        except KeyError:
            return
        if row is not None:
            db.delete(row)
            db.flush()


class _VaultStub:
    """Placeholder for external vaults. Implement put/get/rotate/delete against the vault API and keep refs
    as ``<name>:<vault-path-or-id>``. Not available in this build."""

    def __init__(self, name: str) -> None:
        self.name = name

    def _na(self, *_: Any, **__: Any) -> Any:
        raise NotImplementedError(f"Secret store backend {self.name!r} is not implemented yet")

    put = get = rotate = delete = _na


_BACKENDS = {
    "local": LocalSecretStore,
    "azure_key_vault": lambda: _VaultStub("azure_key_vault"),      # azure-keyvault-secrets: set/get/begin_delete
    "aws_secrets_manager": lambda: _VaultStub("aws_secrets_manager"),  # create/get/put_secret_value/delete
    "hashicorp_vault": lambda: _VaultStub("hashicorp_vault"),      # KV v2: /v1/<mount>/data/<path>
}


@lru_cache
def _store(name: str, secret_key: str) -> Any:
    factory = _BACKENDS.get(name)
    if factory is None:
        raise ValueError(f"Unknown secret store {name!r}")
    return factory()


def get_secret_store() -> SecretStore:
    s = get_settings()
    return _store(s.secret_store, s.secret_key)


def store_for_ref(ref: str) -> SecretStore:
    """The backend that owns `ref` (refs carry their backend name)."""
    backend = ref.partition(":")[0]
    if backend == "local":
        return _store("local", get_settings().secret_key)
    return _store(backend, get_settings().secret_key)


# Module-level convenience API (used by app.db.seed and routers).

def put(db: Session, workspace_id: str, payload: dict, *, purpose: str | None = None) -> str:
    return get_secret_store().put(db, workspace_id, payload, purpose=purpose)


def get(db: Session, ref: str) -> dict:
    return store_for_ref(ref).get(db, ref)


def rotate(db: Session, ref: str, payload: dict) -> str:
    return store_for_ref(ref).rotate(db, ref, payload)


def delete(db: Session, ref: str) -> None:
    store_for_ref(ref).delete(db, ref)


__all__ = ["SecretStore", "LocalSecretStore", "get_secret_store", "store_for_ref", "hint", "MASK", "put", "get",
           "rotate", "delete"]
