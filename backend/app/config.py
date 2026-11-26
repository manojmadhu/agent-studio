"""Runtime configuration (environment variables prefixed DI_)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DI_", env_file=".env", extra="ignore")

    env: str = "development"  # development | test | production

    # PostgreSQL: postgresql+psycopg://user:pass@host:5432/db
    # MSSQL:      mssql+pyodbc://user:pass@host:1433/db?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
    database_url: str = "postgresql+psycopg://di:di@localhost:5432/docintel"
    db_echo: bool = False
    db_pool_size: int = 10

    # Master key used by the local SecretStore (Fernet) and to sign short-lived tokens (sse_token, oauth state).
    # MUST be overridden in production. Any string works; it is stretched with SHA-256.
    secret_key: str = "dev-only-change-me"
    secret_store: str = "local"  # local | (future) azure_key_vault | aws_secrets_manager | hashicorp_vault

    cors_origins: str = "http://localhost:5173,http://localhost:4173,http://127.0.0.1:5173,http://127.0.0.1:4173"
    cors_allow_null_origin: bool = False  # dev only: the sandboxed preview iframe sends `Origin: null`

    storage_dir: str = "./var/storage"  # local object store for documents (S3-compatible store later)
    max_upload_mb: int = 50

    # Auth
    bootstrap_api_key: str | None = None  # if set, seeded as an admin key for the default workspace
    dev_auth: bool = True                 # accept HS256 dev bearer tokens signed with secret_key (never in production)
    # Local development only: a request with NO credential is treated as the seeded admin (usr_admin of
    # ws_default), so the portal's live mode works before a key is entered. Ignored when env=production.
    dev_anonymous_admin: bool = False
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None

    # Runs
    run_mode: str = "simulate"  # simulate (mirror portal mock engine) | live (call workers/providers)
    run_step_delay_ms: int = 350  # pacing of simulated steps so the portal run panel animates like the mock

    seed_on_startup: bool = True  # seed the demo workspace when the DB is empty

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if self.cors_allow_null_origin and not self.is_production:
            origins.append("null")
        return origins

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
