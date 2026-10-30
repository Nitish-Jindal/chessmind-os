#!/usr/bin/env bash
# Container entrypoint for the Hugging Face Docker Space (see Dockerfile).
#
# Migrate, run the idempotent opening seed, then serve. Any failure aborts
# the start so the Space shows a build/runtime error instead of serving a
# half-initialised database. Run from the repository root.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${DATABASE_URL:?DATABASE_URL is not set (add it as a Space secret)}"
: "${JWT_SECRET:?JWT_SECRET is not set (add it as a Space secret)}"

(cd backend && alembic upgrade head)

# Mainline opening graph always exists after a deploy; no-op once seeded.
python scripts/seed_games.py --skip-if-seeded

cd backend
exec uvicorn chessmind.app:app --host 0.0.0.0 --port "${PORT:-7860}" \
    --proxy-headers --forwarded-allow-ips='*'
