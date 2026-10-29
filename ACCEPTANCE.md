# Acceptance — handoff §10 scenarios

Status of the 13 acceptance scenarios from
`docs/handoff/ChessMind_OS_Developer_Handoff.docx` §10, as of 2026-10-29.

- **pass**: the scenario works end to end against the real stack, with
  real Postgres and Stockfish, and a script or test proves it.
- **thin**: part of the scenario works and is proven. The missing part
  is named in the row.
- **out-of-scope**: excluded from the MVP by `CLAUDE.md` §2 or blocked
  outside the code (credentials, licensing).

"Rehearsal" means the run recorded in `scripts/data/deploy_rehearsal/`:
every proof script was re-run against the deploy-shaped stack. That stack
was built by `scripts/render_build.sh` on a fresh Postgres 16. It used a
Neon-style URL (`sslmode=require&channel_binding=require`, TLS +
SCRAM), `ENVIRONMENT=production` and Stockfish 17.1. The frontend was
built with `VITE_API_BASE_URL` as a bare host. None of this ran on Render
itself. The live check on the deployed URL is listed under scenario 13.

| # | Scenario | Status | Proof |
| --- | --- | --- | --- |
| 1 | Create account, configure preferences, import 100+ PGNs without corruption | **pass** | `scripts/day1_proof.py` (rehearsal: 153/153 games, 0 errors, 10.2 s); `scripts/proof_ui_flows.mjs` (upload page, intents, job polling, `PATCH /players/me` settings merge); `backend/tests/test_auth.py`, `test_pgn_import.py` (variations, comments/NAGs, malformed PGN). Endpoints: `POST /auth/register`, `/auth/login`, `PATCH /players/me`, `POST /imports/pgn`, `GET /imports/{id}`. |
| 2 | Repertoire tree: mainlines, sidelines, transpositions, missing branches | **thin** | Mainline/sideline (`is_mainline`) and transposition convergence onto one canonical-FEN position: `test_pgn_import.py`, `test_canonical_fen.py`, `test_openings_tree.py`; `GET /openings/tree`. **Missing:** no "missing branch" / repertoire-gap report (that belongs with Chess DNA, not built). |
| 3 | Browse and test a named opening outside the player's repertoire | **thin** | Browse: Openings page over `GET /openings/tree` with ECO / name badges (`proof_frontend.mjs`: "openings page rendered tree edges"). Test: `POST /sessions` accepts any `starting_fen`, and the drill flow uses it (`proof_ui_flows.mjs`). **Missing:** no "train from this position" button on the Openings page. Names show only where a PGN carried ECO/Opening headers. |
| 4 | Voice-only opening against randomly selected legal opponent branches | **pass** (mic verified manually) | Blindfold session with no board in the DOM, opponent replies spoken, reveal budget enforced: `proof_frontend.mjs`. Seeded sampler (`mix` / `surprise`) that never returns an illegal move: `test_sampler.py`. Seeded replays are deterministic (20/20 identical in the rehearsal). Real-microphone STT is checked by hand on the HTTPS deploy; headless runs use typed input through the same resolver → `POST /sessions/{id}/move` path. |
| 5 | Recognize spoken notation; ask for clarification on ambiguous moves | **pass** | `frontend/src/voice/resolver.test.ts` (21 cases: SAN, "knight f3", castling, ambiguity); `proof_frontend.mjs`: "ambiguous utterance shows confirmation choices", "TTS asked 'Did you mean A or B?'", "confirmation resolved to exactly one POST /move". |
| 6 | Deliberate inaccuracy → verified Stockfish eval, PV and positional explanation | **pass** | `scripts/proof_italian_session.py` (rehearsal: 3.g4 graded *mistake*, swing 187 cp, correction Bb5 with PV, then the engine's best move graded *best*); `test_labeler.py`, `test_coaching.py`. Every number comes from a persisted `engine_analysis` row (version, depth, nodes, perspective). The explanation is a deterministic template over engine and board-feature evidence (hanging pieces, material, check, castling rights). No LLM is involved. |
| 7 | Consistent White-centric evaluation and player-relative advantage | **pass** | `MoveResponse` carries `eval_before/after_cp_white` and `player_relative_before/after_cp`; engine rows are stored with `perspective='white'`. Tests: `test_labeler.py`, `test_engine.py`. Shown in the coach card ("eval (white) +0.41 → +0.21") in `proof_ui_flows.mjs`. |
| 8 | Reconstruct board from a spoken variation; validate piece-location questions | **thin** | Board reconstruction from speech is proven in blindfold mode (`proof_frontend.mjs`: board hidden, moves spoken, reveal shows the position the server tracked). **Missing:** piece-location / visualization questions. `visualization_q` exists only as a session-mode value and has no question generator or grader. |
| 9 | Track errors, response latency, hint usage and the next spaced-repetition drill | **pass** | `training_attempt` stores classification, eval swing, `latency_ms` and `hints_used`. SM-2 is in `mastery_state`, and `POST /players/{id}/mastery/next` picks the due drill. `scripts/proof_mastery.py` (rehearsal: 1.g4 mistake → due now → retrain 1.e4 → reps 0→1, interval 0→1 day) and `proof_ui_flows.mjs` (same loop in the UI); `test_sm2.py`, `test_mastery_api.py`. |
| 10 | Import 100+ opponent PGNs and run an opponent-conditioned simulated game | **pass** | `scripts/proof_opponent.py` (rehearsal: 120 games imported in 7.1 s; after 1.d4 the twin played Nf6 from the `opponent_profile` tier, confidence 1.0, sample 60); `proof_ui_flows.mjs` ("Play the twin"). Endpoints: `POST /opponents`, `POST /opponents/{id}/import`, `GET /opponents/{id}/tree`. The handoff's `POST /opponents/import` is `POST /opponents/{id}/import` here. |
| 11 | Real open/master game statistics with source and sample size | **thin** | Source and sample size are on every sampler pick (`source_tier`, `sample_size`, `confidence`) and every tree edge (`games_count`, W/L/D) (`test_sampler.py`, `proof_opponent.py`). **Missing:** real master data. `master_move_stat` is empty, and the Lichess masters fetch (planned to run on the deployed host) is not written. The seeded W/L/D are assigned results, not real ones. Real statistics exist only for PGNs a user imports. |
| 12 | MetaDb-compatible import; direct MetaDb only with authorized credentials | **thin** / direct access **out-of-scope** | PGN exports from MetaDb / ChessBase import through `POST /imports/pgn` (`test_pgn_import.py`, `day1_proof.py`). No vendor-specific adapter or fixture has been tested against a real MetaDb export. Direct MetaDb / ChessBase API access is out of scope: there are no credentials or licence, and nothing in the repo calls either vendor. |
| 13 | Deploy a working application with environment configuration, tests, logs and README | **thin** | `render.yaml` (API + static site, Singapore, no secrets), `scripts/render_build.sh` (Stockfish + deps + `alembic upgrade head` + idempotent seed), Neon URL handling (`backend/tests/test_config.py`). Rehearsed end to end as described above. 86 backend tests and 26 frontend tests pass. Request-id middleware, with uvicorn access logs going to Render's log stream. Latency is measured (README). README covers setup, deploy and gaps. **Missing:** the live Render deploy (the maintainer connects the repo and secrets), a GitHub Actions CI workflow, and structured metrics (Stockfish job metrics, LLM token accounting). |

## Live check on the deployed URL (to do after Render wiring)

1. `curl https://<api>/health` → `{"status":"ok"}`.
2. Re-run the API proofs against it, for example
   `python scripts/proof_italian_session.py --base-url https://<api>`. The
   same applies to `day1_proof.py`, `proof_mastery.py` and
   `proof_opponent.py`.
3. `python scripts/measure_latency.py --base-url https://<api> --out scripts/data/latency_render.json`
   to get real deployed p50/p95.
4. In Chrome on the static-site URL: push-to-talk a full Italian line in a
   blindfold session, say an ambiguous move ("knight d2" with both knights
   able to go there) and confirm the clarification prompt (scenarios 4, 5
   and 8 with a real microphone).

## Notes from the rehearsal

- `proof_ui_flows.mjs` passed every check except "1.e4 = 108 games". That
  assertion assumes an empty database. The rehearsal DB already held the
  deploy seed plus the day-1 proof import, so the global graph showed
  324 = 3 × 108. The import itself reported 153/153.
- Three defects were found and fixed while proving the deploy (details in
  the PR):
  1. `POST /sessions/{id}/move` returned 500 after ~15 correct reviews of
     one position. SM-2 intervals grew without a cap and overflowed
     `datetime`.
  2. The DB commit ran after the HTTP response was sent, so an immediate
     follow-up read could see the old board (1/96 stale reads before the
     fix, 0/288 after).
  3. `proof_italian_session.py` replayed a correction for the position
     before the opponent's reply.
