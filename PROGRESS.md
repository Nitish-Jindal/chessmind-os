# ChessMind OS — Progress Tracker

A single-pane view of where the five-day MVP is. Update as items land; do
not mark a phase complete until its proof condition is demonstrated.

Last updated: 2026-10-29 — Day 1 complete.

## Phase 0 — Scaffold & plan

- [x] Branch `claude/chessmind-scaffold-architecture-y2url9` created.
- [x] `.gitignore`, `CLAUDE.md`, `PROGRESS.md`, `README.md` committed.
- [x] Handoff archived under `docs/handoff/`.
- [x] `docs/ARCHITECTURE.md` drafted with resolved decisions applied.
- [x] Architecture reviewed and approved by maintainer.
- [x] Repo layout stubs (`backend/`, `scripts/`, `docker-compose.yml`)
      created and in-use. `frontend/` + `.github/workflows/` still
      pending — Day 2 and Day 5 land them.

**Proof of phase complete:** maintainer sign-off on `docs/ARCHITECTURE.md`
and the backend scaffold boots clean (uvicorn + Postgres via docker-compose).

## Phase 1 — Day 1: Foundation

- [x] Postgres 16 + Redis 7 running under `docker compose up`.
- [x] FastAPI app boot, `/health`, request-id middleware, structured
      logging.
- [x] Alembic initialized; three migrations land on `main`:
      - `0001_player` — citext extension + player table.
      - `0002_opening_graph` — game, import_job, position, move_edge,
        repertoire_line with the composite uniques and FKs.
      - `0003_engine_analysis` — engine_analysis cache with the
        `(position_id, engine_version, depth)` lookup index.
      - Day-4 tables (`opponent_profile`, `training_session`,
        `training_attempt`, `mastery_state`) land in their own phase.
- [x] Auth: email + password (argon2id), JWT access + refresh,
      `/auth/register`, `/auth/login`, `/auth/refresh`, `/players/me`
      (GET + PATCH with `settings_json` merge-patch for §11.3 blindfold
      budget and §11.5 TTS voice).
- [x] PGN importer walks variations recursively (§11.2); sublines land
      with `is_mainline=false`; comments and NAGs attach to the move
      they decorate; provenance_json preserves PGN headers.
- [x] Opening graph: canonical FEN normalization via
      `python-chess.epd(en_passant='legal')` strips move counters and
      only keeps EP target when a legal EP capture exists;
      `move_edge` upserts aggregate games_count / W / L / D; the
      transposition test proves two move orders converge on one
      Position row.
- [x] Stockfish worker: long-lived singleton UCI process per worker,
      threading.Lock-guarded with an `asyncio.to_thread` async
      surface; MultiPV configurable; `engine_analysis` cache is the
      primary path; every row stamps engine version, depth, nodes,
      time, and `perspective='white'`.
- [x] Seeded opening dataset: `scripts/seed_games.py` composes 153
      real opening lines across Italian, Ruy Lopez, Sicilian, and QGD
      family (QGD, Slav, Catalan, Nimzo-Indian) and feeds them
      through the same import pipeline the API uses.
- [ ] Frontend shell: login, profile, PGN upload page, opening tree
      view. **Deferred to Day 2** per the sprint plan.

**Proof — passed 2026-10-29:**

- `scripts/day1_proof.py` registered a player, logged in, POSTed the
  composed 153-game PGN to `/imports/pgn`, polled `/imports/{id}`
  until it reached `succeeded`, and GETed `/openings/tree?limit=10`.
- Import job finished in **15.3 s**: 153/153 games succeeded, 266
  positions touched, 1725 move edges created or bumped, 1725
  repertoire rows upserted, zero errors.
- Opening tree root (startpos):

  ```
  move    games    W    L    D
  e4        108   36   39   33
  d4         45   14   17   14
  ```

- After 1.e4 (`?fen=...`):

  ```
  move    games    W    L    D
  e5         63   21   21   21
  c5         45   15   18   12
  ```

- Totals reconcile: 108 e4 + 45 d4 = 153 games (every game played a
  first move); 63 e5 + 45 c5 = 108 (every e4 game played e5 or c5).
  Graph has 267 total edges, 266 positions.
- Full artifact: `scripts/data/day1_proof_tree.json`.
- Backend test suite: 37 passing (health, auth × 6, rules × 6,
  canonical FEN × 4, PGN import × 8, opening tree × 5, engine × 7).

## Phase 2 — Day 2: Voice

- [x] Spoken-SAN resolver (SAN, "knight f3", "castle kingside", etc.)
      constrained to legal moves; disambiguates rather than guesses.
      (`frontend/src/voice/resolver.ts`, 21 vitest cases; resolves against
      `GET /sessions/{id}/legal`, asks "Did you mean A or B?" on ambiguity.)
- [ ] Guided session: AI announces opponent moves, player speaks replies,
      board state tracked server-side.
- [ ] Blindfold session: no board; 2 free reveals per session (configurable
      in `player.settings_json`); extra reveals increment `hints_used` and
      lower the visualization sub-score on the attempt.
- [ ] Random branch generator: seeded, legal, mixes mainlines and sidelines.
- [ ] Stockfish-grounded correction after an inaccuracy / mistake / blunder
      with a stronger alternative drawn from the engine PV.
- [x] TTS: browser default voice, with a `getVoices()` dropdown populated
      on the `voiceschanged` event; choice persisted in
      `player.settings_json.tts_voice`.

**Frontend (Vite + React + TS, `frontend/`):** home, openings, session,
mastery and opponents pages against the real backend. Full-board and
blindfold sessions, push-to-talk + text fallback + live captions. Proof:
`node scripts/proof_frontend.mjs` (real browser, real API/Stockfish; only
the microphone and system voices are stubbed) — transcript and screenshots
in `scripts/data/proof_frontend.json` / `frontend_*.png`.

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
