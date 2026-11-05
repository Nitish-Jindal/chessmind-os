# ChessMind OS — working agreement

Voice-first chess opening trainer. Backend: FastAPI + python-chess + real Stockfish + Postgres. Frontend: React + Vite.

Rules for any change:
- Mock nothing. Chess correctness is deterministic: legality/SAN/FEN/PGN via python-chess, evals/PVs from a real Stockfish process, opening names from the vendored Lichess taxonomy, opponent/book stats from real games. Moves and evals must NEVER come from an LLM; the LLM layer only explains, and is off unless its flag + API key are set.
- Set up a real environment (Postgres + Stockfish) and run the tests before claiming a change works.
- Write tests for new logic; prove user-facing flows with the existing headless-browser proof scripts.
- Commit logically. Update only the relevant acceptance criteria, README, architecture, or progress documentation when the change requires it. Open a PR and stop.
- Read existing code and extend it; don't duplicate. Keep migrations additive.

Engine conventions:
- Keep interactive session grading, opponent fallback and position questions on the interactive UCI lane. Bulk/explicit analysis, prep reports and scheduled evaluations use the background lane. Never reintroduce SimpleEngine/to_thread searches or share an engine lock between lanes.
- Every UCI interaction and admission wait must have a wall deadline. Poisoned processes must be killed/reaped before replacement; cancellation must release admission. Use real process-stop/kill tests for isolation and recovery.
- Keep one ASGI worker on shared-CPU hosting (at most two Stockfish subprocesses, each Threads=1/Hash=16 by default). Keep in-process jobs and uploads bounded.
- Evaluation GETs remain cache-only. Frontend caches must include session + FEN, deduplicate in-flight reads, ignore stale results and stop polling once analysis is available. Frontend evaluation fixtures must come from recorded real engine responses.
