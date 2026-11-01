# ChessMind OS — working agreement

Voice-first chess opening trainer. Backend: FastAPI + python-chess + real Stockfish + Postgres. Frontend: React + Vite.

Rules for any change:
- Mock nothing. Chess correctness is deterministic: legality/SAN/FEN/PGN via python-chess, evals/PVs from a real Stockfish process, opening names from the vendored Lichess taxonomy, opponent/book stats from real games. Moves and evals must NEVER come from an LLM; the LLM layer only explains, and is off unless its flag + API key are set.
- Set up a real environment (Postgres + Stockfish) and run the tests before claiming a change works.
- Write tests for new logic; prove user-facing flows with the existing headless-browser proof scripts.
- Commit logically, update ACCEPTANCE.md / README.md / PROGRESS.md / docs/ARCHITECTURE.md, then open a PR. Stop after the PR.
- Read existing code and extend it; don't duplicate. Keep migrations additive.
