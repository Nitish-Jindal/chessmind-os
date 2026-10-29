#!/usr/bin/env bash
# Render build for the backend web service (see render.yaml).
#
# Free-plan web services have no pre-deploy hook, so migrations and the
# idempotent opening seed run here, against the DATABASE_URL in the
# service environment. Run from the repository root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

bash scripts/install_stockfish.sh

pip install --upgrade pip
pip install ./backend

cd backend
alembic upgrade head
cd ..

# Mainline opening graph always exists after a deploy; no-op once seeded.
python scripts/seed_games.py --skip-if-seeded
