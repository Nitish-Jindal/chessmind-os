# ChessMind OS

Voice-first, engine-grounded chess opening intelligence. Maps a player's
repertoire against a universal opening graph, trains blindfold
visualization and tournament-speed recall, explains errors with grounded
engine evidence, and simulates opponents from their actual games.

Working title; brand clearance pending.

**Status:** Phase 0 — scaffold and architecture plan. No feature code has
landed yet; see `PROGRESS.md`.

## Documents

- `CLAUDE.md` — agent operating instructions, MVP scope, the
  deterministic-correctness hard rule, and the stack.
- `docs/ARCHITECTURE.md` — the implementation plan (data model, APIs,
  modules, deployment).
- `docs/handoff/ChessMind_OS_Developer_Handoff.docx` — the original product
  brief.
- `PROGRESS.md` — phase tracker.

## Stack

- Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic
- PostgreSQL 16 · Redis 7
- `python-chess` · Stockfish (long-lived UCI worker, lock-guarded; cache
  is the primary path)
- React 18 · Vite · TypeScript · `react-chessboard`
- Browser Web Speech API (STT + TTS, with a provider seam for server-side
  streaming later)
- Render (web + worker + managed Postgres + managed Redis)
- GitHub Actions

## Local development (target shape; wiring lands in Day 1)

```bash
# 1. Copy env template once wiring is in
cp .env.example .env

# 2. Start Postgres + Redis + backend + frontend
docker compose up

# 3. Backend
cd backend
uv venv && source .venv/bin/activate
uv pip install -e .[dev]
alembic upgrade head
uvicorn chessmind.app:app --reload

# 4. Frontend
cd frontend
cp .env.example .env.local   # VITE_API_BASE_URL → the backend (default http://localhost:8000)
npm install
npm run dev                  # http://localhost:5173; `npm test` runs the voice-resolver tests

# 5. Seed openings
python scripts/seed_openings.py
```

Stockfish is expected on `PATH` or at `STOCKFISH_PATH`; the worker starts a
single UCI process per worker and reuses it for every request behind an
asyncio lock. **SQLite is not a supported runtime or test backend.** Tests
run against a disposable Postgres container.

## Deployment

Render. One web service (FastAPI), one background worker (import + engine
jobs), managed Postgres, managed Redis. Env values live in a Render env
group; nothing secret is in the repo.

## Known gaps (kept honest)

- **Render free-tier cold start.** The free web-service plan sleeps after
  ~15 minutes idle. The first request after a sleep takes 30–60 s to boot
  the container and warm the Stockfish worker, so the first voice session
  on a cold instance will feel sluggish. Mitigations: keep the engine
  worker warm with a cron ping, upgrade to the Starter plan, or pre-warm
  from the frontend before the user hits "start session". Not a defect —
  a free-tier characteristic to disclose.
- **MetaDb vendor integration.** Day 5 ships a MetaDb-compatible PGN
  import adapter against user-uploaded exports; a direct vendor API
  connection is blocked on credentials / licensing. Status is tracked in
  `PROGRESS.md`.
- **Voice latency depends on the user's browser.** Day 2 uses the Web
  Speech API, so latency and voice selection vary by Chrome / Edge /
  Safari. A server-side streaming TTS/STT swap is scoped post-MVP.
- **No preindexed master database at launch.** The seed covers ECO
  A00–E99 headers plus a curated subset of mainlines. Larger ingestion
  (Lichess monthly dumps, TWIC) is a post-MVP job.
- **LLM is explain-only.** No chess fact — move, eval, stat — comes from
  the LLM; see `CLAUDE.md` §3. If the engine or stats layer is missing a
  value, coaching falls back to a deterministic template rather than
  improvising.
- **Browser Web Speech API coverage.** TTS voice list is populated on the
  `voiceschanged` event; some browsers return an empty list on first
  load, so the voice dropdown shows a "Loading voices…" state until the
  event fires.

## License

TBD. Do not redistribute third-party PGN or opening-database content
without checking the source's license first.
