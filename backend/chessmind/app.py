"""FastAPI application entry point."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from chessmind import __version__
from chessmind.routers import health

logger = logging.getLogger("chessmind")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("chessmind startup version=%s", __version__)
    yield
    logger.info("chessmind shutdown")


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
