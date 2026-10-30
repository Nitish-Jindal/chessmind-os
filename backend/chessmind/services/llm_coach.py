"""Optional LLM wording of the coaching explanation (CLAUDE.md §3.4–3.5).

Off by default. When ``LLM_EXPLANATIONS_ENABLED=true`` and ``LLM_API_KEY`` and ``LLM_MODEL``
are set, the deterministic coaching facts for a move (classification and swing from the
labeler, Stockfish's best move / PV / evals, python-chess board features, the template text)
are sent to the configured provider, which may only reword them. Its reply must be JSON
``{"text": ..., "cited_facts": ["F1", ...]}`` and is checked before use:

- every cited fact id exists, and at least one is cited;
- every piece move, capture or castle written in the text is one of the evidence moves
  (the played move, Stockfish's MultiPV first moves, the PV), and those are themselves
  legal on the board (python-chess ``push_san`` replay);
- every signed decimal (an eval such as ``+0.45``) is one of the evaluations in the facts;
- the text is non-empty and short enough to speak.

Any failure (disabled, no key, HTTP error, timeout, bad JSON, a check fails) returns the
deterministic template unchanged. The correction move shown in the UI always comes from the
template; the LLM text is only the spoken summary.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import chess
import httpx

from chessmind.config import Settings, get_settings

logger = logging.getLogger("chessmind.llm_coach")

MAX_TEXT_CHARS = 600

_DEFAULT_BASE = {
    "xai": "https://api.x.ai/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}

SYSTEM_PROMPT = (
    "You are a chess coach speaking to a player during a voice training session. "
    "Rewrite the coaching for their last move in two or three short spoken sentences. "
    "Use ONLY the numbered facts you are given. Do not name any move, square, evaluation or "
    "statistic that is not in the facts, and do not judge positions yourself. "
    'Reply with JSON only: {"text": "<what to say>", "cited_facts": ["F1", ...]} listing the '
    "fact ids your text relies on."
)

_MOVE_RE = re.compile(r"(?<![\w-])(O-O-O|O-O|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8](?:=[QRBN])?)[+#]?(?!\w)")
_EVAL_RE = re.compile(r"(?<![\w.])[+-]\d+\.\d+")


@dataclass(frozen=True)
class Evidence:
    fen_before: str
    player_color: str
    played_san: str
    classification: str
    eval_swing_cp: int
    eval_before_cp_white: int | None
    eval_after_cp_white: int | None
    best_san_list: list[str]
    best_pv_san: list[str]
    board_features: dict[str, Any]
    template_text: str

    def facts(self) -> dict[str, str]:
        f: dict[str, str] = {
            "F1": f"The player ({self.player_color}) played {self.played_san}.",
            "F2": f"Stockfish classification: {self.classification}; eval swing {_fmt(self.eval_swing_cp)} pawns "
                  f"against the player.",
        }
        if self.eval_before_cp_white is not None and self.eval_after_cp_white is not None:
            f["F3"] = (f"Evaluation from White's side: {_fmt(self.eval_before_cp_white)} before the move, "
                       f"{_fmt(self.eval_after_cp_white)} after.")
        if self.best_san_list:
            f["F4"] = f"Stockfish's best move was {self.best_san_list[0]}."
        if len(self.best_san_list) > 1:
            f["F5"] = f"Other strong engine moves: {', '.join(self.best_san_list[1:])}."
        if self.best_pv_san:
            f["F6"] = f"Stockfish's main line: {' '.join(self.best_pv_san[:6])}."
        bf, i = self.board_features, 7
        for cond, text in [
            (bf.get("new_hanging_squares"), f"After the move the player's piece on {', '.join(bf.get('new_hanging_squares') or [])} is attacked and under-defended."),
            (bf.get("captured_opp_hanging_squares"), f"The move won material on {', '.join(bf.get('captured_opp_hanging_squares') or [])}."),
            (bf.get("lost_castling_kingside"), "The move gave up kingside castling rights."),
            (bf.get("lost_castling_queenside"), "The move gave up queenside castling rights."),
            (bf.get("delivered_check"), "The move gives check."),
        ]:
            if cond:
                f[f"F{i}"] = text
                i += 1
        f[f"F{i}"] = f"Template summary: {self.template_text}"
        return f

    def allowed_moves(self) -> set[str]:
        return {_strip(m) for m in [self.played_san, *self.best_san_list, *self.best_pv_san]}

    def allowed_evals(self) -> set[float]:
        vals = [self.eval_swing_cp, self.eval_before_cp_white, self.eval_after_cp_white]
        return {round(v / 100.0, 2) for v in vals if v is not None}


@dataclass
class Explanation:
    text: str
    source: str  # "template" | "llm:<provider>:<model>"
    cited_facts: list[str] = field(default_factory=list)
    rejected_reason: str | None = None


def _fmt(cp: int) -> str:
    return f"{cp / 100.0:+.2f}"


def _strip(san: str) -> str:
    return san.rstrip("+#")


def is_enabled(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(s.llm_explanations_enabled and s.llm_api_key and s.llm_model)


def validate(evidence: Evidence, text: str, cited: list[str]) -> str | None:
    """Return why ``text`` must be rejected, or None when it only uses the evidence."""
    if not text or not text.strip():
        return "empty text"
    if len(text) > MAX_TEXT_CHARS:
        return "text too long"
    facts = evidence.facts()
    if not cited or any(c not in facts for c in cited):
        return f"cited facts {cited!r} not in {sorted(facts)}"
    # The evidence moves themselves must be legal (python-chess), or nothing is trusted.
    board = chess.Board(evidence.fen_before)
    try:
        board.parse_san(evidence.played_san)
        for san in evidence.best_san_list:
            board.parse_san(san)
        pv = board.copy(stack=False)
        for san in evidence.best_pv_san:
            pv.push_san(san)
    except ValueError as exc:
        return f"evidence move not legal: {exc}"
    allowed = evidence.allowed_moves()
    for m in _MOVE_RE.findall(text):
        if _strip(m) not in allowed:
            return f"move {m} is not in the engine evidence"
    evals = evidence.allowed_evals()
    for e in _EVAL_RE.findall(text):
        if round(float(e), 2) not in evals:
            return f"evaluation {e} is not in the facts"
    return None


async def explain(
    evidence: Evidence,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> Explanation:
    """LLM wording of ``evidence`` when enabled and valid; otherwise the template."""
    s = settings or get_settings()
    template = Explanation(text=evidence.template_text, source="template")
    if not is_enabled(s):
        return template
    try:
        raw = await _call_provider(s, evidence, client)
        parsed = json.loads(_json_block(raw))
        text = str(parsed.get("text", "")).strip()
        cited = [str(c) for c in parsed.get("cited_facts", [])]
    except Exception as exc:  # noqa: BLE001 - any provider failure falls back
        logger.warning("llm explanation failed (%s); using template", exc)
        template.rejected_reason = f"provider error: {type(exc).__name__}"
        return template
    reason = validate(evidence, text, cited)
    if reason is not None:
        logger.warning("llm explanation rejected: %s", reason)
        template.rejected_reason = reason
        return template
    return Explanation(text=text, source=f"llm:{s.llm_provider}:{s.llm_model}", cited_facts=cited)


def _json_block(raw: str) -> str:
    """Models sometimes wrap JSON in a code fence; take the outermost object."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return raw[start:end + 1]


async def _call_provider(s: Settings, evidence: Evidence, client: httpx.AsyncClient | None) -> str:
    provider = s.llm_provider.lower()
    if provider not in _DEFAULT_BASE:
        raise ValueError(f"unknown LLM_PROVIDER {s.llm_provider!r}")
    base = (s.llm_base_url or _DEFAULT_BASE[provider]).rstrip("/")
    user = "Facts:\n" + "\n".join(f"{k}: {v}" for k, v in evidence.facts().items())
    own = client is None
    client = client or httpx.AsyncClient(timeout=s.llm_timeout_s)
    try:
        if provider in ("xai", "openai"):
            r = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {s.llm_api_key}"},
                json={"model": s.llm_model, "temperature": 0.2,
                      "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                   {"role": "user", "content": user}]},
                timeout=s.llm_timeout_s,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        if provider == "anthropic":
            r = await client.post(
                f"{base}/messages",
                headers={"x-api-key": s.llm_api_key, "anthropic-version": "2023-06-01"},
                json={"model": s.llm_model, "max_tokens": 400, "system": SYSTEM_PROMPT,
                      "messages": [{"role": "user", "content": user}]},
                timeout=s.llm_timeout_s,
            )
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
        r = await client.post(  # gemini
            f"{base}/models/{s.llm_model}:generateContent",
            headers={"x-goog-api-key": s.llm_api_key},
            json={"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                  "contents": [{"role": "user", "parts": [{"text": user}]}],
                  "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}},
            timeout=s.llm_timeout_s,
        )
        r.raise_for_status()
        return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
    finally:
        if own:
            await client.aclose()
