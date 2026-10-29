# ChessMind OS — Agent Operating Instructions

> Voice-first, engine-grounded chess opening intelligence. This file is loaded
> on every Claude Code session in this repo. Read it in full before writing or
> changing code.

## 1. What this product is

ChessMind OS maps a player's repertoire against a universal opening graph,
trains blindfold visualization and tournament-speed recall, explains errors
with grounded engine evidence, and simulates opponents from their actual
games.

The differentiator is **voice + mental board visualization + personalized
recall**. It is explicitly not a generic chessboard, not an LLM chat wrapper,
and not a repertoire PDF.

The authoritative product brief is
`docs/handoff/ChessMind_OS_Developer_Handoff.docx`. The architecture plan
derived from it is `docs/ARCHITECTURE.md`. If this file and the architecture
plan disagree, the architecture plan wins and this file should be updated.

## 2. MVP scope (five-day vertical slice)

The MVP is a working end-to-end chess trainer that proves every major user
flow. It is not a product-complete system.

| Day | Deliverable |
| --- | --- |
| 1 | Auth / profile; PGN import; opening graph with transposition normalization; Stockfish worker; seeded opening dataset. |
| 2 | Streaming voice moves; legal-move resolver; guided and blindfold sessions; Stockfish-grounded feedback. |
| 3 | Chess DNA v1; mastery events; retest-on-error; visualization questions; spaced repetition. |
| 4 | Opponent PGN ingestion; opening-tree stats; digital-twin opponent sampler. |
| 5 | Polish, end-to-end tests, CI, Render deployment, observability, handoff artifacts. |

In-scope for MVP: Postgres, python-chess, Stockfish via UCI, FastAPI, React,
browser Web Speech API, Render deployment.

**Out of scope for MVP** (do not build these without explicit approval):

- Leela / Maia secondary engines.
- Native mobile apps.
- Payment, billing, teams, org management.
- Direct MetaDb / ChessBase vendor API integration (import adapter only;
  user-uploaded PGNs are the Day-5 proof).
- Multi-tenant admin consoles beyond the single Admin role in the brief.
- Vector / RAG retrieval of coaching notes (`pgvector` is reserved in the
  schema but not wired in Day 1-5).

## 3. Hard rule — deterministic chess correctness

**Chess facts come from code, not the LLM.** This is non-negotiable.

1. **Legal moves, FEN, SAN, castling, en passant, promotion, check, PGN
   parsing** — only `python-chess`. The LLM is never asked whether a move is
   legal, and its output never bypasses the parser.
2. **Position evaluation and principal variations** — only Stockfish (UCI,
   with recorded engine version, depth, nodes, and perspective). Any
   `cp` / `mate` / PV shown to the user originates from a Stockfish response
   that is persisted to `engine_analysis`.
3. **Opening statistics, game counts, opponent frequencies** — only the
   indexed Postgres tables built from parsed PGNs. The LLM never invents
   counts, win rates, or "theory says".
4. **LLM job** — explain *grounded evidence* in natural language, in one
   JSON schema that cites which facts it used (FEN, PV id, stat row). If the
   evidence is missing, the coaching layer falls back to a deterministic
   template; it does not improvise moves or evaluations.
5. **Verification path** — every move the LLM utters as a *recommendation*
   is validated through `python-chess.Board.push_san` on the current legal
   board before it reaches TTS or the UI. A rejection is a bug in the LLM
   call, not in the parser.

If a feature cannot be implemented inside this rule, surface the gap in
`PROGRESS.md` and ask. Do not relax the rule to make a feature fit.

## 4. Stack

| Layer | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Uvicorn |
| ORM / migrations | SQLAlchemy 2.x + Alembic |
| Database | PostgreSQL 16 (Render managed in deploy; Docker Postgres locally — **never SQLite**) |
| Cache / jobs | Redis 7 (session state, job queue, rate limits) |
| Chess rules | `python-chess` |
| Engine | Stockfish (latest validated stable); single long-lived UCI process per worker, guarded by an asyncio lock; cache is the primary path |
| LLM | Grok via a provider adapter; swappable to GPT / Claude / Gemini |
| Speech | Browser Web Speech API (STT + TTS) with provider adapter seam for later server-side streaming |
| Frontend | React 18 + Vite + TypeScript; `chess.js` + `react-chessboard` for the board UI |
| Auth | Email + password with argon2; JWT access + refresh |
| Deploy | Render (web service + worker + managed Postgres + managed Redis) |
| CI | GitHub Actions |

## 5. Repository layout (target)

```
backend/        FastAPI app, services, workers, Alembic migrations
frontend/       Vite + React app
docs/           ARCHITECTURE.md, handoff/, API notes, ADRs
scripts/        PGN seeders, Stockfish benchmarks, utility CLIs
tests/          backend tests; frontend tests live under frontend/
.github/        workflows
docker-compose.yml   Postgres + Redis + backend + frontend for local dev
```

Phase 0 (this scaffold commit) contains docs only. Code lands starting Day 1.

## 6. Rules of engagement for Claude Code in this repo

- **Follow the plan.** `docs/ARCHITECTURE.md` is the source of truth for
  module layout, data model, and API shapes. Deviations need a note in the
  commit message and an updated section in the architecture doc.
- **Progress tracking.** Mark phase checkboxes in `PROGRESS.md` as items
  land. Do not mark a phase complete until its acceptance proof (see §9 of
  the handoff) is demonstrated.
- **No feature code without approval of the plan.** Scaffolding and docs
  may be committed; feature code waits for the maintainer's go-ahead on the
  architecture doc.
- **Postgres only.** Any PR that introduces SQLite as a runtime or test
  dependency should be rejected. Tests use a dedicated Postgres schema or a
  disposable Docker container.
- **No mocked critical paths.** Stockfish, PGN parsing, voice resolution,
  and persistence are real in every demo. Fixtures and seed data are fine;
  pretending the engine ran is not.
- **Secrets** live in `.env` locally (gitignored) and in Render env groups
  in deploy. Never commit a key.
- **White-centric evaluations** are the default surface; player-relative
  interpretation is derived at the view layer.
- **Deterministic seeds** on every randomized sampler so sessions replay.

## 7. Open questions tracked

Any decision that is still open rides in `docs/ARCHITECTURE.md` §11 as
"Open" with a dated note. Once resolved, flip it to "Resolved" and inline the
resolution — do not delete the history.
