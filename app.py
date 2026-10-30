"""Hugging Face Gradio Space entrypoint (``python app.py``).

Startup: check required env, run Alembic migrations, run the idempotent
opening seed, then serve the existing FastAPI app on 0.0.0.0:7860 with a
tiny Gradio status page mounted at ``/status``. Any failed step aborts the
start so the Space shows a runtime error instead of a half-initialised
database. Config comes from env (Space secrets / variables):

    DATABASE_URL       required, secret   Postgres URL (Neon)
    JWT_SECRET         required, secret
    CORS_ORIGINS       variable           frontend origin(s), comma-separated
    STOCKFISH_PATH     default /usr/games/stockfish (apt: packages.txt)
    ENVIRONMENT        forced to production
    LICHESS_API_TOKEN  optional secret
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 7860

os.environ.setdefault("STOCKFISH_PATH", "/usr/games/stockfish")
os.environ["ENVIRONMENT"] = "production"


def _require_env() -> None:
    missing = [k for k in ("DATABASE_URL", "JWT_SECRET") if not os.environ.get(k, "").strip()]
    if missing:
        sys.exit(f"ERROR: missing required env: {', '.join(missing)} (add as Space secrets)")


def _bootstrap() -> None:
    """Migrate then seed; same steps as scripts/hf_start.sh."""
    # script_location in alembic.ini is relative to the cwd, so run from backend/
    # with the -c path relative to the repo root resolved explicitly.
    subprocess.run(
        ["alembic", "-c", str(ROOT / "backend" / "alembic.ini"), "upgrade", "head"],
        cwd=ROOT / "backend",
        check=True,
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed_games.py"), "--skip-if-seeded"],
        cwd=ROOT,
        check=True,
    )


_require_env()
if __name__ == "__main__":
    _bootstrap()

import gradio as gr  # noqa: E402
from chessmind.app import app as fastapi_app  # noqa: E402

with gr.Blocks(title="ChessMind OS") as status_page:
    gr.Markdown("# ChessMind OS\nChessMind OS API — see [/docs](/docs). Health: [/health](/health).")

app = gr.mount_gradio_app(fastapi_app, status_page, path="/status")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, proxy_headers=True, forwarded_allow_ips="*")
