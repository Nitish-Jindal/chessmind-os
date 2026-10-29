# ChessMind OS

Voice-first, engine-grounded chess opening intelligence. Maps a player's
repertoire against a universal opening graph, trains blindfold
visualization and tournament-speed recall, explains errors with grounded
Stockfish evidence, and simulates opponents from their actual games.

Working title; brand clearance pending.

**Status:** MVP vertical slice, deployable to Render with the Blueprint in
`render.yaml`. Scenario-by-scenario status is in [`ACCEPTANCE.md`](ACCEPTANCE.md);
phase tracking is in [`PROGRESS.md`](PROGRESS.md).

## Documents

| File | What it is |
| --- | --- |
| `CLAUDE.md` | Agent operating rules, MVP scope, the deterministic-correctness rule. |
| `docs/ARCHITECTURE.md` | Implementation plan: data model, APIs, modules, deployment. |
| `docs/handoff/ChessMind_OS_Developer_Handoff.docx` | Original product brief. |
| `ACCEPTANCE.md` | The handoff's 13 acceptance scenarios, each with its proof. |
| `PROGRESS.md` | Phase tracker. |
| `render.yaml` | Render Blueprint (API web service + static frontend). |

## Architecture

```
 Browser (React 18 + Vite + TS)                       Render static site
 ├─ react-chessboard / chess.js (display only)
 ├─ Web Speech API: STT (push-to-talk) + TTS
 └─ spoken-SAN resolver, constrained to GET /sessions/{id}/legal
        │  HTTPS + JWT (VITE_API_BASE_URL)
        ▼
 FastAPI (uvicorn, one process)                       Render web service (Python)
 ├─ auth (argon2id, JWT access + refresh)
 ├─ PGN import → opening graph (canonical-FEN positions, move edges, W/L/D)
 ├─ sessions FSM (guided / blindfold / tournament / opponent twin)
 ├─ sampler: opponent_profile → master_move_stat → opening_graph → engine
 ├─ labeler + deterministic coaching template (no LLM in the loop)
 ├─ SM-2 mastery scheduler
 └─ Stockfish: one long-lived UCI process, lock-guarded; engine_analysis
    cache is the primary path
        │  asyncpg (app) / psycopg (Alembic), TLS
        ▼
 PostgreSQL 16                                        Neon (or any Postgres 16)
```

Chess facts never come from a language model: legality, SAN and FEN come
from `python-chess`; evaluations and PVs from Stockfish (persisted with
version, depth, nodes and `perspective='white'`); statistics from the
indexed tables. See `CLAUDE.md` §3. The coaching text is currently a
deterministic template over that evidence; the LLM adapter is not wired.

Layout: `backend/` (FastAPI app, Alembic migrations, pytest suite),
`frontend/` (Vite app, vitest suite), `scripts/` (seeders, proof scripts,
build helpers, latency harness), `docs/`.

## Local setup

Prerequisites: Python 3.12, Node 20+, Docker (for Postgres), `curl`.

```bash
# 1. Postgres 16 (+ Redis, not used by the app yet)
docker compose up -d db
docker compose exec db createdb -U chessmind chessmind_test   # for pytest

# 2. Stockfish → engines/stockfish-bin (official sf_17.1 Linux build)
bash scripts/install_stockfish.sh        # macOS: brew install stockfish, then STOCKFISH_PATH=stockfish

# 3. Backend
cp .env.example backend/.env
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
alembic upgrade head
python ../scripts/seed_games.py --skip-if-seeded   # mainline opening graph
uvicorn chessmind.app:app --reload               # http://localhost:8000/docs

# 4. Frontend (second terminal)
cd frontend
cp .env.example .env.local     # VITE_API_BASE_URL=http://localhost:8000
npm ci
npm run dev                    # http://localhost:5173
```

Tests (real Postgres and real Stockfish; SQLite is not supported anywhere):

```bash
cd backend && pytest -q        # 86 tests; needs the chessmind_test DB and engines/stockfish-bin
cd frontend && npm test        # voice resolver + API base URL
```

Known wart: pytest prints `86 passed` and then the interpreter can hang on
exit (pre-existing; the engine/DB background threads are not torn down).
Run it under `timeout` in scripts.

Browser proofs (headless Chromium against the running stack; only the
microphone/system voices are stubbed): `node scripts/proof_frontend.mjs`
and `node scripts/proof_ui_flows.mjs`. API proofs: `scripts/day1_proof.py`,
`proof_italian_session.py`, `proof_mastery.py`, `proof_opponent.py`.

## Configuration

| Variable | Default (local) | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://chessmind:chessmind@localhost:5432/chessmind` | Any `postgres://` / `postgresql://` URL works, e.g. Neon's as copied. The app rewrites it to `postgresql+asyncpg://`, renames `sslmode` → `ssl`, and drops `channel_binding` (asyncpg rejects it; TLS is still required). |
| `DATABASE_URL_SYNC` | derived | Alembic URL. Empty = `DATABASE_URL` rewritten to `postgresql+psycopg://`, keeping `sslmode` and `channel_binding` (libpq supports both). |
| `JWT_SECRET` | dev placeholder | The app refuses to boot with the placeholder when `ENVIRONMENT=production`. |
| `ENVIRONMENT` | `dev` | `production` on Render. |
| `STOCKFISH_PATH` | `../engines/stockfish-bin` | Absolute, backend-relative, or a bare command on `PATH`. |
| `STOCKFISH_DEFAULT_DEPTH` | `18` | Default depth for `/analysis/position`. Session grading uses depth 10. |
| `STOCKFISH_DEFAULT_MULTIPV` | `3` | |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated exact origins. |
| `CORS_ORIGIN_REGEX` | empty | Optional extra origin regex (used for the Render static-site host). |
| `VITE_API_BASE_URL` (frontend, build time) | `http://localhost:8000` | Full URL, or a bare host: `api.example.com` → `https://api.example.com`; a dot-less Render service host `chessmind-api` → `https://chessmind-api.onrender.com`. |

## Dataset ingestion

- **Seed opening graph** — `scripts/seed_games.py` composes 153 real
  opening move sequences (Italian, Ruy Lopez, Sicilian, QGD / Slav /
  Catalan / Nimzo) and feeds them through the same import pipeline as the
  API, owned by a seed player. The move orders are real theory; the game
  **results are assigned in rotation, so the W/L/D numbers on seeded edges
  are not real-world statistics**. `--skip-if-seeded` makes it a no-op once
  the seed player has games; the Render build runs it that way on every
  deploy so mainlines always exist on a fresh database.
- **User PGNs** — `POST /imports/pgn` (JSON `{pgn, intent, source}`; the UI at `/import` reads a file or a paste) with
  `intent=repertoire` (graph + repertoire lines), `corpus` (graph only) or
  `opponent` (+ `opponent_profile_id`). Jobs are async; poll
  `GET /imports/{id}`. Variations are walked recursively, comments/NAGs
  kept, headers preserved in `provenance_json`. Exports from ChessBase /
  MetaDb arrive this way, as PGN.
- **Opponent PGNs** — `POST /opponents`, then `POST /opponents/{id}/import`
  (or the `opponent` intent). Rebuilds the opponent's conditional move
  frequencies used by the twin sampler.
- Fixtures: `scripts/data/ui_repertoire_153.pgn`, `ui_opponent_120.pgn`.

## Stockfish deployment

`scripts/install_stockfish.sh` downloads the official
`stockfish-ubuntu-x86-64-avx2` build of the pinned release (`sf_17.1`,
override with `STOCKFISH_RELEASE`) to `engines/stockfish-bin`, smoke-tests
it with `uci`, and falls back to the portable `x86-64-sse41-popcnt` build if
AVX2 does not run on the build host (`STOCKFISH_ARCH` forces one). Render's
native Python runtime has no root/apt, so this runs in the build; files
written under the repo during the build are part of the deployed service,
and `STOCKFISH_PATH` points at `/opt/render/project/src/engines/stockfish-bin`.

The app runs one Stockfish process per uvicorn worker with Stockfish's
defaults (`Threads=1`, `Hash=16` MB), behind a lock. Every result is cached
in `engine_analysis`, so repeated positions do not hit the engine.

## Voice configuration

Voice is the browser's Web Speech API: no server-side speech, no keys.

- **STT**: `SpeechRecognition` / `webkitSpeechRecognition`, `en-US`,
  push-to-talk, up to 5 alternatives per utterance. Works in Chrome and
  Edge; Safari support varies; Firefox has no STT (the text box is the
  fallback). Microphone access requires a secure context: `localhost` or
  HTTPS (the Render URL is HTTPS).
- **Resolution**: `frontend/src/voice/resolver.ts` maps "knight f3",
  "castle kingside", raw SAN, etc. onto the server's legal-move list and
  asks "Did you mean A or B?" instead of guessing. The backend re-validates
  every move with `python-chess`.
- **TTS**: `speechSynthesis`; the voice dropdown is filled on
  `voiceschanged`, and the choice is stored in `player.settings_json.tts_voice`.
- Blindfold reveals: `settings_json.blindfold_free_reveals_per_session`
  (default 2); extra reveals lower the visualization score.

## Deploy to Render

`render.yaml` defines two services (no secrets in the file):

| Service | Type | Build | Start |
| --- | --- | --- | --- |
| `chessmind-api` | Python web, free plan, `singapore` | `bash scripts/render_build.sh`: installs Stockfish, `pip install ./backend`, `alembic upgrade head`, `seed_games.py --skip-if-seeded` | `uvicorn chessmind.app:app --host 0.0.0.0 --port $PORT` (health check `/health`) |
| `chessmind-web` | Static site (global CDN) | `npm ci && npm run build` in `frontend/` | publishes `frontend/dist`; SPA rewrite `/* → /index.html` |

Steps:

1. Create a Neon project, preferably in AWS `ap-southeast-1` (Singapore) to
   sit next to the API, and copy its connection string.
2. Render → **New → Blueprint** → select this repo. When prompted, paste
   the Neon string into `DATABASE_URL` exactly as Neon gives it
   (`postgresql://…?sslmode=require&channel_binding=require` is fine).
   `JWT_SECRET` is generated by Render.
3. Apply. The API build migrates and seeds the database. Free plans have no
   pre-deploy hook, so this happens during the build.
4. Check the URLs. The frontend gets `VITE_API_BASE_URL` from the API
   service's `host` property, and `CORS_ORIGIN_REGEX` allows
   `https://chessmind-web[-suffix].onrender.com`. If Render gave either
   service a different name or domain, set `VITE_API_BASE_URL` to the API's
   full `https://…` URL (then redeploy the static site), and/or set
   `CORS_ORIGINS` to the frontend's URL.
5. Smoke test: `curl https://<api>/health`, then register, import a PGN
   and play a session in the browser. Real-microphone voice is verified
   here, on the HTTPS URL.

What was proven locally (`scripts/render_build.sh` against a fresh
Postgres 16 with TLS + SCRAM, using a Neon-style URL with
`sslmode=require&channel_binding=require`): Stockfish installed; Alembic
migrated through psycopg with channel binding; the seed imported 153 games
through asyncpg over `ssl=require` (`pg_stat_ssl` showed TLSv1.3); a second
build skipped the seed; uvicorn booted with `ENVIRONMENT=production`;
`/health` returned OK; CORS allowed `https://chessmind-web-x7k2.onrender.com`
and rejected other origins. The Blueprint itself was not applied (no Render
account here), and render.com docs could not be reached from the build
sandbox, so the `fromService … property: host` → public URL mapping is the
one unverified link. Step 4 covers it.

## Measured latency

Measured with `scripts/measure_latency.py` (results in
`scripts/data/latency.json`) on 2026-10-29 against the local stack:
one uvicorn process; Postgres 16 on the same host, reached over TLS with
the Neon-style URL; Stockfish 17.1 (AVX2 build, `Threads=1`, `Hash=16` MB);
a 4-vCPU Intel Xeon 2.8 GHz container. The client ran on the same host, so
**there is no network RTT in these numbers**. Searches are depth-limited
(no movetime limit). Cold = the position had no cached analysis at that
depth, so Stockfish searched. Warm = the same requests replayed and served
from `engine_analysis`.

| Endpoint | Engine settings | Cold p50 / p95 | Warm p50 / p95 | n |
| --- | --- | --- | --- | --- |
| `POST /sessions/{id}/move` | depth 10, MultiPV 3, two lookups per move (before/after) + sampler | **66.4 / 104.0 ms** | **22.7 / 28.6 ms** | 120 + 120 |
| `POST /analysis/position` | depth 18, MultiPV 3 | **850.3 / 1655.3 ms** (engine time 856 / 1637 ms) | **9.3 / 13.2 ms** | 60 + 60 |

Moves were random legal moves from 20 seeded guided sessions (6 player
moves each), so they leave the book quickly. That is a pessimistic case
for the cache. One of the 60 cold analysis positions was already cached.
**Nothing here was measured on Render.** The free plan's shared fractional
CPU will make cold Stockfish searches much slower than on this 4-vCPU box,
and every request adds browser→Singapore and API→Neon round trips. Re-run
the script against the deployed URL (`--base-url https://…`) for real
deployed numbers.

## Known gaps / backlog

- **MetaDb / ChessBase**: import is through an authorized PGN adapter only.
  Users upload PGN exports through `/imports/pgn`. There is no direct
  MetaDb or ChessBase vendor API integration, and none will be built
  without credentials and licensing. Nothing in the repo talks to either
  vendor.
- **Lichess master statistics are not ingested.** The sampler has a
  `master_move_stat` tier (with source and sample size), but the table is
  empty. The plan is to fetch Lichess masters stats at runtime on the
  deployed host, because this build sandbox cannot reach lichess.org. That
  fetcher is not written. Until it is, "open" statistics come only from
  imported PGNs, and the seeded W/L/D counts are not real (see Dataset
  ingestion).
- **Render free-tier cold start.** The free web service sleeps after
  ~15 min idle. The first request then waits for the container to boot,
  and the first analysis also pays the Stockfish process spawn. Expect
  tens of seconds; not measured here. Mitigations: upgrade the plan,
  ping `/health` from the frontend before a session starts.
- **Opponent tree filters and dedup are not built.** `GET /opponents/{id}/tree`
  has no recency / colour / time-control / rating filters. Re-importing
  the same PGN double-counts games, because there is no dedup by source and
  headers. The same applies to the global opening graph.
- **Real-microphone voice is verified manually on the deployed HTTPS URL.**
  CI and local proofs drive the same resolver → `/move` path with typed
  input and a stubbed `speechSynthesis`. No real microphone is available
  headless.
- **Visualization questions** (piece-location / hanging-piece quizzes over
  a spoken variation) are not built. Blindfold play with reveal budgets is.
- **Chess DNA** (`GET /players/{id}/chess-dna`) and the progress dashboard
  beyond the mastery page are not built.
- **LLM coach not wired.** Coaching is a deterministic template over
  Stockfish evidence and board features. The Grok adapter is future work.
- **No CI yet.** There is no `.github/workflows`. Tests run locally (see
  above).
- **Single process, no Redis/worker.** Import jobs run as in-process
  `asyncio` tasks, so a restart mid-import leaves the job unfinished. Redis is in `docker-compose.yml` but unused.
- **Replay determinism is per cache state.** A seeded session replays
  exactly while `engine_analysis` is unchanged (verified: 20/20 seeds
  identical). Out-of-book opponent moves come from the engine fallback, and
  the cache returns the deepest stored row. If a deeper analysis of that
  position lands between runs, the replay can choose differently.

## License

TBD. Do not redistribute third-party PGN or opening-database content
without checking the source's license first.
