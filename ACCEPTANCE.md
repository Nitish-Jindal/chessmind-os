# Acceptance — handoff §10 scenarios

Status of the 13 acceptance scenarios from
`docs/handoff/ChessMind_OS_Developer_Handoff.docx` §10, as of 2026-10-29
(updated the same day for position questions and Lichess master stats, and
on 2026-10-30 for the reviewer-feedback round; see the section after the
table).

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
| 3 | Browse and test a named opening outside the player's repertoire | **thin** | Browse: Openings page over `GET /openings/tree` with ECO / name badges (`proof_frontend.mjs`: "openings page rendered tree edges"). Test: `POST /sessions` accepts any `starting_fen`, and the drill flow uses it (`proof_ui_flows.mjs`). Opening names now come from the Lichess `chess-openings` taxonomy (CC0) for every named position, and sessions announce them. The player can make the opponent play any named opening ("play the Caro-Kann against me"; `proof_voice_openings.mjs`). **Missing:** no "train from this position" button on the Openings page. |
| 4 | Voice-only opening against randomly selected legal opponent branches | **pass** (mic verified manually) | Blindfold session with no board in the DOM, opponent replies spoken, reveal budget enforced: `proof_frontend.mjs`. Seeded sampler (`mix` / `surprise`) that never returns an illegal move: `test_sampler.py`. Seeded replays are deterministic (20/20 identical in the rehearsal). Real-microphone STT is checked by hand on the HTTPS deploy; headless runs use typed input through the same resolver → `POST /sessions/{id}/move` path. Illegal spoken moves now get a spoken and captioned "That's an illegal move, try again." and a visible alert in blindfold, and the game does not advance (`proof_voice_openings.mjs` phase B). |
| 5 | Recognize spoken notation; ask for clarification on ambiguous moves | **pass** | `frontend/src/voice/resolver.test.ts` (21 cases: SAN, "knight f3", castling, ambiguity); `proof_frontend.mjs`: "ambiguous utterance shows confirmation choices", "TTS asked 'Did you mean A or B?'", "confirmation resolved to exactly one POST /move". |
| 6 | Deliberate inaccuracy → verified Stockfish eval, PV and positional explanation | **pass** | `scripts/proof_italian_session.py` (rehearsal: 3.g4 graded *mistake*, swing 187 cp, correction Bb5 with PV, then the engine's best move graded *best*); `test_labeler.py`, `test_coaching.py`. Every number comes from a persisted `engine_analysis` row (version, depth, nodes, perspective). The explanation is a deterministic template over engine and board-feature evidence (hanging pieces, material, check, castling rights). An optional LLM layer can reword it: off by default, it needs `LLM_API_KEY` + `LLM_MODEL`, and it is validated against the cited evidence with the template as fallback (`test_llm_coach.py`; no live provider tested). |
| 7 | Consistent White-centric evaluation and player-relative advantage | **pass** | `MoveResponse` carries `eval_before/after_cp_white` and `player_relative_before/after_cp`; engine rows are stored with `perspective='white'`. Tests: `test_labeler.py`, `test_engine.py`. Shown in the coach card ("eval (white) +0.41 → +0.21") in `proof_ui_flows.mjs`. |
| 8 | Reconstruct board from a spoken variation; validate piece-location questions | **pass** | `scripts/proof_position_questions.mjs` runs a blindfold session where the board is never in the DOM. Moves are spoken, then the player asks by voice or text: "where is my knight", "where are your bishops", "what's on e4", "what is on see six", "anything hanging", "whose move", "what's the eval". Every answer is captioned and spoken, and matches an independent chess.js computation of the same FEN. The eval matches the persisted `engine_analysis` row (Stockfish 17.1, depth 10). The app then quizzes the player 10 times by button and by "quiz me", covering all 5 kinds. Answers are spoken and scored against the board: 9/10 correct, with the 1 deliberately wrong answer scored 0. Results are persisted in `position_question`. Unit and API tests: `backend/tests/test_position_questions.py` (50, including seeded replay of quiz sequences and owner checks) and `frontend/src/voice/resolver.test.ts` (question vs move routing). Endpoints: `POST /sessions/{id}/ask`, `POST /sessions/{id}/quiz`, `POST /sessions/{id}/quiz/{qid}/answer`, `GET /sessions/{id}/questions`. Not covered: threatened squares, pawn structure, candidate moves and tactical motifs (listed in handoff §3.3). The real microphone is checked manually on the deploy. |
| 9 | Track errors, response latency, hint usage and the next spaced-repetition drill | **pass** | `training_attempt` stores classification, eval swing, `latency_ms` and `hints_used`. SM-2 is in `mastery_state`, and `POST /players/{id}/mastery/next` picks the due drill. `scripts/proof_mastery.py` (rehearsal: 1.g4 mistake → due now → retrain 1.e4 → reps 0→1, interval 0→1 day) and `proof_ui_flows.mjs` (same loop in the UI); `test_sm2.py`, `test_mastery_api.py`. |
| 10 | Import 100+ opponent PGNs and run an opponent-conditioned simulated game | **pass** | `scripts/proof_opponent.py` (rehearsal: 120 games imported in 7.1 s; after 1.d4 the twin played Nf6 from the `opponent_profile` tier, confidence 1.0, sample 60); `proof_ui_flows.mjs` ("Play the twin"). Endpoints: `POST /opponents`, `POST /opponents/{id}/import`, `GET /opponents/{id}/tree`. The handoff's `POST /opponents/import` is `POST /opponents/{id}/import` here. Now also with **real games**: `scripts/proof_opponent_prep.mjs` imports 150 of Firouzja's games (150/150), builds the prep report (`GET /opponents/{id}/prep`; counts match an independent chess.js scan) and plays a blindfold voice game against the twin (1.e4 e5 2.Nf3 from his games, `opponent_profile` tier). |
| 11 | Real open/master game statistics with source and sample size | **thin** (code complete; live data unverified) | Lichess Masters explorer adapter (`chessmind/services/lichess_explorer.py`). `/openings/tree` returns a separate `masters` block, and `GET /openings/masters?fen=` covers any legal position. The block carries source, attribution, lichess.org link, status, fetch time, total games, per-move White/Draw/Black and average rating. Moves are validated by python-chess. Snapshots are cached by canonical FEN with a TTL and mirrored into `master_move_stat` for the sampler. If Lichess is unreachable, a stale cached copy is served, otherwise `unavailable`, and the UI falls back to local counts. Proof: `backend/tests/test_lichess_explorer.py` (11 tests: parsing, castling normalization, illegal-move drop, TTL refresh, cache hit with no second request, stale/unavailable/backoff, 429, endpoint shape). `proof_position_questions.mjs` checks the Openings page twice: once with the explorer port closed (a real connection failure: "unavailable … Showing local counts only"), and once with a local server replaying the fixture ("fetched just now", sample size, attribution, the illegal fixture move dropped, the reload served from cache with no second upstream call). **Why thin:** explorer.lichess.ovh is blocked by this sandbox's network policy, so the fixtures are **hand-written** in the documented response shape, not recorded, and no real Lichess numbers have been fetched yet. The first live fetch happens on Render (see the live check below). Local seed W/L/D stay assigned and are labelled as such. |
| 12 | MetaDb-compatible import; direct MetaDb only with authorized credentials | **thin** / direct access **out-of-scope** | PGN exports from MetaDb / ChessBase import through `POST /imports/pgn` (`test_pgn_import.py`, `day1_proof.py`). No vendor-specific adapter or fixture has been tested against a real MetaDb export. Direct MetaDb / ChessBase API access is out of scope: there are no credentials or licence, and nothing in the repo calls either vendor. |
| 13 | Deploy a working application with environment configuration, tests, logs and README | **thin** | `render.yaml` (API + static site, Singapore, no secrets), `scripts/render_build.sh` (Stockfish + deps + `alembic upgrade head` + idempotent seed), Neon URL handling (`backend/tests/test_config.py`). Rehearsed end to end as described above. 194 backend tests and 32 frontend tests pass (2026-10-30). Request-id middleware, with uvicorn access logs going to Render's log stream. Latency is measured (README). README covers setup, deploy and gaps. **Missing:** the live Render deploy (the maintainer connects the repo and secrets), a GitHub Actions CI workflow, and structured metrics (Stockfish job metrics, LLM token accounting). |

## Reviewer feedback round — 2026-10-30

Seven requested features. Each was proven against the real stack: Postgres 16
in Docker, Stockfish 17.1, python-chess, and the vendored opening taxonomy.
Browser proofs run in headless Chromium; only the microphone and system
voices are stubbed, as in the earlier proofs.

| # | Feature | Status | Proof |
| --- | --- | --- | --- |
| 1 | Announce the opening by voice | **pass** | `proof_voice_openings.mjs` phase A: blindfold, 1.e4 e5 2.Nf3 Nc6 3.Bb5 by voice. No announcement for the first four plies (generic names are skipped). 3.Bb5 returns `Ruy Lopez (C60, ply 5, by player)`, spoken exactly once and captioned `opening: Ruy Lopez`. The reply 3...a6 then announces "Ruy Lopez: Morphy Defense" once. Names come from `backend/chessmind/data/openings` (Lichess `chess-openings`, CC0) keyed by canonical FEN, so transpositions match (`test_opening_voice.py`). Fixed on the way: importers used to stamp a game's header ECO onto every position, which named the start position "Italian Giuoco Pianissimo"; migration `0006` re-labels existing rows. |
| 2 | Quiet mode | **pass** | Phase C: a session started with *Quiet mode* returned empty coaching and no announcements on 3 moves. Only "Opponent plays …" was spoken, there were no coach/opening captions and no coach card, and "quiz me" made no quiz request (the API returns 409). "quiet mode off" by voice → `PATCH /sessions/{id}/settings` → coaching returns; the checkbox turns it back on. An illegal move is still rejected in quiet mode (`test_quiet_mode_…`). |
| 3 | Illegal-move feedback | **pass** | Phase B, blindfold: "king e3" (not in python-chess's legal list) → spoken and captioned "That's an illegal move, try again." and a red *Illegal move* alert above the hidden board; no move was posted and the board stayed hidden. A direct `POST /move {san: "Qh5"}` → 422 `{code: "illegal_move"}`, and the FEN and history are unchanged. The next legal move clears the alert. API tests cover illegal SAN, UCI and garbage. |
| 4 | "Play <opening> against me" | **pass** | Phase D: as White, "play the Caro-Kann against me" → "I'll play the Caro-Kann Defense (B10: 1. e4 c6)…"; then 1.e4 → **1...c6** (`requested_opening` tier), with "Caro-Kann Defense" announced. Parser tests cover the London System, Caro-Kann, Spanish / Ruy Lopez, Italian, Queen's Gambit and Sicilian (plus mishearings), any other taxonomy family, and moves ("play e four against me"). The book is the defining line first, then any move on the family's named lines, weighted by master/graph counts with the session seed, then normal sampling once out of book (`test_black_requests_london_then_leaves_book`). Works for both colours. |
| 5 | Play Black, engine opens | **pass** | Phase E: *Play as Black* → the engine opened and its move was spoken. "play d4 against me" → "Restarted. I play d4." → history `d4`, Black to move; then 1...d5 by voice was accepted. The launcher's *Opponent's opening* "Queen's Gambit" as Black → 1.d4, and after 1...d5 the engine played **2.c4** and announced "Queen's Gambit". Seeded replays are identical (`test_requested_session_replays_deterministically`). |
| 6 | Opponent prep | **pass** | `proof_opponent_prep.mjs`: 150 real Firouzja games (`opponent_firouzja_150.pgn`) uploaded on the Opponents page, **150 of 150 imported** (~95 s). *Build prep report*: 78 games as White / 72 as Black. First moves, every frequent line and every branch count equal an independent chess.js scan (41 checks). Each suggested response is a legal Stockfish 17.1 move (MultiPV, depth 14, persisted) with his record against it. Then *Blindfold* + *Play the twin* as Black: the twin opened 1.e4 (55/78 of his White games); 1...e5 by voice was answered with 2.Nf3 from the `opponent_profile` tier. `test_opponent_prep.py` checks the same against 40 games plus the persisted engine row. |
| 7 | Deterministic facts + optional LLM wording | **pass** (no live provider) | Moves, evals and stats are unchanged (python-chess / Stockfish / tables). `services/llm_coach.py` is off unless `LLM_EXPLANATIONS_ENABLED`, `LLM_API_KEY` and `LLM_MODEL` are set (xai/openai/anthropic/gemini). `test_llm_coach.py` (12 tests, a local HTTP server with canned provider replies): with no key nothing is sent; a valid cited reply is used; replies naming a move outside the evidence (Qh5), an invented eval (−3.20), unknown or no citations, non-JSON, a timeout or HTTP 500 all fall back to the template; Anthropic wire format; and in a session the LLM text is used while quiet mode never calls it. The browser proof checks the coach card reads "wording: deterministic template" when no key is configured. **Not verified:** a real provider (no key in this sandbox); the Gemini adapter. |

Regression: `proof_frontend.mjs` (19 checks), `proof_ui_flows.mjs` (18) and
`proof_position_questions.mjs` (45) were re-run after these changes and all
pass. The last needs a production frontend build: under the Vite dev server,
StrictMode double effects make the masters panel read "cached". Its "not a
move" caption check was updated to the new wording ("I didn't catch a
move"), since move-shaped utterances now get the illegal-move response.

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
   and 8 with a real microphone). In the same session, ask "where is my
   knight" and say "quiz me", then answer out loud.
5. Open the Openings page. The "Master games" panel should read "fetched
   just now" with real Lichess counts, then "cached" on reload. This is the
   first live check of scenario 11. If it reads "unavailable", the panel
   shows the upstream error. A 401 means Lichess now requires a token: set
   `LICHESS_API_TOKEN`. Then run `python scripts/record_explorer_fixture.py`
   on the Render shell to replace the hand-written fixtures with recorded
   ones.

## Notes from the rehearsal

- `proof_ui_flows.mjs` originally failed "1.e4 = 108 games" because the
  assertion assumed an empty database. Deploys always seed the global
  graph, so the check now measures the import as a delta
  (216 → 324, +108) and passes. `proof_frontend.mjs` (19 checks),
  `proof_ui_flows.mjs` (18) and `proof_position_questions.mjs` (45) all
  pass against a freshly built deploy-shaped database; logs are in
  `scripts/data/deploy_rehearsal/`.
- `pq_masters_fixture_replay.png` shows the **hand-written fixture**
  rendered through the real fetch → parse → cache → UI path. Its numbers
  are test values, not Lichess data.
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
