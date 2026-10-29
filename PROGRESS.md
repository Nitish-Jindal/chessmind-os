# ChessMind OS — Progress Tracker

A single-pane view of where the five-day MVP is. Update as items land; do
not mark a phase complete until its proof condition is demonstrated.

Last updated: 2026-10-29 — Phase 0 scaffold in flight.

## Phase 0 — Scaffold & plan

- [x] Branch `claude/chessmind-scaffold-architecture-y2url9` created.
- [x] `.gitignore`, `CLAUDE.md`, `PROGRESS.md`, `README.md` committed.
- [x] Handoff archived under `docs/handoff/`.
- [x] `docs/ARCHITECTURE.md` drafted with resolved decisions applied.
- [ ] Architecture reviewed and approved by maintainer.
- [ ] Repo layout stubs (`backend/`, `frontend/`, `docker-compose.yml`,
      `.github/workflows/`) created — gated on approval.

**Proof of phase complete:** maintainer sign-off on `docs/ARCHITECTURE.md`
and a green CI run on the empty project skeleton.

## Phase 1 — Day 1: Foundation

- [ ] Postgres 16 + Redis 7 running under `docker compose up`.
- [ ] FastAPI app boot, health endpoint, request-id middleware, structured
      logging.
- [ ] Alembic initialized; first migration covers `player`, `game`,
      `position`, `move_edge`, `repertoire_line`, `engine_analysis`,
      `opponent_profile`, `training_session`, `training_attempt`,
      `mastery_state`, `import_job`.
- [ ] Auth: email + password (argon2), JWT access + refresh, profile CRUD.
- [ ] PGN importer walks variations recursively; sublines land with
      `is_mainline=false`; preserves comments, NAGs, source attribution.
- [ ] Opening graph build: canonical position identity (normalized FEN),
      `move_edge` upserts, transpositions converge.
- [ ] Stockfish worker: single long-lived UCI process per worker, guarded
      by an asyncio lock; MultiPV configurable; `engine_analysis` cache
      is the primary path.
- [ ] Seeded opening dataset (ECO A00–E99 headers + a curated subset of
      mainlines) loaded via `scripts/seed_openings.py`.
- [ ] Frontend shell: login, profile, PGN upload page, opening tree view.

**Proof:** import 100+ games and explore the opening tree.

## Phase 2 — Day 2: Voice

- [ ] Spoken-SAN resolver (SAN, "knight f3", "castle kingside", etc.)
      constrained to legal moves; disambiguates rather than guesses.
- [ ] Guided session: AI announces opponent moves, player speaks replies,
      board state tracked server-side.
- [ ] Blindfold session: no board; 2 free reveals per session (configurable
      in `player.settings_json`); extra reveals increment `hints_used` and
      lower the visualization sub-score on the attempt.
- [ ] Random branch generator: seeded, legal, mixes mainlines and sidelines.
- [ ] Stockfish-grounded correction after an inaccuracy / mistake / blunder
      with a stronger alternative drawn from the engine PV.
- [ ] TTS: browser default voice, with a `getVoices()` dropdown populated
      on the `voiceschanged` event; choice persisted in
      `player.settings_json.tts_voice`.

**Proof:** complete a spoken Italian line, hit an unexpected sideline, and
receive a corrective explanation.

## Phase 3 — Day 3: Adaptive

- [ ] Chess DNA v1: style estimates from played games (open/closed,
      initiative, pawn structure) with confidence and evidence.
- [ ] `training_attempt` captures played move, best alternatives, eval
      swing, latency, hints used.
- [ ] Mastery states (`untested → emerging → competent → mastered →
      decayed`) with separate recall / understanding / visualization / speed
      scores.
- [ ] Spaced-repetition scheduler (retry today / next day / interval).
- [ ] Progress dashboard: weak branches, due drills, opening readiness.
- [ ] Visualization questions (hanging pieces, pawn structure, candidate
      moves) scored against the live board.

**Proof:** a mistake is tracked, retrained, and the mastery score moves.

## Phase 4 — Day 4: Opponent

- [ ] Bulk opponent PGN import under `POST /opponents/import`; async via
      `ImportJob`; dedupe by source + headers; attribution preserved.
- [ ] `opponent_profile` + opening-tree stats filtered by recency, color,
      time control, rating.
- [ ] `GET /opponents/{id}/tree?limit=N` with `limit=20` default per
      position; no cursor pagination.
- [ ] Digital twin sampler: historical conditional move frequencies with
      smoothing / fallback to broader data; confidence and sample size on
      every row.
- [ ] Opponent-specific voice session mode.

**Proof:** import 100+ opponent PGNs and run a frequency-weighted voice
game.

## Phase 5 — Day 5: Integration & deploy

- [ ] End-to-end test suite exercising all 13 acceptance scenarios from the
      handoff.
- [ ] GitHub Actions: lint, type-check, backend tests, frontend tests,
      container build.
- [ ] Render deploy: web service, worker, managed Postgres, managed Redis,
      env group wired; manual deploy script documented.
- [ ] Observability: request logs with correlation id, Stockfish job
      metrics, LLM token accounting.
- [ ] Known Gaps section in `README.md` kept honest (free-tier cold start,
      MetaDb vendor status, LLM latency notes).

**Proof:** deployed demo passes all 13 acceptance scenarios live.

## Known gaps parked for post-MVP

- MetaDb direct API integration (credentials/licensing pending).
- Leela / Maia secondary engines.
- Server-side streaming STT/TTS (Day 2 uses the browser Web Speech API).
- `pgvector`-backed retrieval of coaching notes.
- Coach-side workflow beyond the single-player path.
