"""Health / readiness probes."""

from fastapi import APIRouter

from chessmind import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
