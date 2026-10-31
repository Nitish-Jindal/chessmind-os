---
title: ChessMind OS
emoji: ♟️
colorFrom: indigo
colorTo: gray
sdk: gradio
sdk_version: 6.31.0
app_file: app.py
python_version: "3.12"
pinned: false
short_description: Voice-first, engine-grounded chess opening trainer (API)
---

# ChessMind OS

Voice-first, engine-grounded chess opening intelligence. Maps a player's
repertoire against a universal opening graph, trains blindfold
visualization and tournament-speed recall, explains errors with grounded
Stockfish evidence, and simulates opponents from their actual games.

Working title; brand clearance pending.

**Status:** MVP vertical slice. Card-free deploy: API on a Hugging Face Gradio
Space, frontend on Cloudflare Pages or Vercel (see *Deploy*). `render.yaml` is
kept as an alternative. Scenario-by-scenario status is in [`ACCEPTANCE.md`](ACCEPTANCE.md);
phase tracking is in [`PROGRESS.md`](PROGRESS.md).

## Documents

| File | What it is |
| --- | --- |
| `CLAUDE.md` | Agent operating rules, MVP scope, the deterministic-correctness rule. |
| `docs/ARCHITECTURE.md` | Implementation plan: data model, APIs, modules, deployment. |
| `docs/handoff/ChessMind_OS_Developer_Handoff.docx` | Original product brief. |
| `ACCEPTANCE.md` | The handoff's 13 acceptance scenarios, each with its proof. |
| `PROGRESS.md` | Phase tracker. |
| `app.py`, `requirements.txt`, `packages.txt` | Hugging Face Gradio Space entrypoint, pip deps, apt deps (Stockfish). |
| `Dockerfile`, `scripts/hf_start.sh` | Docker alternative (Docker Spaces are PRO-gated; also runs anywhere). |
| `.github/workflows/deploy-hf-space.yml` | Pushes `main` to the Space. |
| `render.yaml` | Render Blueprint (alternative deploy). |

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
 ├─ sampler: [requested opening book] → opponent_profile → master_move_stat
 │           → opening_graph → engine
 ├─ opening taxonomy (Lichess chess-openings, CC0): names + announcements
 ├─ labeler + deterministic coaching template; optional LLM wording
 │  (off by default; validated against the cited evidence)
 ├─ opponent prep report (their games + Stockfish suggestions)
 ├─ position questions + quizzes: answered/scored from the session FEN
 │  (python-chess) and the cached Stockfish row
 ├─ Lichess Masters explorer adapter (TTL cache → master_move_stat)
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
indexed tables; opening names from the vendored Lichess `chess-openings`
taxonomy. See `CLAUDE.md` §3. The coaching text is a deterministic template
over that evidence. An optional LLM layer (off by default, needs an API key)
can reword it, and only within the cited evidence; see *Optional LLM coaching*.

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
cd backend && pytest -q        # 194 tests; needs the chessmind_test DB and engines/stockfish-bin
cd frontend && npm test        # 32 tests: voice resolver, question/command routing, API base URL
```

Known wart: pytest prints `194 passed` and then the interpreter can hang on
exit (pre-existing; the engine/DB background threads are not torn down).
Run it under `timeout` in scripts.

Browser proofs (headless Chromium against the running stack; only the
microphone/system voices are stubbed): `node scripts/proof_frontend.mjs`,
`node scripts/proof_ui_flows.mjs`, `node scripts/proof_voice_openings.mjs`
(opening announcements, quiet mode, illegal moves, "play … against me",
playing Black), `node scripts/proof_opponent_prep.mjs` (150 real games →
prep report → blindfold game vs the twin) and
`node scripts/proof_position_questions.mjs`. The last one needs a fresh
database, the backend started with
`LICHESS_EXPLORER_URL=http://127.0.0.1:8765 LICHESS_EXPLORER_BACKOFF_S=2`,
and a production frontend build (`npx vite build && npx vite preview --port 5173`).
Under the dev server, React StrictMode runs effects twice, so the masters
panel reads "cached" instead of "fetched just now". API proofs: `scripts/day1_proof.py`,
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
| `LICHESS_EXPLORER_ENABLED` | `true` | Fetch real master stats from the Lichess explorer. Tests set it to `false`. |
| `LICHESS_EXPLORER_URL` | `https://explorer.lichess.ovh` | Base URL; `/masters` is appended. |
| `LICHESS_EXPLORER_TTL_HOURS` | `168` | How long a cached position is served before refetching. |
| `LICHESS_EXPLORER_TIMEOUT_S` / `LICHESS_EXPLORER_BACKOFF_S` | `4` / `60` | Per-request timeout; after a failure, no upstream calls for this long (a 429 honours `Retry-After`). |
| `LICHESS_API_TOKEN` | empty | Optional. Sent as `Authorization: Bearer` if Lichess starts requiring a token for the explorer. |
| `LLM_EXPLANATIONS_ENABLED` | `false` | Optional LLM wording of coaching. Stays off unless `LLM_API_KEY` **and** `LLM_MODEL` are also set. |
| `LLM_PROVIDER` | `xai` | `xai` (Grok), `openai`, `anthropic` or `gemini`. |
| `LLM_API_KEY` | empty | Provider API key. **Required to enable the LLM layer**; never commit it. |
| `LLM_MODEL` | empty | Provider model id (e.g. a Grok, GPT, Claude or Gemini model name). Required when enabled. |
| `LLM_BASE_URL` / `LLM_TIMEOUT_S` | provider default / `6` | Endpoint override (OpenAI-compatible gateways) and per-call timeout. |
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
- **Real master statistics (Lichess Masters)** — fetched on demand, per
  position, from `https://explorer.lichess.ovh/masters?fen=…` (free; no key
  needed today). `/openings/tree` returns them in a separate `masters` block
  (source, status, fetch time, total games, per-move White/Draw/Black and
  average rating, opening name, lichess.org link). They are never mixed
  into the local counts, which carry a `local_stats_note`. `GET
  /openings/masters?fen=…` works for any legal position, even one outside
  the local graph. Every move in a response is validated with python-chess
  (`parse_san` on the queried board; Lichess's `e1h1`-style castling is
  normalized) before it is stored or served. Snapshots are cached in
  `master_explorer_cache` keyed by canonical FEN, with a TTL, and the
  per-move counts are mirrored into `master_move_stat`, so the opponent
  sampler's master tier runs on the same real data. If Lichess is
  unreachable, a stale cached snapshot is served (`status: stale`), or
  `status: unavailable` and the UI shows local counts only. The Openings
  page shows the Lichess attribution and a link back.
- **Opening names** — `backend/chessmind/data/openings/*.tsv` is the
  Lichess [`chess-openings`](https://github.com/lichess-org/chess-openings)
  dataset (CC0, commit in `SOURCE.md`), ~3,900 named lines. Each is
  replayed with python-chess and keyed by canonical FEN, so transpositions
  share a name. Positions get `eco` / `opening_name` only from this table
  (migration `0006` re-labelled older rows); PGN `ECO`/`Opening` headers
  stay on the `game` row.
- Fixtures: `scripts/data/ui_repertoire_153.pgn`, `ui_opponent_120.pgn`
  (composed lines), and `opponent_firouzja_150.pgn`: the 150 most recent
  standard games of Alireza Firouzja (2021-09 → 2022-08), real games from
  the PgnMentor collection as mirrored at
  [rozim/ChessData](https://github.com/rozim/ChessData) (`PgnMentor/Firouzja.pgn`).

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
- **Position questions** (handoff §3.3), by voice or text, at any point in
  a session. Questions are recognized client-side (`isBoardQuestion`) and
  sent to `POST /sessions/{id}/ask`, which parses them on the server.
  Supported: "where is my knight" / "where are your bishops", "what's on
  e5" ("see five" works), "anything hanging?" / "is your rook hanging",
  "whose move is it", and "what's the eval" / "who is better". Answers come
  from the session FEN via python-chess. The evaluation comes from the
  cache-first Stockfish path (depth 10, the persisted `engine_analysis`
  row), white-centric with a player-relative reading. Answers are
  captioned and spoken. "Hanging" means attacked by more enemy pieces than
  defenders (no exchange evaluation); the answer says so.
- **Quiz mode**: "quiz me" (or the button) gets a seeded question about
  the current board. Kinds: piece location, square contents, hanging
  pieces, side to move, rough evaluation. The spoken answer is scored
  against facts captured when the question was asked
  (`POST /sessions/{id}/quiz/{qid}/answer`). Square sets use Jaccard
  overlap, square contents give half credit each for piece and colour, and
  the evaluation bucket uses ±50 cp. Every question and answer is stored
  in `position_question`. `GET /sessions/{id}/questions` returns the tally.

- **Opening announcements**: the first time a session reaches a named
  position (after either side's move), its name is spoken and captioned
  once (`opening:` captions), e.g. "Ruy Lopez" after 1.e4 e5 2.Nf3 Nc6
  3.Bb5. Generic first-move names (King's Pawn Game, Queen's Pawn Game,
  King's Knight Opening) are skipped. The session page shows the current
  opening.
- **Quiet mode**: a launcher checkbox, a session checkbox, or saying "quiet
  mode on/off" (`PATCH /sessions/{id}/settings`). Only the opponent's moves
  are spoken; no coaching, quizzes or announcements. Moves are still
  validated, and still graded for the mastery schedule.
- **Illegal moves**: a move-shaped utterance or entry that matches no legal
  move gets "That's an illegal move, try again." (spoken and captioned),
  plus a red alert above the board, or above the hidden board in
  blindfold. The game does not advance. The API answers an illegal SAN/UCI
  with 422 `{code: "illegal_move"}`.
- **Setting the opponent's opening**: say "play the London System /
  Caro-Kann / Spanish (Ruy Lopez) / Italian / Queen's Gambit / Sicilian
  against me", or any other taxonomy family by name, or a move: "play d4
  against me". It can also be typed in the launcher's *Opponent's opening*
  field. The phrase is parsed deterministically
  (`services/opening_taxonomy.py`) and the move validated by python-chess.
  The opponent first follows the opening's defining line, then any move
  that stays on the family's named lines (weighted by master/graph
  counts, seeded), then samples normally once out of book (captioned).
  This works whether you play White or Black.
- **Playing Black**: choose *Play as: Black*. The engine opens and its
  first move is spoken. Saying "play d4 against me" (or naming an opening)
  before your first move restarts the game so the engine opens that way.

## Opponent prep

Import one opponent's games on the Opponents page (or `POST
/opponents/{id}/import`), then *Build prep report*
(`GET /opponents/{id}/prep?plies=12&lines=5&depth=14`). For each colour the
opponent played:

- **first moves** and **most frequent lines**: every move in a line was
  played in at least max(2, 5%) of their games with that colour, with the
  opponent's score and the taxonomy name;
- **likely branches**: positions where two or more continuations recur,
  with their shares and who chooses;
- **suggested responses**: Stockfish's best move (MultiPV 3, persisted in
  `engine_analysis`) where it is your turn at the end of each line, after
  their usual next move if needed. Each comes with how the opponent scored
  when they faced it.

Everything is counted from their games with python-chess. *Play the twin*
with *Blindfold* checked starts a voice-only session against their digital
twin.

## Optional LLM coaching

Off by default. To enable, set `LLM_EXPLANATIONS_ENABLED=true`,
`LLM_PROVIDER` (`xai` for Grok, `openai`, `anthropic`, `gemini`),
**`LLM_API_KEY`** and `LLM_MODEL`. Without a key nothing is sent anywhere,
and coaching is the deterministic template. When enabled, the move's
grounded facts are sent as a numbered list: classification and swing,
Stockfish's best move, alternatives, PV and white-centric evals, the
python-chess board features, and the template text. The model must answer
JSON `{"text", "cited_facts"}`. The reply is discarded, and the template
used, if it cites unknown facts, names a piece move or capture that is not
in the engine evidence, quotes an eval that is not in the facts, or if the
call fails or times out. Only the spoken summary is reworded; the
suggested move and every number stay deterministic. Quiet mode never calls
it. The coach card shows the wording's source.

## Deploy: Hugging Face Gradio Space (API, free tier)

The README frontmatter declares a **Gradio** Space (`sdk: gradio`,
`app_file: app.py`, `python_version: "3.12"` because the backend needs
Python ≥ 3.12). Hugging Face installs `packages.txt` via apt (Stockfish at
`/usr/games/stockfish`) and `requirements.txt` via pip, then runs
`python app.py`, which:

1. exits early if `DATABASE_URL` or `JWT_SECRET` is missing;
2. runs `alembic -c backend/alembic.ini upgrade head`;
3. runs `scripts/seed_games.py --skip-if-seeded` (no-op once seeded);
4. serves the real FastAPI app (`chessmind.app`) with uvicorn on `0.0.0.0:7860`,
   plus a small Gradio status page at `/status`. API docs are at `/docs`.

`ENVIRONMENT=production` is forced by `app.py`; `STOCKFISH_PATH` defaults to
`/usr/games/stockfish`.

**Where to set what**

| Where | Name | Kind | Value |
| --- | --- | --- | --- |
| Space → Settings → Variables and secrets | `DATABASE_URL` | Secret (required) | Neon connection string, as-is. |
| same | `JWT_SECRET` | Secret (required) | Long random string (`python -c "import secrets; print(secrets.token_urlsafe(48))"`). |
| same | `CORS_ORIGINS` | Variable | Exact frontend origin(s), comma-separated, no trailing slash. |
| same | `LICHESS_API_TOKEN` | Secret (optional) | Only if the Lichess explorer requires a token. |
| same | `STOCKFISH_PATH` | Variable (optional) | Only to override `/usr/games/stockfish`. |
| GitHub repo → Settings → Secrets and variables → Actions | `HF_TOKEN` | Repository **secret** | Hugging Face **Write** token. |
| same | `HF_SPACE` | Repository **variable** | `<hf-username-or-org>/<space-name>`. |

Create the Space with **SDK: Gradio**, hardware CPU basic (free), public.
The `deploy-hf-space.yml` workflow force-pushes a snapshot of the repo to the
Space on every push to `main` (it works unchanged for Gradio Spaces). The
frontend steps below (*Deploy the frontend*) are the same.

Local run: `pip install -r requirements.txt` (needs Python 3.12+, a local
`stockfish`, and a Postgres), set the env vars, then `python app.py`.

The Docker flow below is kept as an alternative (it needs a Docker Space, so
swap the frontmatter back to `sdk: docker` / `app_port: 7860` to use it).

## Deploy: Hugging Face Docker Space (API, alternative) + Cloudflare Pages / Vercel (frontend)

No payment card needed. You need a Postgres 16 URL (Neon's free tier works),
a Hugging Face account and a Cloudflare or Vercel account.

How it fits together:

| Piece | Where | Notes |
| --- | --- | --- |
| API | Hugging Face **Docker** Space | Built from the root `Dockerfile` (`python:3.12-slim`, Stockfish from apt at `/usr/games/stockfish`). Listens on `7860` (`app_port` in the frontmatter above). |
| Start | `scripts/hf_start.sh` | `alembic upgrade head` → `seed_games.py --skip-if-seeded` → `uvicorn chessmind.app:app --host 0.0.0.0 --port 7860`. A failed step stops the container. |
| Database | Neon (any Postgres 16) | `DATABASE_URL` as Neon gives it; the app normalizes the driver and TLS parameters. |
| Frontend | Cloudflare Pages or Vercel | Static build of `frontend/`; `frontend/public/_redirects` (Cloudflare/Netlify) and `frontend/vercel.json` send every path to `/index.html` so client-side routes survive a reload. |
| Deploys | `.github/workflows/deploy-hf-space.yml` | On push to `main`, force-pushes a single-commit snapshot to the Space (without `scripts/data` screenshots and the handoff `.docx`: binary files Hugging Face's git remote rejects without LFS). |

### 1. Create the Space (once)

1. huggingface.co → **New → Space**. Name it (e.g. `chessmind-api`), choose
   **SDK: Docker → Blank**, hardware **CPU basic (free)**. Visibility
   **Public** (a private Space cannot be called from a browser without a
   token). Create it empty; the first deploy fills it.
2. Hugging Face → **Settings → Access Tokens → New token**, type **Write**
   (or fine-grained with write access to this Space). Copy it.
3. In the Space → **Settings → Variables and secrets**, add:

| Name | Kind | Value |
| --- | --- | --- |
| `DATABASE_URL` | **Secret** | Neon connection string, pasted as-is. |
| `JWT_SECRET` | **Secret** | A long random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`. The app refuses to boot in production with the placeholder. |
| `CORS_ORIGINS` | Variable | Exact frontend origin(s), comma-separated, no trailing slash: `https://chessmind.pages.dev`. |
| `LICHESS_API_TOKEN` | Secret (optional) | Only if Lichess starts requiring a token for the masters explorer. |
| `CORS_ORIGIN_REGEX` | Variable (optional) | e.g. `^https://chessmind(-[a-z0-9]+)?\.pages\.dev$` to also allow Cloudflare/Vercel preview URLs. |

`ENVIRONMENT=production` and `STOCKFISH_PATH=/usr/games/stockfish` are baked
into the image; override them in the Space only if you change the image.

### 2. Wire up GitHub (once)

GitHub repo → **Settings → Secrets and variables → Actions**:

| Name | Kind | Value |
| --- | --- | --- |
| `HF_TOKEN` | Repository **secret** | The Hugging Face write token from step 1.2. |
| `HF_SPACE` | Repository **variable** | `<hf-username-or-org>/<space-name>`, e.g. `jane/chessmind-api`. |

Then push to `main` (or run the workflow from the **Actions** tab). The
Space rebuilds; watch **Logs** in the Space. First build takes a few minutes.
The API is at `https://<hf-username>-<space-name>.hf.space`; check
`https://<hf-username>-<space-name>.hf.space/health`.

Free Spaces sleep after inactivity; the first request wakes the container
(migrate + seed-skip + boot take a few seconds).

### 3. Deploy the frontend

Set `VITE_API_BASE_URL` to the Space URL at **build time**.

- **Cloudflare Pages** → Create → Connect to Git → this repo. Root directory
  `frontend`, build command `npm ci && npm run build`, output directory
  `dist`, env var `VITE_API_BASE_URL=https://<hf-username>-<space-name>.hf.space`.
  `_redirects` is copied into `dist` from `frontend/public`.
- **Vercel** → Add New → Project → this repo. Root directory `frontend`
  (framework Vite is detected; `frontend/vercel.json` adds the rewrite), env
  var `VITE_API_BASE_URL` as above.

Then put the resulting frontend origin into the Space's `CORS_ORIGINS`
(Space restarts on change) and reload.

### Local check of the image

```bash
docker build -t chessmind-hf .
docker run --rm -p 7860:7860 -e DATABASE_URL='postgresql://user:pass@host/db' \
  -e JWT_SECRET=change-me-long-random -e CORS_ORIGINS=http://localhost:5173 chessmind-hf
curl localhost:7860/health
```

## Deploy to Render (alternative)

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
- **Lichess master statistics are verified against a hand-written
  fixture only.** The adapter, cache, fallback and UI are built and tested,
  but explorer.lichess.ovh is blocked by this build sandbox's network
  policy. Parsing, caching, TTL, `master_move_stat` mirroring, stale/
  unavailable fallback and the UI panel are proven with JSON fixtures
  hand-written in the documented response shape
  (`backend/tests/fixtures/`, marked as such inside the files) and a
  local replay server. The first live fetch happens on the deployed host.
  Check it there (Openings page → "fetched just now"), and run
  `python scripts/record_explorer_fixture.py` from a networked host to
  replace the fixtures with recorded responses. If Lichess requires a
  token for the explorer, set `LICHESS_API_TOKEN`. Seeded local W/L/D
  counts remain assigned, not real, and are labelled as such.
- **Opening names are exact-position only.** A position gets a name only
  if it is one of the taxonomy's named positions. Unnamed positions keep
  the last name reached for display, and nothing is announced for them.
- **Opening requests follow named lines only.** The "book" for a
  requested opening is the taxonomy's named lines for that family, not a
  repertoire. Systems such as the London are reached only through move
  orders the taxonomy names (1.d4 d5 2.Bf4, 1.d4 Nf6 2.Nf3 e6 3.Bf4, …);
  after an unnamed reply (e.g. 1.d4 e6) the opponent samples normally. A
  single-move request applies to the next opponent move only. Once you
  have moved, a request does not restart the game.
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
- **Position questions cover five kinds.** Threatened squares, pawn
  structure, candidate moves and tactical motifs (also listed in handoff
  §3.3) are not built. Asking questions in blindfold mode is counted
  (`questions_asked`) but does not reduce the visualization score.
- **Chess DNA** (`GET /players/{id}/chess-dna`) and the progress dashboard
  beyond the mastery page are not built.
- **LLM coaching is optional and unproven against a live provider.** No
  API key is available in this sandbox. The adapter, validation and every
  fallback are tested against a local HTTP server that speaks the
  OpenAI-compatible and Anthropic wire formats with canned replies; the
  Gemini path is untested. The validator cannot tell a pawn move written
  as a bare square ("d4") from a square mention, so it does not reject one.
- **Quiet mode still grades.** Each quiet move still runs the Stockfish
  grading (for the mastery schedule), so latency matches a normal move.
- **Opponent prep reads whole games; import is slow.** Opponent import
  walks every ply into the global graph: 150 real games took ~95 s locally.
  The report has no recency or time-control weighting yet.
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
