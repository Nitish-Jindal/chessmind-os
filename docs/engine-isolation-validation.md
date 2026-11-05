# Stockfish isolation and recovery validation

## Diagnosis and call paths

The previous process-wide `SimpleEngine` used `threading.Lock`, shared by every
caller, with `asyncio.to_thread` searches. `Limit(depth=...)` is a search target,
not a wall-clock deadline. The synchronous wrapper's default timeout does not
bound depth-only searches or application lock waiting. Cancelling an HTTP task
cannot stop the thread that owns the engine/lock. Its stale-process check only
proved that a PID existed, not that UCI was responding. The strongest supported
diagnosis is unbounded depth-only UCI work plus synchronization starvation; the
production hang itself was not reproduced in this environment.

`POST /sessions/{id}/move` resolves SAN/UCI using python-chess, grades before/after
positions with Stockfish, runs deterministic coaching (optionally reworded by the
configured LLM), updates mastery, selects an opponent response from recorded-game
statistics or the vendored opening taxonomy, then uses Stockfish as the last
sampler tier. Taxonomy/statistics selection is unchanged. Opening statistics use recorded game tables; the frontend separately loads
`/openings/masters` through its statistics panel. That endpoint uses a persisted
explorer snapshot or a bounded upstream request and never invokes Stockfish.
Explorer and optional LLM calls already have HTTP deadlines. A move computation now also
has an overall deadline to bound database row contention and combined I/O.

Call-site assignment:

| Path | Lane |
| --- | --- |
| Session move grading, opponent engine fallback, position questions | Interactive |
| Explicit `/analysis/position`, opponent prep, scheduled final-position evaluation | Background |
| PGN/opponent imports | No engine calls; bounded serial import admission, offloaded per-game parsing, async SQL |

There are no other Stockfish call sites or scheduled engine workers. The existing
cache stores actual white-perspective scores/PVs in PostgreSQL. Evaluation GETs
never invoke Stockfish. Frontend polling was already pending-only at 500ms; it is
now sequential at 2 seconds with ten retries, in-flight deduplication and a bounded
session/FEN cache. Move-response scores concern the player's move rather than the
final opponent-response FEN, so reusing them for that bar would be incorrect.

## Lifecycle and resource design

Each lane owns one native asyncio python-chess `UciProtocol` and one asyncio lock.
No synchronous engine calls or engine worker threads remain. Only the lock owner
starts/retires a process. Admission, initialization/configure/ping, and analysis
have separate enforced deadlines. Queue rejection and admission timeouts do not
kill someone else's active process. Search cancellation is shielded while its exact
transport is killed/reaped; this avoids python-chess's analysis-future cancellation
race during `connection_lost`. Cleanup waits at most 0.5 seconds and withholds any
replacement until the retired child has exited. Future requests recover lazily.
All paths release admission, including cancellation during cleanup. Multi-line
prep HTTP computation has a combined 25-second default budget so individual line
deadlines cannot accumulate into a Cloudflare timeout.

Defaults are one interactive and one background process, one Stockfish thread and
16 MB hash each. Background niceness 10 yields CPU priority on Linux. Production
entrypoints explicitly select one ASGI worker, including inherited
`WEB_CONCURRENCY` environments. No paid infrastructure, migration, synthetic engine
score or alternative move generator was added. Explicit depth/MultiPV requests are
honored under the background wall deadline. Lower automatic defaults trade search
quality/variation diversity for bounded single-CPU resource use; see README.

New-position inserts are deferred until after searches so a background search
cannot hold the position unique key while an interactive search needs the same
new FEN. Imports retain existing progress/status rows. Full/import timeout and
interruption states are observable and retryable; resubmit rejected uploads.
Scheduled position work is deduplicated/capped, and logs failures without failing
moves. These remain in-process jobs, not a durable external worker system.

## Reproduction

Use an isolated PostgreSQL 16 test database; pytest resets its schema. Never point
these commands at production. Install the backend dev extra and real Stockfish
17.1 using `scripts/install_stockfish.sh`; export `STOCKFISH_PATH` to the installed
absolute path. Use the normal database settings with the isolated database URL.

```sh
cd backend
pytest -q -s
pytest -q -s tests/test_engine_lanes.py
cd ../frontend
npm ci
npm run lint
npm test
npm run typecheck
npm run build
```

For browser/live HTTP proofs, migrate a **separate** proof database with Alembic.
Run one API on port 8000 and the Vite frontend on 5173. Disable external explorer
requests for deterministic local measurements (`LICHESS_EXPLORER_ENABLED=false`),
and leave optional LLM configuration disabled. Install Playwright in a local tool
environment and provide its `node_modules` through `NODE_PATH` if needed.

```sh
node scripts/proof_engine_evaluation.mjs http://127.0.0.1:5173
python scripts/proof_engine_http.py
```

The HTTP proof starts a real single-worker uvicorn process on 8001 with background
depth 40, submits real background analysis, and verifies a legal move before that
analysis completes. It then SIGKILLs the actual interactive child and verifies a
legal move with a new child PID and unchanged API PID. Its database, Stockfish and
all HTTP requests are real. Linux process signals are required for kill/stop tests.

The browser proof registers a user, starts an Italian session, shows a genuine
saved evaluation, submits `e4` through the text move resolver and verifies that the
bar updates for the resulting final FEN. It observes a 4.5-second quiet window
before/after the move (more than two poll intervals). API/chess responses are never
stubbed. Frontend watcher test evaluation fixtures were copied from these actual
responses; pending states remove scores rather than inventing them.

## Measured evidence

Environment: managed Linux development host, PostgreSQL 16, official Stockfish
17.1 AVX2, Python 3.12, headless Chromium. This is **not** blitz.cloud's single
shared-CPU host; these measurements prove isolation and recovery locally, not a
production latency guarantee.

- Full backend suite: **219 passed in 61.60 s (final rerun after search-duration logging; prior measured run 63.52 s)**, real PostgreSQL 16 and Stockfish 17.1.
  Focused lane/recovery suite: **6 passed in 4.69 s**; timeout, kill,
  admission/cancellation, import admission, HTTP isolation and shared-new-FEN SQL contention.
- Frontend: **81 passed in 2.26 s**; TypeScript checks and ESLint passed; Vite
  production build passed (58 modules). ESLint tooling is new because the frontend
  previously had no lint command; TypeScript retains unused/name checks.
- ASGI HTTP move while a real background search was SIGSTOPped: **0.217 s**.
  The test asserts different locks/PIDs and a still-pending background task; CPU
  speed cannot make that paused background task finish first. The assertion budget
  is the 3-second interactive search setting plus a 1-second startup/HTTP allowance.
- Live HTTP move during **unpaused** depth-40 background search: **0.247 s**.
- Live next move after interactive SIGKILL: **0.210 s**, API PID **10932** unchanged;
  interactive PID **11041 → 11046**, background PID **11039**. Background depth-40
  search hit its 10-second deadline and returned **503**.
- Browser cold-engine move response: **252 ms**. Three evaluation GETs total: two
  initial reads until ready, one after the move. A final cached-position rerun took
  **65 ms**, with two GETs total (one per FEN). Both runs observed zero repeat reads
  in either settled quiet window.
  Both scores are actual depth-12 Stockfish results with white perspective.
- Engine timeout test deliberately uses a 1ms search deadline at depth 128; it
  asserts bounded failure, actual process exit/reaping and a genuine successful
  subsequent search with a different PID. Queue/cancellation tests use observable
  UCI `go` state, real processes and explicit events rather than timing sleeps.

Evidence: [HTTP transcript](../scripts/data/engine-isolation/http.json),
[final browser transcript](../scripts/data/engine-isolation/browser.json),
[cold browser transcript](../scripts/data/engine-isolation/browser-cold.json),
[before move](../scripts/data/engine-isolation/before-move.png),
[after move](../scripts/data/engine-isolation/after-move.png).

Production steps: use `STOCKFISH_PATH=/usr/games/stockfish`, one ASGI worker,
and the documented conservative lane defaults; remove the old global default
depth/MultiPV settings in favor of lane settings. Monitor engine lane admission,
timeout, restart and queue-full logs after normal deployment. Live validation on
blitz.cloud under actual CPU throttling/import volume remains necessary. No
production deployment was performed, and no Cloudflare timeout was increased.
