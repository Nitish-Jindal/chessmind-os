# ChessMind OS — Architecture & Implementation Plan

Version 1.1 · 2026-10-29 · Supersedes the prior plan from the previous
session. Reflects the five resolved decisions from the maintainer review
(§11).

The authoritative product brief is
`docs/handoff/ChessMind_OS_Developer_Handoff.docx`. This document is the
implementation layer: what we are building, how it is wired, what the data
looks like, and what we are explicitly deferring.

---

## Table of contents

1. Scope and MVP boundaries
2. System architecture
3. Tech stack and runtime posture
4. Data model
5. API surface
6. Chess subsystems — rules, engine, LLM, voice
7. PGN import and the opening graph
8. Opponent modeling
9. Deployment and environments
10. Non-functional requirements
11. Decisions log (previously open; now resolved)
12. Testing strategy
13. Five-day phase mapping

---

## 1. Scope and MVP boundaries

The MVP is a working end-to-end chess trainer that proves every major user
flow from the handoff. It is **not** a product-complete system; see
`CLAUDE.md` §2 for the day-by-day deliverables and §3 for the hard rule on
deterministic chess correctness.

**In scope:** Postgres, python-chess, Stockfish, FastAPI, React, browser
Web Speech API, Render deployment, user-uploaded PGN ingestion, voice
guided/blindfold/tournament sessions, mastery scoring, opponent tree, LLM
coach that explains grounded evidence only.

**Out of scope for MVP:** Leela/Maia; mobile apps; billing; direct MetaDb
or ChessBase vendor APIs; multi-tenant admin; `pgvector`-backed retrieval
of coaching notes (the column is reserved but not wired in Day 1–5);
coach-side workflows beyond the single-player path.

---

## 2. System architecture

```
┌───────────────────────┐
│  React + Vite SPA     │   board, voice controls, dashboards
│  (chess.js UI model;  │   browser Web Speech API (STT + TTS)
│   authoritative state │
│   lives server-side)  │
└──────────┬────────────┘
           │ HTTPS / JWT
┌──────────▼────────────┐
│  FastAPI gateway      │   routers · auth · DTOs · WebSockets for session
│  (Uvicorn workers)    │   middleware: request id, rate limit, auth
└──┬────────┬───────────┘
   │        │
   │        │  (process-local calls; same web dyno in MVP)
   │        ▼
   │   ┌─────────────────────────────┐
   │   │  Session orchestrator       │   turn FSM, hint accounting,
   │   │  (chess_rules + repertoire  │   randomized sampler w/ seed,
   │   │   + engine_cache + coach)   │   mastery event emitter
   │   └──────┬───────────┬──────────┘
   │          │           │
   │   ┌──────▼──────┐ ┌──▼──────────────┐
   │   │ Stockfish   │ │ Grounded LLM    │
   │   │ worker      │ │ coach adapter   │
   │   │ (singleton  │ │ (Grok default,  │
   │   │  per worker,│ │  schema-locked) │
   │   │  asyncio    │ │                 │
   │   │  lock;      │ │                 │
   │   │  cache is   │ │                 │
   │   │  primary)   │ │                 │
   │   └─────────────┘ └─────────────────┘
   │
┌──▼──────────────────────────────────────┐
│  Background worker (same codebase,      │   Alembic migrations, PGN
│  separate Render service)               │   import jobs, deep engine
│                                         │   analysis, opponent tree
│  Reads ImportJob rows, runs the heavy   │   builds, scheduled mastery
│  stuff, writes results back.            │   decay sweeps.
└──┬───────────┬──────────────────────────┘
   │           │
┌──▼───────┐ ┌─▼────────────┐
│Postgres16│ │   Redis 7    │   session state, job queue, rate limits
│(managed) │ │  (managed)   │   (fallback: in-memory for dev)
└──────────┘ └──────────────┘
```

Key points:

- A single codebase ships as two Render services: one web (FastAPI /
  Uvicorn), one worker (ARQ/RQ consuming Redis). Both import the same
  Python package; the worker just runs `arq chessmind.worker:settings`.
- The chess rules module (`python-chess` wrapper), the engine cache, and
  the coach adapter are **process-local libraries**, not microservices. We
  avoid network hops inside what is really one request.
- Session state is persisted every turn so a disconnected voice session
  reconnects to the same board.
- The LLM never touches raw user requests. The orchestrator builds the
  schema-locked prompt and verifies the response before anything reaches
  the UI (see §6).

---

## 3. Tech stack and runtime posture

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12 | Mature chess + ML ecosystem; FastAPI's native fit. |
| Web | FastAPI + Uvicorn + WebSockets | Async I/O for streaming voice + engine. |
| ORM / migrations | SQLAlchemy 2.x (async) + Alembic | Standard, migrates well on Render. |
| Database | **PostgreSQL 16 everywhere** | Resolved — see §11.1. SQLite is explicitly not supported, including for tests. |
| Cache / queue | Redis 7 (managed on Render; local Docker) | Session state, job queue, rate limits. |
| Jobs | ARQ (asyncio-native) or RQ | Picks up `ImportJob` rows, runs heavy work off the web dyno. |
| Chess rules | `python-chess` | §3 hard rule in `CLAUDE.md`. |
| Engine | Stockfish (latest validated stable) | Single long-lived UCI process per worker, guarded by an asyncio lock; cache remains the primary path. See §6.2. |
| LLM | Grok via provider adapter | Swappable; see §6.3. |
| Speech | Browser Web Speech API | MVP latency path; adapter seam for server-side STT/TTS post-MVP. |
| Frontend | React 18 + Vite + TypeScript | Fast dev loop; good library coverage. |
| Board UI | `react-chessboard` + `chess.js` (UI only — server is authoritative) | Prevents client/server drift. |
| Auth | Argon2 passwords, JWT access + refresh | No third-party auth dependency for MVP. |
| Deploy | Render | §9. |
| CI | GitHub Actions | lint, type, test, container build. |

**Python package tooling:** `uv` + `pyproject.toml`. Lockfile checked in.

**Frontend package tooling:** `pnpm` + `package.json`. Lockfile checked in.

---

## 4. Data model

All tables below are Postgres with SQLAlchemy 2 declarative models and
Alembic migrations. UUID primary keys (`uuid_generate_v4()`), `created_at`
/ `updated_at` timestamptz on every row.

### 4.1 Core tables

**`player`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| email | citext unique | lowercase; auth key |
| password_hash | text | argon2id |
| display_name | text | |
| rating_fide | int null | optional |
| rating_lichess | int null | optional |
| rating_chesscom | int null | optional |
| preferred_color | text | `white` / `black` / `either` |
| age_band | text null | optional |
| role | text default `player` | `player` / `coach` / `admin` |
| settings_json | jsonb default `'{}'` | board theme, TTS voice, free-reveal budget, hint policy, etc. |

**`game`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| player_id | uuid fk → player.id | |
| source | text | `upload` / `lichess` / `chesscom` / `metadb` / `twic` |
| pgn | text | raw PGN with headers and comments |
| imported_from_batch | uuid fk → import_job.id null | |
| white_name | text | |
| black_name | text | |
| white_rating | int null | |
| black_rating | int null | |
| result | text | `1-0` / `0-1` / `1/2-1/2` / `*` |
| date_played | date null | |
| time_control | text null | |
| eco | text null | normalized ECO code at exit of book |
| provenance_json | jsonb | source URL, file name, upload timestamp |

**`position`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| canonical_key | text unique | normalized FEN (board + side + castling + ep legality only — move counters stripped) |
| fen | text | canonical FEN with counters zeroed |
| eco | text null | first ECO code encountered |
| opening_name | text null | first opening name encountered |
| features_json | jsonb | pawn structure tags, open/closed, piece activity flags |
| popularity | int default 0 | denormalized game-count cache |

Index: `btree(canonical_key)` unique, `gin(features_json)`.

**`move_edge`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| from_position_id | uuid fk → position.id | |
| to_position_id | uuid fk → position.id | |
| uci | text | |
| san | text | SAN rendered from the parent position |
| games_count | int default 0 | aggregate over all `game` paths that traversed this edge |
| white_wins | int default 0 | |
| black_wins | int default 0 | |
| draws | int default 0 | |

Composite unique `(from_position_id, uci)`.

**`repertoire_line`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| player_id | uuid fk | |
| color | text | `white` / `black` |
| from_position_id | uuid fk → position.id | |
| preferred_uci | text | |
| priority | int | lower = preferred |
| is_mainline | bool | **false for recursively-walked variations**; see §7.2 |
| source_game_id | uuid fk → game.id null | which PGN produced it |
| source_variation_path | text | SAN path from root `1.e4 e5 2.Nf3 (2.d4)` to disambiguate transpositions |
| comments | text null | PGN comments preserved verbatim |
| nags_json | jsonb | NAG codes attached in the PGN |

**`engine_analysis`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| position_id | uuid fk | |
| engine | text | `stockfish` |
| engine_version | text | e.g. `17.0` |
| depth | int | |
| multipv | int | configured MultiPV lines |
| nodes | bigint | |
| time_ms | int | |
| perspective | text | always `white` (we store white-centric; UI flips) |
| results_json | jsonb | array of `{line_rank, cp, mate, pv_uci[], pv_san[]}` |
| cached_at | timestamptz | |

Index: `(position_id, engine_version, depth DESC)` so cache lookups pick
the deepest usable row.

**`opponent_profile`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| owner_player_id | uuid fk | the player preparing against this opponent |
| display_name | text | |
| aliases_json | jsonb | list of handles / FIDE id / Chess.com + Lichess usernames |
| games_imported | int default 0 | |
| last_import_at | timestamptz null | |

**`opponent_move_stat`** — materialized conditional frequencies for the
opponent tree (built by the worker after each import).

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| opponent_profile_id | uuid fk | |
| position_id | uuid fk | |
| color | text | side the opponent was playing in this slice |
| move_uci | text | |
| games_count | int | |
| wins | int | |
| losses | int | |
| draws | int | |
| recency_weight | real | time-decayed share |
| sample_size_bucket | text | `small` / `medium` / `large` for confidence surfacing |

Composite unique `(opponent_profile_id, position_id, color, move_uci)`.
Index: `(opponent_profile_id, position_id, games_count DESC)` to serve the
top-20-per-position query (see §5 and §11.4).

**`training_session`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| player_id | uuid fk | |
| mode | text | `guided` / `blindfold` / `tournament` / `visualization_q` / `opponent` |
| opening_scope_json | jsonb | ECO filters, named-opening set, repertoire slice, random |
| opponent_profile_id | uuid fk null | |
| seed | bigint | reproducible randomness |
| settings_json | jsonb | snapshot of relevant player settings at session start |
| started_at | timestamptz | |
| ended_at | timestamptz null | |

**`training_attempt`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| session_id | uuid fk | |
| position_id | uuid fk | |
| played_uci | text | |
| best_uci_list_json | jsonb | Stockfish top-N UCIs considered best |
| classification | text | `best` / `playable` / `inaccuracy` / `mistake` / `blunder` |
| eval_before_cp | int null | mate scores stored in a separate column |
| eval_after_cp | int null | |
| eval_swing_cp | int null | |
| latency_ms | int | time from prompt → player move committed |
| hints_used | int default 0 | **blindfold reveals beyond the free budget count here** (§11.3) |
| visualization_score | real null | 0–1; reduced when `hints_used > free_reveals_budget` |
| feedback_json | jsonb | the coach adapter's grounded explanation + evidence refs |

**`mastery_state`**

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| player_id | uuid fk | |
| position_id | uuid fk | |
| recall_score | real | 0–1 |
| understanding_score | real | 0–1 |
| visualization_score | real | 0–1 |
| speed_score | real | 0–1 |
| state | text | `untested` / `emerging` / `competent` / `mastered` / `decayed` |
| due_at | timestamptz | spaced-repetition due date |
| last_tested_at | timestamptz null | |

Composite unique `(player_id, position_id)`.

**`import_job`** — the single async entry point for every bulk ingest.

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| player_id | uuid fk | |
| kind | text | `player_pgn` / `opponent_pgn` / `seed` |
| opponent_profile_id | uuid fk null | |
| source | text | `upload` / `lichess_url` / `chesscom_url` / `metadb_file` / `twic` |
| status | text | `queued` / `running` / `succeeded` / `failed` / `partial` |
| progress_games | int | |
| total_games | int | |
| error | text null | |
| artifacts_json | jsonb | counts of positions created, edges created, ECOs touched |
| started_at | timestamptz null | |
| finished_at | timestamptz null | |

### 4.2 Position identity rule

`canonical_key` is a normalized FEN: board + side-to-move + castling
rights + en-passant target *only when a legal en-passant capture is
actually available on this position*. Halfmove and fullmove counters are
stripped. This makes transpositions converge to the same `position.id`
regardless of move order, which is what the opening graph needs.

Historical move order lives separately on `repertoire_line` and `game`
rows — the FEN-only tree would otherwise lose the PGN narrative.

---

## 5. API surface

JSON over HTTPS, JWT bearer auth. All list endpoints take `?limit=` with a
sane default; **we do not use cursor pagination in the MVP** (see §11.4).

| Endpoint | Purpose |
| --- | --- |
| `POST /auth/register` · `POST /auth/login` · `POST /auth/refresh` | account lifecycle |
| `GET  /players/me` · `PATCH /players/me` | profile + `settings_json` edits |
| `POST /imports/pgn` | upload games/repertoire; returns `ImportJob` id; async |
| `GET  /imports/{id}` | poll status + artifacts |
| `GET  /players/{id}/chess-dna` | style estimates + confidence + evidence |
| `GET  /openings/tree?eco=&depth=&popularity=&rating=` | universal opening graph query |
| `GET  /positions/{canonical_key}` | legal moves + stats + preferred repertoire + cached eval |
| `POST /sessions` | start a session (mode, opening scope, opponent, seed) |
| `POST /sessions/{id}/move` | validate move; return board state, classification, feedback, next opponent move |
| `WS   /sessions/{id}/voice` | streaming turn-based voice; messages carry recognized SAN candidates + spoken response payloads |
| `POST /sessions/{id}/reveal` | consume a blindfold reveal (free up to budget; beyond that increments `hints_used`) |
| `POST /analysis/position` | request Stockfish MultiPV; serves cache then triggers compute |
| `POST /opponents` · `GET /opponents` | opponent profile CRUD |
| `POST /opponents/import` | bulk PGN opponent ingestion; async via `ImportJob` |
| `GET  /opponents/{id}/tree?position=&color=&limit=20` | top-N moves per position; **limit default 20, no cursor** |
| `GET  /players/{id}/mastery` | mastery scores, due drills, weak branches, analytics |

**Added 2026-10-29 (as built):**

| Endpoint | Purpose |
| --- | --- |
| `GET  /openings/tree` → `masters` block | real Lichess Masters stats for the root (source, status, fetched_at, sample size, per-move W/D/B), kept apart from local counts (`local_stats_note`) |
| `GET  /openings/masters?fen=` | the same block for any legal position, in the local graph or not |
| `POST /sessions/{id}/ask` | answer a board question (piece location, square contents, hanging pieces, side to move, evaluation) from the session FEN via python-chess; eval from the cache-first Stockfish row |
| `POST /sessions/{id}/quiz` · `POST /sessions/{id}/quiz/{qid}/answer` · `GET /sessions/{id}/questions` | seeded visualization quiz about the current board; spoken/typed answers scored against facts captured at question time; persisted in `position_question` |

Lichess explorer data is cached in `master_explorer_cache` (canonical FEN,
TTL) and mirrored into `master_move_stat`, so the §8.3 master tier is fed
by real data once positions have been viewed. Network calls never sit in
the move hot path: the sampler only reads the table.

Error shape is uniform: `{ "error": { "code": "...", "message": "...",
"details": {} } }` with HTTP status reflecting the class.

---

## 6. Chess subsystems

### 6.1 Rules module (`chessmind.chess.rules`)

Wraps `python-chess`:

- `parse_pgn(stream) -> list[GameRecord]`
- `normalize_position(board) -> CanonicalKey`
- `legal_moves(board) -> list[Move]`
- `resolve_spoken_move(text, board) -> MoveResolution` returning either a
  unique legal move, a list of candidates for disambiguation, or an error.
- `push_san(board, san) -> Board` with structured errors for the API.

Every rules call returns rich errors; the API never swallows a parser
exception and returns 200.

### 6.2 Stockfish worker (`chessmind.services.engine`)

**Resolved shape — long-lived singleton per worker process, lock-guarded.**

- One UCI subprocess is spawned on first use and reused for the process
  lifetime (`uci`, `isready`, options set once).
- All callers go through a single `EngineClient` with a `threading.Lock`.
  (Day-1 implementation note: python-chess's `SimpleEngine` is a sync
  wrapper over subprocess pipes with its own internal event loop on a
  background thread, so a threading lock is the right level — pytest's
  per-test event loops have no affinity issues. The async surface stays
  clean via `asyncio.to_thread`.) Only one `position` + `go` sequence
  runs at a time per worker; parallel demand is served by scaling
  worker replicas, not by multiplexing one engine.
- The **cache (`engine_analysis` table) is the primary path**. The engine
  is called only when no row at the requested depth or deeper exists for
  this `canonical_key` + engine version.
- Each call records engine version, depth, nodes, time, perspective. The
  returned shape is Pydantic-validated before anything downstream sees it.
- On crash: a watchdog restarts the subprocess and the next request pays
  the ~1 s warm-up tax. The health endpoint exposes the engine's `uci`
  readiness so Render's health checks reflect reality.
- Local dev reads `STOCKFISH_PATH`; Render builds install the binary in
  the container image at a fixed location.

Rejected shape: spawn-per-request. We would pay the UCI handshake on every
move, lose the shared transposition table across turns of the same session,
and burn CPU reloading options.

### 6.3 Coach adapter (`chessmind.coach`)

Schema-locked LLM calls. The orchestrator builds the request from
deterministic facts (FEN, legal moves, player move, Stockfish PV with cp
and depth, repertoire branch, verified stat row, session mode) and asks
the LLM to produce a JSON object with:

```json
{
  "claim_type": "evaluation | plan | tactic | principle",
  "evidence_refs": ["fen:...", "pv:engine_analysis:<id>:0", "stat:move_edge:<id>"],
  "correction_san": "Nf3",
  "spoken_summary": "...",
  "follow_up_question": "..."
}
```

Validation:

1. `correction_san` is pushed through `rules.push_san` against the live
   board; a reject fails the response and we fall back to a deterministic
   template.
2. Every `evidence_ref` must resolve to a row we actually fetched for this
   turn; unknown refs fail validation.
3. The LLM never writes an evaluation number into prose — it reads one off
   the Stockfish row we handed it.

Provider seam: `CoachProvider` interface, `GrokProvider` implementation,
and a `TemplateFallbackProvider` that uses the Stockfish PV directly for
when the LLM is unavailable or over budget.

### 6.4 Voice (`chessmind.voice`)

MVP uses the browser Web Speech API end-to-end:

- **STT:** `SpeechRecognition` streams interim results; the frontend sends
  final hypotheses to `WS /sessions/{id}/voice`. The server resolves them
  via `rules.resolve_spoken_move` and either commits the move or returns a
  disambiguation prompt.
- **TTS:** `SpeechSynthesisUtterance` on the browser. The spoken text is
  the coach's `spoken_summary` plus the next opponent move announcement.
  Voice selection — see §11.5.
- **Server-side streaming** (post-MVP): the `VoiceProvider` interface is
  already present so we can swap in a server-side streaming provider
  without reshaping the orchestrator.

---

## 7. PGN import and the opening graph

### 7.1 Pipeline

```
upload → ImportJob queued → worker picks up → python-chess parse →
for each game:
  normalize headers (source, players, ratings, time control, date)
  walk game tree (see 7.2)
  upsert position + move_edge rows
  attach repertoire_line rows when ownership is player + color matches
  update move_edge aggregate counts
→ job marked succeeded with artifacts_json counts
```

### 7.2 Variation walking — resolved (§11.2)

The importer walks the PGN variation tree **recursively**:

- A depth-first traversal of `game.mainline` emits the mainline edges with
  `is_mainline=true`.
- At every node we also recurse into `node.variations[1:]`; those edges
  (and the repertoire-line rows attached from them) land with
  `is_mainline=false`.
- Comments and NAGs attach to the edge / repertoire-line record at the
  move they decorate.
- Transpositions resolve automatically because every position is upserted
  by `canonical_key`.

A seed PGN library may be flattened per-line (one PGN per final leaf) as
a fallback when a source produces a tree that `python-chess` cannot walk
cleanly. The runtime behavior is unchanged — flattened seeds just produce
multiple mainline paths.

### 7.3 Opening graph queries

`GET /openings/tree` serves a materialized slice of `position` +
`move_edge` filtered by ECO, popularity, and rating band. Deep expansion
is lazy — a position is in the graph if it has at least one `move_edge`
pointing out of it; the frontend requests the next layer on demand.

---

## 8. Opponent modeling

### 8.1 Import

`POST /opponents/import` queues an `ImportJob` with `kind=opponent_pgn`.
The worker parses the PGNs, writes `game` rows attributed to the opponent
profile, walks the tree the same way §7.2 describes, and after the import
rebuilds `opponent_move_stat` for every position the opponent reached.

### 8.2 Tree query — resolved (§11.4)

`GET /opponents/{id}/tree?position=<canonical_key>&color=<w|b>&limit=20`
returns up to 20 moves per position by default, ordered by `games_count`
desc then `recency_weight` desc. The `limit` is bounded (max 100) to keep
response sizes predictable. **No cursor pagination** — opening trees are
narrow at the top (there are not thousands of moves played from a given
position) and the dashboards consume the result as a single payload.

### 8.3 Digital twin sampler

Given a position + color, the sampler draws a move from the opponent's
conditional distribution with:

- Laplace smoothing against the broader `move_edge` distribution for the
  same position when the opponent has fewer than `k` samples here.
- A novelty quota (configurable per session) that occasionally picks a
  Stockfish-reasonable move the opponent has not played, so sessions do
  not degenerate to pure replay.
- A session seed so playbacks are reproducible.

Confidence and sample size ship on every surfaced row; the UI never shows
a "the opponent plays X" without them.

---

## 9. Deployment and environments

### 9.1 Target: Render

- **Web service:** FastAPI + Uvicorn behind Render's HTTPS. Dockerfile
  builds a slim image, installs Stockfish into `/opt/stockfish/`, pins
  the engine binary version.
- **Worker service:** same container image, different start command
  (`arq chessmind.worker:settings`).
- **Managed Postgres 16:** daily backups, point-in-time recovery on paid
  plans.
- **Managed Redis 7:** session state + job queue + rate limits.
- **Env group:** `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `GROK_API_KEY`,
  `STOCKFISH_PATH`, `SENTRY_DSN`.
- **Deploy flow:** push to `main` → GitHub Actions runs tests → Render
  auto-deploys from the merged commit.

**As built (2026-10-29, `render.yaml`): deviates from the plan above.**

- **API:** native Python runtime, not Docker. It runs as a free-plan web
  service in `singapore`. `scripts/render_build.sh` downloads the pinned
  official Stockfish build (`sf_17.1`, AVX2 with an SSE4.1 fallback) into
  `engines/`, installs `./backend`, runs `alembic upgrade head` and seeds the
  opening graph idempotently (`seed_games.py --skip-if-seeded`). The free
  plan has no pre-deploy hook, so migrations and the seed run in the build.
- **Frontend:** Render static site (`frontend/dist`) with an SPA rewrite.
  `VITE_API_BASE_URL` comes from the API service's `host`.
- **Postgres:** Neon (external), not Render-managed. The app accepts the
  Neon URL as-is. `chessmind.config.normalize_database_url` adds the driver
  prefix. For asyncpg it renames `sslmode`→`ssl` and drops
  `channel_binding`. psycopg/Alembic keeps both.
- **No worker, no Redis:** import jobs are in-process asyncio tasks. The
  worker and Redis stay planned.
- **Env:** `DATABASE_URL` (prompted, `sync: false`), `JWT_SECRET` (Render
  generated; boot refuses the dev placeholder in production),
  `STOCKFISH_PATH`, `CORS_ORIGINS` + `CORS_ORIGIN_REGEX`, `PYTHON_VERSION`.
  There is no `REDIS_URL`, `GROK_API_KEY` or `SENTRY_DSN` yet.
- **No GitHub Actions yet.** Render auto-deploys from the branch it tracks.

### 9.2 Local

`docker compose up` brings up:

- `db` — `postgres:16`.
- `redis` — `redis:7-alpine`.
- `backend` — mounts source, runs Uvicorn with `--reload`.
- `worker` — mounts source, runs `arq`.
- `frontend` — Vite dev server with HMR.

Alembic migrations run via `docker compose run --rm backend alembic
upgrade head`. **No SQLite anywhere** — tests also use a disposable
Postgres container (CI service container; local via docker-compose
override).

### 9.3 Free-tier cold start

Render's free web plan sleeps after ~15 minutes idle. First post-sleep
request pays container boot + Stockfish warm-up (~30–60 s). Mitigations:

- A keep-warm cron that pings `/health` every 10 minutes while a user is
  active (frontend-driven, so we don't burn free-tier CPU for nothing).
- Pre-warm from the "start session" button before the voice loop opens.
- Upgrade to Starter when the project is funded.

Called out in `README.md` under Known Gaps.

---

## 10. Non-functional requirements

- **No illegal moves ever.** The parser is the arbiter; every API endpoint
  that accepts a move runs it through `rules.push_san`.
- **Deterministic simulation.** Every randomized sampler takes a session
  seed; replays are byte-identical.
- **Engine evaluation metadata.** Every eval surfaced to the UI carries
  engine version, depth, and perspective; no "accuracy" claim stronger
  than the depth supports.
- **Observability.** Request-id propagated across web and worker; coach
  calls log token counts and latency; Stockfish calls log depth, nodes,
  time, cache-hit flag.
- **Privacy.** Player and opponent data are isolated; deletion endpoints
  wipe owned games and derived stats; private PGNs never bleed into the
  shared opening graph aggregates without consent (configurable).
- **Accessibility.** Keyboard board controls; captions on every spoken
  turn; push-to-talk and text-input fallbacks for voice sessions.
- **Cost controls.** Analysis cache first; per-user Stockfish time budget
  per session; LLM token budget per session; both configurable.

---

## 11. Decisions log (previously open; now resolved)

Section 11 in the prior draft held the open decisions. All five are now
resolved; the resolution is inline below and reflected throughout the rest
of this document.

### 11.1 Database — **Resolved: Postgres everywhere**

- Postgres 16 both in production (Render managed) and locally (Docker
  Postgres service in `docker-compose.yml`).
- SQLAlchemy 2.x with the async driver; Alembic manages schema.
- **SQLite is not supported** as a runtime or test backend. CI runs
  against a Postgres service container; local tests either reuse the
  compose database or spin a disposable `postgres:16` container.

### 11.2 PGN import — **Resolved: recursive variation walk**

- The importer walks `game.mainline` and recurses into every
  `node.variations[1:]`.
- Sublines land with `is_mainline=false` on `repertoire_line` and the
  corresponding `move_edge` records; mainline rows keep
  `is_mainline=true`.
- Comments and NAGs attach to the edge / line record at the move they
  decorate, matching the PGN semantics.
- Transpositions converge via `canonical_key`.
- Seed PGN libraries may be shipped as per-line flattened files as a
  fallback; the runtime path is identical, flattened seeds just produce
  additional mainline paths.

### 11.3 Blindfold reveals — **Resolved: 2 free per session, settings-driven**

- Each `training_session` of mode `blindfold` starts with a free reveal
  budget sourced from
  `player.settings_json.blindfold_free_reveals_per_session` (default
  `2`, admin-adjustable, per-player override).
- The first N reveals (up to the budget) are free and do not touch the
  attempt score.
- Each reveal beyond the budget:
  - increments `training_attempt.hints_used`;
  - applies a configurable per-reveal penalty
    (`settings_json.blindfold_reveal_penalty`, default `0.15`) subtracted
    from `visualization_score`, floored at `0.0`;
  - is logged in the session feed so the dashboard shows where the
    player leaned on reveals.

### 11.4 Opponent tree — **Resolved: `?limit=` top-20 default, no cursor**

- `GET /opponents/{id}/tree` takes a `limit` query parameter (default
  `20`, max `100`) and returns the top moves per position by
  `games_count` desc, `recency_weight` desc.
- Imports stay asynchronous via `ImportJob` with `kind=opponent_pgn`; the
  tree query is purely a read against the materialized
  `opponent_move_stat` table.
- Cursor pagination is **not** implemented — opening trees are narrow
  enough at the top that a single bounded payload is simpler and faster.

### 11.5 TTS — **Resolved: browser default + populated voice dropdown**

- Default voice: whatever the browser returns as the default on load.
- The frontend calls `window.speechSynthesis.getVoices()` on mount and
  again on the `voiceschanged` event (required for Chrome, which
  populates the list asynchronously); the resulting list drives a
  settings dropdown. A "Loading voices…" state covers the first-mount
  empty-list case.
- The player's choice is persisted in
  `player.settings_json.tts_voice` as `{ "name": "...", "lang": "..." }`
  and reapplied on subsequent sessions.
- A `VoiceProvider` interface is present on the backend so a later
  server-side streaming TTS can be swapped in without reshaping the
  session orchestrator.

---

## 12. Testing strategy

- **Unit — chess rules.** SAN parsing, castling, en passant, promotion,
  check, mate, draw by threefold / fifty-move / stalemate, malformed PGN,
  transposition convergence.
- **Unit — importer.** Variation walk recurses to the correct depth;
  `is_mainline` flags are correct; comments and NAGs survive; aggregate
  counts on `move_edge` match the number of games.
- **Unit — engine cache.** Deeper rows satisfy shallower requests;
  version bumps invalidate.
- **Integration — Stockfish worker.** Lock serialization is correct under
  concurrent request fan-out; crash recovery works.
- **Integration — voice resolver.** Spoken forms resolve to legal moves;
  ambiguity is surfaced not guessed.
- **Integration — opponent tree.** Rebuilds after import; `?limit=` is
  honored; frequency ordering matches raw counts.
- **End-to-end — acceptance scenarios.** The 13 scenarios from the
  handoff's §10 run against a seeded Postgres and a real Stockfish binary.
- **Database testing.** All of the above use a real Postgres (CI service
  container, local Docker). SQLite is not a supported test backend.

---

## 13. Five-day phase mapping

| Day | Modules landed | Proof |
| --- | --- | --- |
| 1 | `chessmind.chess.rules`, `chessmind.chess.engine` (singleton + lock), `chessmind.importer` (recursive walk), `chessmind.openings`, auth, migrations, seed script | Import 100+ games, explore the opening tree. |
| 2 | `chessmind.voice` (browser-side), `chessmind.sessions` (guided + blindfold with free-reveal budget), coach adapter v1, Stockfish-grounded feedback, TTS voice dropdown | Spoken Italian line, unexpected sideline, correction. |
| 3 | `chessmind.mastery`, `chessmind.scheduler` (spaced repetition), Chess DNA v1, visualization-question mode | Mistake → retrain → mastery score moves. |
| 4 | `chessmind.opponents` (import + `opponent_move_stat` builder + `?limit=` tree + digital-twin sampler) | 100+ opponent PGNs → frequency-weighted voice game. |
| 5 | E2E suite, GitHub Actions, Render deploy, observability wiring, honest Known Gaps | Deployed demo passes all 13 acceptance scenarios. |

Phase progress and demo evidence are tracked in `PROGRESS.md`.
