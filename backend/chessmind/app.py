"""FastAPI application entry point."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from chessmind import __version__
from chessmind.routers import analysis, auth, health, imports, openings, players
from chessmind.services.engine import engine_client

logger = logging.getLogger("chessmind")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("chessmind startup version=%s", __version__)
    yield
    logger.info("chessmind shutdown")
    engine_client.shutdown()


app = FastAPI(
    title="ChessMind OS",
    version=__version__,
    description="Voice-first, engine-grounded chess opening intelligence.",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    return response


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(players.router)
app.include_router(imports.router)
app.include_router(openings.router)
app.include_router(analysis.router)
