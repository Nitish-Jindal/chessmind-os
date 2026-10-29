"""Runtime configuration pulled from the environment."""

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-only-not-for-prod"

# libpq-only query params that asyncpg.connect() rejects as unknown kwargs.
_ASYNCPG_DROP_PARAMS = {"channel_binding", "options", "target_session_attrs", "gssencmode"}


def normalize_database_url(url: str, driver: str) -> str:
    """Coerce a hosted Postgres URL (Neon, Render) into a SQLAlchemy URL for ``driver``.

    Hosted providers hand out ``postgres://`` / ``postgresql://`` URLs with
    libpq query params (``sslmode=require&channel_binding=require``). SQLAlchemy
    needs an explicit driver prefix, and asyncpg rejects libpq params it does
    not know: ``sslmode`` is renamed to asyncpg's ``ssl`` and
    ``channel_binding`` is dropped (TLS is still enforced via ``ssl``).
    psycopg goes through libpq, which understands both, so they are kept.
    """
    if driver not in ("asyncpg", "psycopg"):
        raise ValueError(f"unsupported driver {driver!r}")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.split("+", 1)[0]
    if scheme == "postgres":
        scheme = "postgresql"
    if scheme != "postgresql":
        raise ValueError("DATABASE_URL must be a Postgres URL; SQLite is unsupported")
    query = parse_qsl(parts.query, keep_blank_values=True)
    if driver == "asyncpg":
        query = [
            ("ssl" if k == "sslmode" else k, v) for k, v in query if k not in _ASYNCPG_DROP_PARAMS
        ]
    else:
        query = [("sslmode" if k == "ssl" else k, v) for k, v in query]
    return urlunsplit((f"{scheme}+{driver}", parts.netloc, parts.path, urlencode(query), parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://chessmind:chessmind@localhost:5432/chessmind",
        description=(
            "Postgres URL; any of postgres://, postgresql://, postgresql+asyncpg:// is accepted "
            "and normalized to asyncpg. SQLite is unsupported."
        ),
    )
    database_url_sync: str = Field(
        default="",
        description="Sync URL used only by Alembic. Empty = derived from DATABASE_URL (psycopg).",
    )
    jwt_secret: str = Field(default=_DEV_JWT_SECRET)
    jwt_access_ttl_minutes: int = 60
    jwt_refresh_ttl_days: int = 14
    stockfish_path: str = Field(
        default="../engines/stockfish-bin",
        description=(
            "Absolute path, backend-relative path, or bare command name on PATH "
            "for the Stockfish UCI binary."
        ),
    )
    stockfish_default_depth: int = 18
    stockfish_default_multipv: int = 3
    environment: str = Field(default="dev")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated browser origins allowed to call the API (the Vite frontend).",
    )
    cors_origin_regex: str = Field(
        default="",
        description="Optional regex of extra allowed origins (e.g. the Render static-site host).",
    )

    @model_validator(mode="after")
    def _normalize(self) -> "Settings":
        raw = self.database_url
        self.database_url = normalize_database_url(raw, "asyncpg")
        self.database_url_sync = normalize_database_url(self.database_url_sync or raw, "psycopg")
        if self.environment == "production" and self.jwt_secret in ("", _DEV_JWT_SECRET):
            raise ValueError("JWT_SECRET must be set to a real secret when ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
