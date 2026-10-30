# ChessMind OS — Progress Tracker

A single-pane view of where the five-day MVP is. Update as items land; do
not mark a phase complete until its proof condition is demonstrated.

Last updated: 2026-10-30 — reviewer-feedback round: opening announcements (Lichess taxonomy), quiet mode, illegal-move feedback, "play <opening> against me", play as Black, opponent prep report, optional LLM wording. Previous: 2026-10-29 — Render Blueprint + deploy rehearsal; position questions/quiz; Lichess Masters adapter; ACCEPTANCE.md walk of the 13 scenarios.

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
- [x] Frontend shell: login, profile, PGN upload page, opening tree
      view. Landed with the Day-2 frontend; the upload page (`/import`)
      gained file + paste input, an intent selector, job polling and
      error display (see "UI flows proof" below).

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
- [x] Mastery states (`untested → emerging → competent → mastered →
      decayed`) with separate recall / understanding / visualization / speed
      scores. (Backend `mastery_state`; visible per-position before/after in
      the drill UI.)
- [x] Spaced-repetition scheduler (retry today / next day / interval).
      (SM-2; `POST /players/{id}/mastery/next` picks the due item.)
- [ ] Progress dashboard: weak branches, due drills, opening readiness.
- [x] Visualization questions scored against the live board: piece
      location, square contents, hanging pieces, side to move, rough eval
      (ask by voice/text, or "quiz me"; `position_question` table;
      `scripts/proof_position_questions.mjs`). Pawn structure, candidate
      moves, threatened squares and tactical motifs are not built.

**Proof:** a mistake is tracked, retrained, and the mastery score moves.
Demonstrated at the API (`scripts/proof_mastery.py`) and now in the UI
(`scripts/proof_ui_flows.mjs`: 1.g4 graded a mistake → Mastery page
"Start due drill" → answer 1.e4 → reps 0→1, interval 0→1 day, next-due
and recall move on screen, dashboard drops to 0 due).

## Phase 4 — Day 4: Opponent

- [x] Bulk opponent PGN import (`POST /opponents/{id}/import`, and
      `POST /imports/pgn` with `intent=opponent`); async via `ImportJob`;
      attribution preserved. (Dedupe by source + headers not verified.)
- [ ] `opponent_profile` + opening-tree stats filtered by recency, color,
      time control, rating. (Per-colour lines/branches now exist in the
      prep report, `GET /opponents/{id}/prep`; recency, time-control and
      rating filters still do not.)
- [x] `GET /opponents/{id}/tree?limit=N` with `limit=20` default per
      position; no cursor pagination. Browsable in the Opponents page.
- [x] Lichess Masters explorer adapter: real master move stats per
      position, TTL-cached by canonical FEN, mirrored into
      `master_move_stat`, shown with source/sample size/attribution on the
      Openings page, local-count fallback when unreachable. Live fetch is
      unverified until the Render deploy (sandbox blocks lichess); proven
      with hand-written fixtures + a local replay server.
- [x] Digital twin sampler: historical conditional move frequencies with
      smoothing / fallback to broader data; confidence and sample size on
      every row. (Seeded; `opponent_profile` tier verified from the UI.)
- [x] Opponent-specific session mode: "Play the twin" starts an
      `opponent_twin` session. Voice input is the shared session screen
      (real-mic voice is verified on the HTTPS deploy, not in CI).

**Proof:** import 100+ opponent PGNs and run a frequency-weighted voice
game. Done at the API (`scripts/proof_opponent.py`) and from the UI: 120
opponent games uploaded on `/import`, tree on `/opponents`, "Play the twin"
→ 1.d4 answered `Nf6` from the `opponent_profile` tier.

### UI flows proof — 2026-10-29

`node scripts/proof_ui_flows.mjs` drives the Vite frontend in headless
Chromium against the real FastAPI backend, Postgres 16 and Stockfish 16.
Only `speechSynthesis` is stubbed (no system voices in headless); moves are
typed, which takes the same resolver → `POST /sessions/{id}/move` path as
voice. Artifacts: `scripts/data/proof_ui_flows.json`, `ui_*.png`; fixtures
`ui_repertoire_153.pgn`, `ui_opponent_120.pgn`.

- **PGN upload (`/import`)** — intent selector (repertoire / corpus /
  opponent), file + paste, `POST /imports/pgn` then polling `GET /imports/{id}`.
  153-game file → "153 of 153 games imported · 266 positions · 1725 move
  edges · 1725 repertoire lines"; link to the opening tree shows 1.e4 = 108
  games. Corpus intent writes graph only (0 repertoire lines). Job errors
  render in the UI (opponent not in headers → `partial` + message).
  Backend: `/imports/pgn` gained `intent` (+ `opponent_profile_id`).
- **Mastery drill** — "Start due drill" → `POST /players/{id}/mastery/next` →
  session started at that FEN (`?drill=<position_id>`) → before/after
  `MasteryState` table via new `GET /players/{id}/mastery/{position_id}`.
- **Opponents** — shared import panel (120 games), conditional tree, "Play
  the twin" with a colour choice.
- **`PATCH /players/me`** already merged `settings_json` (top-level key
  merge; nested objects are replaced whole). Verified in the browser run and
  covered by `test_patch_me_merges_settings` plus a new
  profile-only/partial-patch test.
- Backend tests: 76 passing (5 new). Also fixed a test-infra bug: background
  import jobs reuse the app's module-level engine, whose pooled connections
  outlived the previous test's event loop; `conftest` now drops them per test.

## Phase 5 — Day 5: Integration & deploy

- [ ] End-to-end test suite exercising all 13 acceptance scenarios from the
      handoff. (Walked scenario by scenario in `ACCEPTANCE.md`: 8 pass,
      5 thin, with direct MetaDb out of scope. Existing proof scripts
      re-run against the deploy-shaped stack; logs in
      `scripts/data/deploy_rehearsal/`.)
- [ ] GitHub Actions: lint, type-check, backend tests, frontend tests,
      container build. (Not started.)
- [x] Render Blueprint: `render.yaml` (Python web service + static site,
      Singapore, free plan), `scripts/render_build.sh` (Stockfish
      download, pip, `alembic upgrade head`, idempotent seed). Neon URL
      normalization (driver prefix, `sslmode`→`ssl`, `channel_binding`
      dropped for asyncpg). Rehearsed locally against TLS + SCRAM Postgres.
      As built there is no worker or Redis, and Postgres is Neon rather
      than Render-managed (see ARCHITECTURE §9.1).
- [ ] Live Render deploy wired (maintainer: connect repo, paste
      `DATABASE_URL`).
- [ ] Observability: request logs with correlation id (done), Stockfish
      job metrics, LLM token accounting (not started).
- [x] Known Gaps section in `README.md` kept honest.
- [x] Measured p50/p95 for `POST /sessions/{id}/move` and
      `POST /analysis/position` on the local stack (README "Measured
      latency"; `scripts/measure_latency.py`, `scripts/data/latency.json`).

Fixed while proving the deploy: SM-2 interval cap (500 after ~15
correct reviews of one position), DB commit before response (stale reads
right after a move), and the Italian proof's retrain step.

**Proof:** deployed demo passes all 13 acceptance scenarios live.

## Reviewer feedback round — 2026-10-30

Proofs: `scripts/proof_voice_openings.mjs` (38 checks),
`scripts/proof_opponent_prep.mjs` (41 checks), and backend
`test_opening_voice.py` (33), `test_opponent_prep.py` (2),
`test_llm_coach.py` (12). Backend 194 passing, frontend 32. Details in
`ACCEPTANCE.md` → "Reviewer feedback round".

- [x] Announce the opening by voice, once per name, after either side's
      move. Names come from the vendored Lichess `chess-openings` taxonomy
      (CC0) keyed by canonical FEN. Fixed the importer smearing game
      headers onto every position (migration `0006`).
- [x] Quiet mode: session setting + UI checkbox + "quiet mode on/off".
      Only opponent moves are spoken; no coaching, quizzes or announcements.
      Legality is still checked.
- [x] Illegal-move feedback: spoken and captioned "That's an illegal move,
      try again.", a blindfold-visible alert, API 422 `illegal_move`, and
      the game does not advance.
- [x] "Play the London / Caro-Kann / Spanish / Italian / Queen's Gambit /
      Sicilian / d4 against me" for both colours. The sampler follows the
      defining line, then the family's named lines, then falls back to
      normal sampling.
- [x] Play Black with the engine opening; "play d4 against me" before the
      first move restarts the game with 1.d4.
- [x] Opponent prep: real 150-game import, `GET /opponents/{id}/prep` (lines
      by colour, branches, Stockfish suggestions), blindfold game vs the twin.
- [x] Optional LLM wording behind `LLM_EXPLANATIONS_ENABLED` + key + model,
      validated against the cited evidence, template fallback. Not tested
      against a live provider (no key here).

## Known gaps parked for post-MVP

- MetaDb direct API integration (credentials/licensing pending).
- Leela / Maia secondary engines.
- Server-side streaming STT/TTS (Day 2 uses the browser Web Speech API).
- `pgvector`-backed retrieval of coaching notes.
- Coach-side workflow beyond the single-player path.
