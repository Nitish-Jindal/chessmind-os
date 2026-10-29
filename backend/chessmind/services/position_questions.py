"""Position questions during a session — handoff §3.3 "voice questions".

Two directions, one ground truth:

* **ask** — the player asks about the current board ("where is my knight",
  "what's on e5", "anything hanging?", "whose move", "what's the eval") and
  gets an answer computed from the session FEN with python-chess; the
  evaluation comes from the cache-first Stockfish path (persisted
  ``engine_analysis`` row).
* **quiz** — the system asks a visualization question about the current
  board (deterministically seeded from the session seed), the player
  answers by voice or text, and the answer is scored against the board /
  engine facts captured when the question was asked.

Parsing is deterministic word matching over the speech-recognizer forms the
move resolver also accepts ("knight f three", "e four", "see five"). Nothing
here is produced by an LLM.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import chess
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.models import PositionQuestion, TrainingSession
from chessmind.services.coaching import hanging_squares
from chessmind.services.engine import analyze_board

QuestionKind = Literal["piece_location", "square_content", "hanging_pieces", "side_to_move", "evaluation"]
Side = Literal["player", "opponent", "white", "black"]
QUIZ_KINDS: tuple[QuestionKind, ...] = (
    "piece_location", "square_content", "hanging_pieces", "side_to_move", "evaluation",
)

# Session grading depth; evaluation answers reuse its cached rows.
EVAL_DEPTH = 10
EVAL_MULTIPV = 3
# |cp| at or below this is "roughly equal" for evaluation quizzes.
EQUAL_BAND_CP = 50

PIECE_WORDS: dict[str, int] = {
    "pawn": chess.PAWN, "pawns": chess.PAWN, "pond": chess.PAWN,
    "knight": chess.KNIGHT, "knights": chess.KNIGHT, "night": chess.KNIGHT, "nights": chess.KNIGHT,
    "horse": chess.KNIGHT, "horses": chess.KNIGHT,
    "bishop": chess.BISHOP, "bishops": chess.BISHOP,
    "rook": chess.ROOK, "rooks": chess.ROOK, "rock": chess.ROOK, "rocks": chess.ROOK,
    "queen": chess.QUEEN, "queens": chess.QUEEN,
    "king": chess.KING, "kings": chess.KING,
}
FILE_WORDS: dict[str, str] = {
    "a": "a", "ay": "a", "alpha": "a", "alfa": "a",
    "b": "b", "be": "b", "bee": "b", "bravo": "b",
    "c": "c", "see": "c", "sea": "c", "charlie": "c",
    "d": "d", "dee": "d", "delta": "d",
    "e": "e", "echo": "e",
    "f": "f", "ef": "f", "eff": "f", "foxtrot": "f",
    "g": "g", "gee": "g", "golf": "g",
    "h": "h", "aitch": "h", "age": "h", "hotel": "h",
}
RANK_WORDS: dict[str, str] = {
    "1": "1", "one": "1", "won": "1",
    "2": "2", "two": "2", "to": "2", "too": "2",
    "3": "3", "three": "3",
    "4": "4", "four": "4", "for": "4", "fore": "4",
    "5": "5", "five": "5",
    "6": "6", "six": "6",
    "7": "7", "seven": "7",
    "8": "8", "eight": "8", "ate": "8",
}
# Possessives pick the side in a question ("where is MY knight", "YOUR queen").
PLAYER_POSSESSIVE = {"my", "mine", "our"}
OPPONENT_POSSESSIVE = {"your", "yours", "opponent", "opponents", "opponent's", "their", "theirs",
                       "his", "her", "enemy", "computer", "computer's"}
# In an *answer*, pronouns also name a side ("me", "I'm better", "you").
PLAYER_PRONOUNS = {"me", "i", "i'm", "im", "myself", "us"}
OPPONENT_PRONOUNS = {"you", "you're", "youre", "them", "they", "they're"}
NONE_WORDS = {"none", "no", "nothing", "nobody", "zero", "nope", "neither"}
EMPTY_WORDS = {"empty", "nothing", "none", "vacant", "free", "clear", "no"}
EQUAL_WORDS = {"equal", "even", "balanced", "level", "drawn", "draw", "equality", "same"}

PIECE_NAMES = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
               chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}


@dataclass
class Parsed:
    """Everything recognizable in one utterance."""

    tokens: list[str]
    squares: list[str] = field(default_factory=list)
    piece: int | None = None
    sides: list[Side] = field(default_factory=list)  # colours + possessives, in order
    pronoun_sides: list[Side] = field(default_factory=list)  # me / you, in order

    def has(self, *words: str) -> bool:
        return any(w in self.tokens for w in words)


def _tokens(text: str) -> list[str]:
    text = text.lower().replace("’", "'")
    text = re.sub(r"\bwhat'?s\b", "what is", text)
    text = re.sub(r"\bwho'?s\b", "who is", text)
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    # "e4" stays one token; "e 4" / "e four" are joined below.
    return [t for t in text.split() if t]


def parse(text: str) -> Parsed:
    toks = _tokens(text)
    p = Parsed(tokens=toks)
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"[a-h][1-8]", t):
            p.squares.append(t)
        elif t in FILE_WORDS and i + 1 < len(toks) and toks[i + 1] in RANK_WORDS:
            p.squares.append(FILE_WORDS[t] + RANK_WORDS[toks[i + 1]])
            i += 1
        elif t in PIECE_WORDS and p.piece is None:
            p.piece = PIECE_WORDS[t]
        elif t in ("white", "whites", "white's"):
            p.sides.append("white")
        elif t in ("black", "blacks", "black's"):
            p.sides.append("black")
        elif t in PLAYER_POSSESSIVE:
            p.sides.append("player")
        elif t in OPPONENT_POSSESSIVE:
            p.sides.append("opponent")
        elif t in PLAYER_PRONOUNS:
            p.pronoun_sides.append("player")
        elif t in OPPONENT_PRONOUNS:
            p.pronoun_sides.append("opponent")
        i += 1
    return p


@dataclass
class Question:
    kind: QuestionKind
    piece: int | None = None
    side: Side = "player"
    square: str | None = None


def parse_question(text: str) -> Question | None:
    """Map an utterance to a board question, or None if it is not one we answer."""
    p = parse(text)
    joined = " ".join(p.tokens)
    side: Side = p.sides[0] if p.sides else "player"
    if re.search(r"\bwhose (move|turn)\b|\bwho (is )?(to )?(move|moves|play|plays)\b|\bside to move\b|\bmy (move|turn)\b", joined):
        return Question("side_to_move")
    if p.has("eval", "evaluation", "evaluate", "score", "advantage", "winning", "better", "ahead", "assessment"):
        return Question("evaluation")
    if p.has("hanging", "hang", "hangs", "undefended", "unprotected", "loose", "prise", "attacked"):
        return Question("hanging_pieces", side=side)
    # A bare square ("e4") is a move, not a question: require a question word.
    if p.squares and p.has("what", "which", "anything", "occupies", "occupied", "there", "on", "sits", "piece"):
        return Question("square_content", square=p.squares[0])
    if p.piece is not None and p.has("where", "which", "location", "locate", "find", "squares", "square", "is", "are"):
        return Question("piece_location", piece=p.piece, side=side)
    return None


def _color(side: Side, player_color: str) -> chess.Color:
    if side in ("white", "black"):
        return side == "white"
    me = player_color == "white"
    return me if side == "player" else not me


def _whose(color: chess.Color, player_color: str) -> str:
    return "your" if (color == chess.WHITE) == (player_color == "white") else "the opponent's"


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def piece_squares(board: chess.Board, piece: int, color: chess.Color) -> list[str]:
    return sorted(chess.square_name(s) for s in board.pieces(piece, color))


def hanging_report(board: chess.Board, color: chess.Color) -> list[dict[str, Any]]:
    out = []
    for sq in sorted(hanging_squares(board, color)):
        out.append({
            "square": chess.square_name(sq),
            "piece": PIECE_NAMES[board.piece_type_at(sq)],
            "attackers": len(board.attackers(not color, sq)),
            "defenders": len(board.attackers(color, sq)),
        })
    return sorted(out, key=lambda h: h["square"])


def eval_bucket(cp: int | None, mate: int | None) -> Literal["white", "black", "equal"]:
    if mate is not None:
        return "white" if mate > 0 else "black"
    if cp is None or abs(cp) <= EQUAL_BAND_CP:
        return "equal"
    return "white" if cp > 0 else "black"


def describe_eval(cp: int | None, mate: int | None, player_color: str) -> str:
    if mate is not None:
        side = "White" if mate > 0 else "Black"
        return f"{side} has mate in {abs(mate)}"
    cp = cp or 0
    mag = abs(cp)
    if mag <= EQUAL_BAND_CP:
        phrase = "roughly equal"
    else:
        side = "White" if cp > 0 else "Black"
        strength = "slightly better" if mag <= 150 else "better" if mag <= 300 else "winning"
        phrase = f"{side} is {strength}"
    rel = cp if player_color == "white" else -cp
    return f"{cp / 100:+.2f} for White, {phrase} ({rel / 100:+.2f} from your side)"


@dataclass
class EvalEvidence:
    cp: int | None
    mate: int | None
    engine_version: str
    depth: int
    cached: bool
    position_id: str


@dataclass
class Answer:
    kind: QuestionKind
    text: str
    facts: dict[str, Any]


def answer_question(q: Question, board: chess.Board, player_color: str,
                    evidence: EvalEvidence | None = None) -> Answer:
    if q.kind == "side_to_move":
        mover = "White" if board.turn == chess.WHITE else "Black"
        yours = (board.turn == chess.WHITE) == (player_color == "white")
        return Answer(q.kind, f"{mover} to move — {'you' if yours else 'your opponent'}.",
                      {"turn": mover.lower(), "player_to_move": yours})
    if q.kind == "square_content":
        sq = chess.parse_square(q.square)
        piece = board.piece_at(sq)
        if piece is None:
            return Answer(q.kind, f"{q.square} is empty.", {"square": q.square, "piece": None})
        color = "white" if piece.color == chess.WHITE else "black"
        return Answer(q.kind, f"{q.square}: {color} {PIECE_NAMES[piece.piece_type]} ({_whose(piece.color, player_color)}).",
                      {"square": q.square, "piece": PIECE_NAMES[piece.piece_type], "color": color})
    if q.kind == "piece_location":
        color = _color(q.side, player_color)
        name = PIECE_NAMES[q.piece]
        squares = piece_squares(board, q.piece, color)
        owner = _whose(color, player_color).capitalize()
        if not squares:
            text = f"{owner} side has no {name} on the board."
        elif len(squares) == 1:
            text = f"{owner} {name} is on {squares[0]}."
        else:
            text = f"{owner} {name}s are on {_list(squares)}."
        return Answer(q.kind, text, {"piece": name, "color": "white" if color else "black", "squares": squares})
    if q.kind == "hanging_pieces":
        color = _color(q.side, player_color)
        rep = hanging_report(board, color)
        owner = _whose(color, player_color)
        if not rep:
            text = f"None of {owner} pieces is hanging."
        else:
            parts = [f"{h['piece']} on {h['square']} ({h['attackers']} attacker{'s' * (h['attackers'] != 1)}, "
                     f"{h['defenders']} defender{'s' * (h['defenders'] != 1)})" for h in rep]
            text = f"Hanging, {owner}: {_list(parts)}."
        return Answer(q.kind, text, {"color": "white" if color else "black", "hanging": rep,
                                     "rule": "attacked by more enemy pieces than it is defended by (no exchange evaluation)"})
    if q.kind == "evaluation":
        assert evidence is not None, "evaluation needs Stockfish evidence"
        engine = evidence.engine_version if "stockfish" in evidence.engine_version.lower() else f"Stockfish {evidence.engine_version}"
        text = f"{engine} at depth {evidence.depth}: {describe_eval(evidence.cp, evidence.mate, player_color)}."
        return Answer(q.kind, text, {"cp_white": evidence.cp, "mate_white": evidence.mate,
                                     "bucket": eval_bucket(evidence.cp, evidence.mate),
                                     "engine_version": evidence.engine_version, "depth": evidence.depth,
                                     "cached": evidence.cached, "position_id": evidence.position_id})
    raise ValueError(f"unknown question kind {q.kind}")


# --------------------------------------------------------------------------- #
# Quiz: generate from the board, score the spoken answer against it
# --------------------------------------------------------------------------- #


@dataclass
class Quiz:
    kind: QuestionKind
    prompt: str
    expected: dict[str, Any]


def make_quiz(board: chess.Board, player_color: str, rng: random.Random, kind: QuestionKind | None = None,
              evidence: EvalEvidence | None = None) -> Quiz:
    kind = kind or rng.choice(QUIZ_KINDS)
    me = player_color == "white"
    if kind == "piece_location":
        options = [(color, pt) for color in (me, not me) for pt in PIECE_NAMES if board.pieces(pt, color)]
        color, pt = rng.choice(sorted(options, key=lambda o: (o[0] != me, o[1])))
        owner = "your" if color == me else "your opponent's"
        prompt = f"Name every square holding {owner} {PIECE_NAMES[pt]}s."
        return Quiz(kind, prompt, {"squares": piece_squares(board, pt, color), "piece": PIECE_NAMES[pt],
                                   "color": "white" if color else "black"})
    if kind == "square_content":
        occupied = sorted(chess.square_name(s) for s in chess.SQUARES if board.piece_at(s))
        empty_center = sorted(chess.square_name(s) for s in chess.SQUARES
                              if board.piece_at(s) is None and 2 <= chess.square_file(s) <= 5
                              and 2 <= chess.square_rank(s) <= 5)
        pool = occupied if (rng.random() < 0.7 or not empty_center) else empty_center
        sq = rng.choice(pool)
        piece = board.piece_at(chess.parse_square(sq))
        expected = {"square": sq, "piece": PIECE_NAMES[piece.piece_type] if piece else None,
                    "color": ("white" if piece.color else "black") if piece else None}
        return Quiz(kind, f"What is on {sq}? Say the colour and piece, or empty.", expected)
    if kind == "hanging_pieces":
        color = rng.choice([me, not me])
        owner = "your" if color == me else "your opponent's"
        rep = hanging_report(board, color)
        return Quiz(kind, f"Which of {owner} pieces are hanging? Name the squares, or say none.",
                    {"squares": [h["square"] for h in rep], "color": "white" if color else "black",
                     "hanging": rep})
    if kind == "side_to_move":
        return Quiz(kind, "Whose move is it: White or Black?",
                    {"turn": "white" if board.turn == chess.WHITE else "black"})
    if kind == "evaluation":
        assert evidence is not None, "evaluation needs Stockfish evidence"
        return Quiz(kind, "Roughly, who is better here: White, Black, or equal?",
                    {"bucket": eval_bucket(evidence.cp, evidence.mate), "cp_white": evidence.cp,
                     "mate_white": evidence.mate, "engine_version": evidence.engine_version,
                     "depth": evidence.depth, "equal_band_cp": EQUAL_BAND_CP})
    raise ValueError(f"unknown quiz kind {kind}")


@dataclass
class Scored:
    understood: bool
    score: float = 0.0
    said: dict[str, Any] = field(default_factory=dict)


def _pronoun_is_answer(p: Parsed) -> bool:
    """'me' / 'I'm better' / 'you are winning' answer; 'let me think' does not."""
    if len(p.tokens) <= 2:
        return True
    return bool(re.search(r"\b(i'?m|i am|me|you|you're|they|them)\b.*\b(better|winning|ahead|move|turn|to)\b"
                          r"|\b(you|they) are\b", " ".join(p.tokens)))


def _side_word(p: Parsed, player_color: str) -> str | None:
    for s in p.sides + (p.pronoun_sides if _pronoun_is_answer(p) else []):
        if s in ("white", "black"):
            return s
        if s == "player":
            return player_color
        if s == "opponent":
            return "black" if player_color == "white" else "white"
    return None


def score_answer(kind: QuestionKind, expected: dict[str, Any], text: str, player_color: str) -> Scored:
    p = parse(text)
    if kind in ("piece_location", "hanging_pieces"):
        if p.squares:
            said = set(p.squares)
        elif p.has(*NONE_WORDS):
            said = set()
        else:
            return Scored(False)
        want = set(expected["squares"])
        union = said | want
        score = 1.0 if not union else len(said & want) / len(union)
        return Scored(True, round(score, 3), {"squares": sorted(said)})
    if kind == "square_content":
        if p.piece is None and p.has(*EMPTY_WORDS):
            return Scored(True, 1.0 if expected["piece"] is None else 0.0, {"piece": None})
        if p.piece is None:
            return Scored(False)
        said_piece = PIECE_NAMES[p.piece]
        said_color = _side_word(p, player_color)
        if expected["piece"] is None:
            score = 0.0
        else:
            score = 0.5 * (said_piece == expected["piece"]) + 0.5 * (said_color == expected["color"])
        return Scored(True, score, {"piece": said_piece, "color": said_color})
    if kind == "side_to_move":
        said = _side_word(p, player_color)
        if said is None:
            return Scored(False)
        return Scored(True, 1.0 if said == expected["turn"] else 0.0, {"turn": said})
    if kind == "evaluation":
        if p.has(*EQUAL_WORDS):
            said = "equal"
        else:
            said = _side_word(p, player_color)
            if said is None:
                return Scored(False)
        return Scored(True, 1.0 if said == expected["bucket"] else 0.0, {"bucket": said})
    raise ValueError(f"unknown quiz kind {kind}")


def expected_text(kind: QuestionKind, expected: dict[str, Any]) -> str:
    if kind in ("piece_location", "hanging_pieces"):
        return _list(expected["squares"]) if expected["squares"] else "none"
    if kind == "square_content":
        return "empty" if expected["piece"] is None else f"{expected['color']} {expected['piece']}"
    if kind == "side_to_move":
        return expected["turn"].capitalize()
    if kind == "evaluation":
        cp, mate = expected.get("cp_white"), expected.get("mate_white")
        detail = f"mate {mate:+d}" if mate is not None else f"{(cp or 0) / 100:+.2f}"
        bucket = "equal" if expected["bucket"] == "equal" else f"{expected['bucket'].capitalize()} better"
        return f"{bucket} (Stockfish {detail} at depth {expected['depth']})"
    raise ValueError(kind)


# --------------------------------------------------------------------------- #
# Session layer: persistence + engine evidence
# --------------------------------------------------------------------------- #


async def _evidence(session: AsyncSession, board: chess.Board) -> EvalEvidence:
    res = await analyze_board(session, board=board, depth=EVAL_DEPTH, multipv=EVAL_MULTIPV)
    top = res.results[0] if res.results else {}
    return EvalEvidence(cp=top.get("cp"), mate=top.get("mate"), engine_version=res.engine_version,
                        depth=res.depth, cached=res.cached, position_id=str(res.position_id))


def _board(ts: TrainingSession) -> chess.Board:
    return chess.Board(ts.state_json["current_fen"])


@dataclass
class AskOutcome:
    understood: bool
    question_id: Any
    kind: QuestionKind | None
    answer_text: str
    facts: dict[str, Any]
    questions_asked: int


HELP = ("I can answer: where is my knight, what is on e5, is anything hanging, "
        "whose move is it, and what is the evaluation.")


async def ask(session: AsyncSession, *, ts: TrainingSession, alternatives: list[str]) -> AskOutcome:
    """Answer the first recognizer alternative that parses as a board question."""
    state = dict(ts.state_json)
    for text in alternatives:
        q = parse_question(text)
        if q is None:
            continue
        board = _board(ts)
        player_color = state["player_color"]
        evidence = await _evidence(session, board) if q.kind == "evaluation" else None
        ans = answer_question(q, board, player_color, evidence)
        row = PositionQuestion(
            session_id=ts.id, fen=board.fen(), mode="ask", kind=q.kind, prompt=text,
            expected_json={}, answer_text=ans.text, answer_json=ans.facts,
            answered_at=datetime.now(timezone.utc),
        )
        session.add(row)
        state["questions_asked"] = int(state.get("questions_asked", 0)) + 1
        ts.state_json = state
        await session.flush()
        return AskOutcome(True, row.id, q.kind, ans.text, ans.facts, state["questions_asked"])
    return AskOutcome(False, None, None, HELP, {}, int(state.get("questions_asked", 0)))


async def quiz_count(session: AsyncSession, ts: TrainingSession) -> int:
    rows = await session.execute(
        select(PositionQuestion.id).where(PositionQuestion.session_id == ts.id, PositionQuestion.mode == "quiz")
    )
    return len(rows.all())


async def new_quiz(session: AsyncSession, *, ts: TrainingSession, kind: QuestionKind | None) -> PositionQuestion:
    board = _board(ts)
    n = await quiz_count(session, ts)
    rng = random.Random(f"{ts.seed}:quiz:{n}")  # deterministic per session + question index
    chosen = kind or rng.choice(QUIZ_KINDS)
    evidence = await _evidence(session, board) if chosen == "evaluation" else None
    qz = make_quiz(board, ts.state_json["player_color"], rng, chosen, evidence)
    row = PositionQuestion(session_id=ts.id, fen=board.fen(), mode="quiz", kind=qz.kind,
                           prompt=qz.prompt, expected_json=qz.expected, answer_json={})
    session.add(row)
    await session.flush()
    return row


@dataclass
class AnswerOutcome:
    understood: bool
    correct: bool
    score: float | None
    expected_text: str | None
    feedback: str


async def answer_quiz(session: AsyncSession, *, ts: TrainingSession, row: PositionQuestion,
                      alternatives: list[str]) -> AnswerOutcome:
    if row.answered_at is not None:
        raise ValueError("question already answered")
    player_color = ts.state_json["player_color"]
    for text in alternatives:
        s = score_answer(row.kind, row.expected_json, text, player_color)
        if not s.understood:
            continue
        row.answer_text = text
        row.answer_json = s.said
        row.score = s.score
        row.answered_at = datetime.now(timezone.utc)
        await session.flush()
        exp = expected_text(row.kind, row.expected_json)
        if s.score == 1.0:
            feedback = f"Correct: {exp}."
        elif s.score > 0:
            feedback = f"Partly right ({round(s.score * 100)}%). The answer is {exp}."
        else:
            feedback = f"Not quite. The answer is {exp}."
        return AnswerOutcome(True, s.score == 1.0, s.score, exp, feedback)
    return AnswerOutcome(False, False, None, None, f"I didn't catch an answer. {row.prompt}")


async def tally(session: AsyncSession, ts: TrainingSession) -> dict[str, Any]:
    rows = (await session.execute(
        select(PositionQuestion).where(PositionQuestion.session_id == ts.id)
    )).scalars().all()
    quizzes = [r for r in rows if r.mode == "quiz"]
    answered = [r for r in quizzes if r.score is not None]
    return {
        "asked": sum(1 for r in rows if r.mode == "ask"),
        "quizzed": len(quizzes),
        "answered": len(answered),
        "correct": sum(1 for r in answered if r.score == 1.0),
        "mean_score": round(sum(r.score for r in answered) / len(answered), 3) if answered else None,
    }
