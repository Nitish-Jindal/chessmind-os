"""Opening taxonomy: ECO code + name for a position, from the Lichess chess-openings dataset.

The data (``chessmind/data/openings/*.tsv``, CC0) lists ~3,900 named lines. Each line
is replayed with python-chess once per process and keyed by the canonical EPD of its
final position, so a transposition reaches the same name. Nothing here is generated:
a position either is one of those named positions or has no name.

Also here, because they read the same table:

- ``announcement_for`` decides whether reaching a position should announce its name.
- ``parse_opening_request`` turns "play the Caro-Kann against me" / "play d4 against me"
  into a serializable request the opponent sampler can follow (``book_moves``).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import chess

from chessmind.services.canonical_fen import canonical_key

# Names that describe the first move rather than an opening; reaching them is not announced.
GENERIC_NAMES = frozenset({
    "King's Pawn Game",
    "Queen's Pawn Game",
    "King's Knight Opening",
    "King's Knight Opening: Normal Variation",
})


@dataclass(frozen=True)
class OpeningEntry:
    eco: str
    name: str
    pgn: str  # the defining line as published, e.g. "1. e4 c6"
    san_line: tuple[str, ...]
    path_keys: tuple[str, ...]  # canonical keys of every position on the line, start first

    @property
    def family(self) -> str:
        return self.name.split(":")[0].strip()

    def to_json(self) -> dict[str, Any]:
        return {"eco": self.eco, "name": self.name, "pgn": self.pgn}


@dataclass(frozen=True)
class Taxonomy:
    by_key: dict[str, OpeningEntry]  # the entry named at that exact position
    entries: tuple[OpeningEntry, ...]


@lru_cache(maxsize=1)
def taxonomy() -> Taxonomy:
    """Parse and replay every TSV line once. Illegal lines would raise: the data is trusted CC0."""
    entries: list[OpeningEntry] = []
    by_key: dict[str, OpeningEntry] = {}
    folder = resources.files("chessmind.data") / "openings"
    for name in ("a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv"):
        with (folder / name).open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                board = chess.Board()
                keys = [canonical_key(board)]
                sans: list[str] = []
                for tok in row["pgn"].split():
                    if tok[0].isdigit():
                        continue
                    board.push_san(tok)
                    sans.append(tok)
                    keys.append(canonical_key(board))
                entry = OpeningEntry(row["eco"], row["name"], row["pgn"], tuple(sans), tuple(keys))
                entries.append(entry)
                # Several lines can end on one position (transpositions); the shortest line names it.
                prev = by_key.get(keys[-1])
                if prev is None or len(sans) < len(prev.san_line):
                    by_key[keys[-1]] = entry
    return Taxonomy(by_key=by_key, entries=tuple(entries))


def lookup(board: chess.Board) -> OpeningEntry | None:
    return taxonomy().by_key.get(canonical_key(board))


def lookup_key(key: str) -> OpeningEntry | None:
    return taxonomy().by_key.get(key)


def announcement_for(board: chess.Board, announced: list[str]) -> OpeningEntry | None:
    """The opening to announce on reaching ``board``, or None.

    A name is announced the first time it is reached in a session; generic first-move names
    are never announced, and positions without a name announce nothing.
    """
    entry = lookup(board)
    if entry is None or entry.name in GENERIC_NAMES or entry.name in announced:
        return None
    return entry


# --------------------------------------------------------------------------- #
# "Play <opening> against me"
# --------------------------------------------------------------------------- #


def _norm(text: str) -> str:
    t = text.lower().replace("’", "'").replace("'", "").replace("defence", "defense")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass(frozen=True)
class _Named:
    label: str
    match: str  # "prefix" | "contains"
    text: str


# Spoken names (normalized) → the taxonomy names they cover. Common recognizer mishearings included.
_ALIASES: dict[str, _Named] = {}
for _aliases, _named in [
    (("london", "london system", "the london"), _Named("London System", "contains", "London System")),
    (("caro kann", "caro kann defense", "caro can", "karo kann", "karo can", "caro khan", "carol khan", "caro"),
     _Named("Caro-Kann Defense", "prefix", "Caro-Kann Defense")),
    (("spanish", "spanish game", "spanish opening", "ruy lopez", "roy lopez", "rui lopez", "ruy lopez opening",
      "spanish ruy lopez", "ruy lopez spanish", "spanish or ruy lopez"),
     _Named("Ruy Lopez", "prefix", "Ruy Lopez")),
    (("italian", "italian game", "italian opening", "giuoco piano"), _Named("Italian Game", "prefix", "Italian Game")),
    (("queens gambit", "queen gambit", "queens gambit opening"), _Named("Queen's Gambit", "prefix", "Queen's Gambit")),
    (("sicilian", "sicilian defense", "the sicilian"), _Named("Sicilian Defense", "prefix", "Sicilian Defense")),
    (("french", "french defense"), _Named("French Defense", "prefix", "French Defense")),
    (("kings indian", "kings indian defense", "kid"), _Named("King's Indian Defense", "prefix", "King's Indian Defense")),
    (("scandinavian", "scandinavian defense", "center counter"), _Named("Scandinavian Defense", "prefix", "Scandinavian Defense")),
    (("slav", "slav defense"), _Named("Slav Defense", "prefix", "Slav Defense")),
    (("english", "english opening"), _Named("English Opening", "prefix", "English Opening")),
    (("scotch", "scotch game"), _Named("Scotch Game", "prefix", "Scotch Game")),
    (("nimzo indian", "nimzo"), _Named("Nimzo-Indian Defense", "prefix", "Nimzo-Indian Defense")),
    (("pirc", "pirc defense"), _Named("Pirc Defense", "prefix", "Pirc Defense")),
]:
    for _a in _aliases:
        _ALIASES[_a] = _named

_REQUEST_RE = re.compile(
    r"\b(?:play|playing|use|try|open with|go for)\s+(?:the\s+|a\s+|an\s+)?(?P<what>.+?)\s+"
    r"(?:against|vs|versus|on)\s+me\b",
    re.IGNORECASE,
)


def _matches(named: _Named, name: str) -> bool:
    if named.match == "contains":
        return named.text in name
    return name == named.text or name.startswith(named.text + ":") or name.startswith(named.text + ",") \
        or name.startswith(named.text + " ")


@lru_cache(maxsize=64)
def book_keys(match: str, text: str) -> frozenset[str]:
    named = _Named(text, match, text)
    keys: set[str] = set()
    for e in taxonomy().entries:
        if _matches(named, e.name):
            keys.update(e.path_keys)
    return frozenset(keys)


def _defining_entry(named: _Named) -> OpeningEntry | None:
    best: OpeningEntry | None = None
    for e in taxonomy().entries:
        if _matches(named, e.name) and (best is None or len(e.san_line) < len(best.san_line)):
            best = e
    return best


def _named_for(phrase: str) -> _Named | None:
    p = _norm(phrase)
    if p in _ALIASES:
        return _ALIASES[p]
    # Any other taxonomy family by name, e.g. "Dutch", "Grunfeld Defense", "Queen's Gambit Declined".
    hits = [e for e in taxonomy().entries if _norm(e.name).startswith(p) or _norm(e.family) == p]
    if not hits:
        return None
    shortest = min(hits, key=lambda e: (len(e.san_line), e.name))
    text = shortest.name if _norm(shortest.name).startswith(p) else shortest.family
    return _Named(text, "prefix", text)


_PIECES = {"knight": "N", "night": "N", "horse": "N", "bishop": "B", "rook": "R", "queen": "Q", "king": "K"}
_FILES = {"a": "a", "b": "b", "be": "b", "bee": "b", "c": "c", "see": "c", "sea": "c", "d": "d", "dee": "d",
          "e": "e", "f": "f", "g": "g", "h": "h"}
_NUMS = {"one": "1", "two": "2", "three": "3", "four": "4", "for": "4", "five": "5", "six": "6",
         "seven": "7", "eight": "8"}


def spoken_to_san(phrase: str) -> str | None:
    """'d4', 'd 4', 'e four', 'pawn to e4', 'knight f3', 'Nf3', 'c4' → SAN text (unvalidated)."""
    raw = phrase.strip()
    if re.fullmatch(r"[NBRQK]?[a-h]?[1-8]?x?[a-h][1-8](=[NBRQ])?[+#]?|O-O(-O)?", raw):
        return raw
    toks = [t for t in re.split(r"[\s\-]+", raw.lower()) if t and t not in {"to", "pawn", "move", "the", "a"}]
    piece = ""
    if toks and toks[0] in _PIECES:
        piece = _PIECES[toks.pop(0)]
    joined = "".join(_NUMS.get(t, _FILES.get(t, t)) for t in toks)
    if re.fullmatch(r"[a-h][1-8]", joined):
        return piece + joined
    m = re.fullmatch(r"([nbrqk])([a-h][1-8])", joined)
    if m and not piece:
        return m.group(1).upper() + m.group(2)
    return None


def parse_opening_request(text: str) -> dict[str, Any] | None:
    """Parse an utterance into an opening request, or None if it is not one.

    Returns ``{"kind": "opening", "label", "match", "text", "eco", "pgn"}`` for a named
    opening, ``{"kind": "move", "san_text"}`` for a single move (validated later against
    the board), or ``{"kind": "unknown", "phrase"}`` when the shape fits but nothing matched.
    """
    m = _REQUEST_RE.search(text)
    if not m:
        return None
    what = m.group("what").strip()
    named = _named_for(what)
    if named is not None:
        entry = _defining_entry(named)
        return {
            "kind": "opening",
            "label": named.label,
            "match": named.match,
            "text": named.text,
            "eco": entry.eco if entry else None,
            "pgn": entry.pgn if entry else None,
        }
    san = spoken_to_san(re.sub(r"^(?:1\s*\.?\s*)", "", what))
    if san is not None:
        return {"kind": "move", "san_text": san}
    return {"kind": "unknown", "phrase": what}


@lru_cache(maxsize=64)
def _defining_path(pgn: str) -> tuple[tuple[str, str], ...]:
    """(canonical key before the move, uci) for each move of the defining line."""
    board, out = chess.Board(), []
    for tok in pgn.split():
        if tok[0].isdigit():
            continue
        mv = board.parse_san(tok)
        out.append((canonical_key(board), mv.uci()))
        board.push(mv)
    return tuple(out)


def book_moves(board: chess.Board, request: dict[str, Any]) -> list[chess.Move]:
    """Legal moves from ``board`` that stay on a named line of the requested opening.

    While the game is on the opening's defining line (``request["pgn"]``, e.g. 1. d4 d5 2. c4
    for the Queen's Gambit), the only book move is that line's next move, so the usual move
    order is played rather than a transposition. After that, any move that stays on one of the
    family's named lines counts. Empty when the game has left (or never reached) those lines:
    the caller then falls back to normal sampling.
    """
    key = canonical_key(board)
    for before, uci in _defining_path(request.get("pgn") or ""):
        if before == key:
            mv = chess.Move.from_uci(uci)
            if mv in board.legal_moves:
                return [mv]
    keys = book_keys(request["match"], request["text"])
    if key not in keys:
        return []
    out: list[chess.Move] = []
    for mv in board.legal_moves:
        board.push(mv)
        try:
            if canonical_key(board) in keys:
                out.append(mv)
        finally:
            board.pop()
    return out
