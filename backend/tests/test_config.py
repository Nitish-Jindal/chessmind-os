"""DATABASE_URL normalization for hosted Postgres (Neon / Render)."""

from __future__ import annotations

import pytest

from chessmind.config import Settings, normalize_database_url

NEON = (
    "postgresql://u:p@ep-x-123.ap-southeast-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def test_asyncpg_renames_sslmode_and_drops_channel_binding() -> None:
    assert normalize_database_url(NEON, "asyncpg") == (
        "postgresql+asyncpg://u:p@ep-x-123.ap-southeast-1.aws.neon.tech/neondb?ssl=require"
    )


def test_psycopg_keeps_libpq_params() -> None:
    assert normalize_database_url(NEON, "psycopg") == (
        "postgresql+psycopg://u:p@ep-x-123.ap-southeast-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@h:5432/db",
        "postgresql://u:p@h:5432/db",
        "postgresql+asyncpg://u:p@h:5432/db",
        "postgresql+psycopg://u:p@h:5432/db",
    ],
)
def test_driver_prefix_is_rewritten(url: str) -> None:
    assert normalize_database_url(url, "asyncpg") == "postgresql+asyncpg://u:p@h:5432/db"
    assert normalize_database_url(url, "psycopg") == "postgresql+psycopg://u:p@h:5432/db"


def test_sqlite_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_database_url("sqlite:///x.db", "asyncpg")


def test_sync_url_derived_from_database_url() -> None:
    s = Settings(database_url=NEON, database_url_sync="")
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert "channel_binding" not in s.database_url
    assert s.database_url_sync.startswith("postgresql+psycopg://")
    assert "channel_binding=require" in s.database_url_sync


def test_production_requires_real_jwt_secret() -> None:
    with pytest.raises(ValueError):
        Settings(environment="production", jwt_secret="dev-only-not-for-prod")
    Settings(environment="production", jwt_secret="a-real-secret")


@pytest.mark.parametrize("field,value", [
    ("stockfish_threads", 0), ("stockfish_hash_mb", -1),
    ("stockfish_interactive_depth", 0), ("stockfish_background_depth", 41),
    ("stockfish_interactive_multipv", 0), ("stockfish_background_multipv", 11),
    ("stockfish_interactive_timeout_sec", 0), ("stockfish_background_timeout_sec", -1),
    ("stockfish_engine_startup_timeout_sec", float("inf")),
    ("stockfish_background_queue_max", 0), ("stockfish_interactive_queue_timeout_sec", 0),
])
def test_invalid_engine_settings_rejected(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})
