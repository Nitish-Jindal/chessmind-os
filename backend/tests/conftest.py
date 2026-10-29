"""Shared pytest fixtures.

Tests use a dedicated Postgres database (``chessmind_test``). The schema
is dropped and re-created from SQLAlchemy metadata at the start of each
test session.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://chessmind:chessmind@localhost:5432/chessmind_test",
)
os.environ.setdefault(
    "DATABASE_URL_SYNC",
    "postgresql+psycopg://chessmind:chessmind@localhost:5432/chessmind_test",
)
# Tests never reach Lichess; explorer tests inject a transport serving tests/fixtures/.
os.environ.setdefault("LICHESS_EXPLORER_ENABLED", "false")
os.environ.setdefault("STOCKFISH_PATH", os.path.abspath("../engines/stockfish-bin"))

from chessmind.app import app  # noqa: E402
from chessmind.config import get_settings  # noqa: E402
from chessmind.db import engine as app_engine  # noqa: E402
from chessmind.db import get_session  # noqa: E402
from chessmind.models import Base  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _reset_schema_sync() -> None:
    """Reset the schema using the sync psycopg driver so this can run outside asyncio.

    Called once per test session; keeps the schema fixture isolated from the
    per-test event loop.
    """
    from sqlalchemy import create_engine

    sync_engine = create_engine(get_settings().database_url_sync)
    with sync_engine.begin() as conn:
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS citext")
        conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
    sync_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    _reset_schema_sync()


_TABLES_TO_TRUNCATE = [
    "position_question",
    "master_explorer_cache",
    "mastery_state",
    "training_attempt",
    "training_session",
    "master_move_stat",
    "opponent_position_move",
    "opponent_profile",
    "engine_analysis",
    "repertoire_line",
    "move_edge",
    "position",
    "game",
    "import_job",
    "player",
]


async def _truncate_all(engine) -> None:
    """TRUNCATE every table so each test starts from an empty DB.

    Simpler than nested transactions when tests commit mid-way (e.g. the
    import endpoint commits the ImportJob row so the background task can
    see it).
    """
    async with engine.begin() as conn:
        tables = ", ".join(_TABLES_TO_TRUNCATE)
        await conn.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """Function-scoped session on a function-scoped engine so loop affinity matches."""
    settings = get_settings()
    # Background import jobs use the app's module-level engine. Its pooled connections are bound to
    # the previous test's (closed) event loop, so drop them rather than reuse them.
    await app_engine.dispose(close=False)
    engine = create_async_engine(settings.database_url, poolclass=None)
    await _truncate_all(engine)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
