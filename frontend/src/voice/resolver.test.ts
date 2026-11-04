import { describe, expect, it } from "vitest";
import { Chess } from "chess.js";
import { buildLegalMoves, isBoardQuestion, isOpeningRequest, looksLikeMove, parseCommand, parseQuietCommand, resolveAlternatives, resolveConfirmation, resolveMove, speakMove, speakQuietMove } from "./resolver";

// Legal lists built from chess.js stand in for the backend's python-chess output;
// the resolver only ever consumes the {san, uci} arrays, same as GET /legal.
function legalFor(fen?: string) {
  const c = fen ? new Chess(fen) : new Chess();
  const mv = c.moves({ verbose: true });
  const sans = mv.map((m) => m.san);
  const ucis = mv.map((m) => m.from + m.to + (m.promotion ?? ""));
  return buildLegalMoves(sans, ucis);
}
const sanOf = (r: ReturnType<typeof resolveMove>) => (r.kind === "match" ? r.move.san : r.kind);

describe("resolveMove", () => {
  const start = legalFor();
  it.each([
    ["knight f3", "Nf3"],
    ["Nf3", "Nf3"],
    ["night f 3", "Nf3"],
    ["knight to f three", "Nf3"],
    ["e4", "e4"],
    ["e four", "e4"],
    ["pawn to e4", "e4"],
    ["e2 e4", "e4"],
    ["e2e4", "e4"],
    ["play d4", "d4"],
  ])("%s -> %s", (said, san) => expect(sanOf(resolveMove(said, start))).toBe(san));

  it("rejects illegal / unheard moves", () => {
    expect(resolveMove("knight e5", start).kind).toBe("none");
    expect(resolveMove("banana", start).kind).toBe("none");
  });

  it("resolves spoken and SAN castling forms only to legal backend-list candidates", () => {
    const fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1";
    const whiteLegal = legalFor(fen);
    const phrases: Array<[string, string]> = [
      ["castle kingside", "O-O"],
      ["short castle", "O-O"],
      ["king side castle", "O-O"],
      ["castle on the king side", "O-O"],
      ["castle king's side", "O-O"],
      ["castle queenside", "O-O-O"],
      ["long castle", "O-O-O"],
      ["queen side castle", "O-O-O"],
      ["castle on the queen side", "O-O-O"],
      ["castling queen side", "O-O-O"],
      ["castle queen's side", "O-O-O"],
      ["O-O", "O-O"],
      ["O-O-O", "O-O-O"],
      ["zero zero", "O-O"],
      ["oh oh oh", "O-O-O"],
    ];
    for (const [said, expected] of phrases) {
      const result = resolveMove(said, whiteLegal);
      expect(sanOf(result), said).toBe(expected);
      if (result.kind === "match") expect(whiteLegal.some((move) => move.uci === result.move.uci)).toBe(true);
    }

    const blackLegal = legalFor(fen.replace(" w ", " b "));
    expect(sanOf(resolveMove("castle kingside", blackLegal))).toBe("O-O");
    expect(sanOf(resolveMove("castle queenside", blackLegal))).toBe("O-O-O");
  });

  it("resolves generic castle only when one legal side exists and clarifies when both do", () => {
    const whiteKingSideOnly = legalFor("4k3/8/8/8/8/8/8/4K2R w K - 0 1");
    expect(sanOf(resolveMove("Castle", whiteKingSideOnly))).toBe("O-O");

    const blackQueenSideOnly = legalFor("r3k3/8/8/8/8/8/8/4K3 b q - 0 1");
    expect(sanOf(resolveMove("castling", blackQueenSideOnly))).toBe("O-O-O");

    const bothSides = legalFor("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1");
    const ambiguous = resolveMove("castle", bothSides);
    expect(ambiguous.kind).toBe("ambiguous");
    if (ambiguous.kind === "ambiguous") expect(ambiguous.moves.map((move) => move.san).sort()).toEqual(["O-O", "O-O-O"]);
    const clarified = resolveConfirmation("queenside", bothSides.filter((move) => move.castle));
    expect(clarified.kind).toBe("chosen");
    if (clarified.kind === "chosen") expect(clarified.move.san).toBe("O-O-O");
  });

  it("never interprets an incomplete side-only phrase as castling, even when one side is legal", () => {
    const onlyKingside = legalFor("4k3/8/8/8/8/8/8/4K2R w K - 0 1");
    for (const phrase of ["short", "long", "kingside", "queenside"]) {
      const result = resolveMove(phrase, onlyKingside);
      expect(result.kind, phrase).toBe("none");
      expect(looksLikeMove(phrase), phrase).toBe(false);
    }
  });

  it("reports unavailable castling clearly and rejects incorrect phrases", () => {
    const startPosition = legalFor();
    expect(resolveMove("castle", startPosition).kind).toBe("castle-unavailable");
    expect(resolveMove("castle kingside", startPosition).kind).toBe("castle-unavailable");
    expect(resolveMove("long castle", startPosition).kind).toBe("castle-unavailable");
    expect(resolveAlternatives(["castling", "banana"], startPosition).kind).toBe("castle-unavailable");
    expect(resolveMove("castle west", legalFor("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")).kind).toBe("none");
    expect(resolveMove("castle kingside queenside", startPosition).kind).toBe("none");
  });

  it("captures, pawn captures, bishop vs b-pawn", () => {
    const l = legalFor("rnbqkbnr/ppp1pppp/8/3p4/2P5/8/PP1PPPPP/RNBQKBNR w KQkq - 0 2");
    expect(sanOf(resolveMove("c takes d5", l))).toBe("cxd5");
    expect(sanOf(resolveMove("cxd5", l))).toBe("cxd5");
    const b = legalFor("rnbqkbnr/pp2pppp/8/2pp4/8/1P6/PBPPPPPP/RN1QKBNR w KQkq - 0 3");
    expect(sanOf(resolveMove("bishop b2 c3", b))).toBe("Bc3");
    expect(sanOf(resolveMove("bishop c3", b))).toBe("Bc3");
  });

  it("reports ambiguity when more than one legal move matches", () => {
    // Both knights can reach d2.
    const l = legalFor("4k3/8/8/8/8/8/8/1N2KN2 w - - 0 1");
    const r = resolveMove("knight d2", l);
    expect(r.kind).toBe("ambiguous");
    if (r.kind === "ambiguous") expect(r.moves.map((m) => m.san).sort()).toEqual(["Nbd2", "Nfd2"]);
    // Disambiguated by file or by source square.
    expect(sanOf(resolveMove("knight b d2", l))).toBe("Nbd2");
    expect(sanOf(resolveMove("knight f1 d2", l))).toBe("Nfd2");
  });

  it("promotion without a piece is ambiguous, with a piece it resolves", () => {
    const l = legalFor("8/P6k/8/8/8/8/8/K7 w - - 0 1");
    const r = resolveMove("a8", l);
    expect(r.kind).toBe("ambiguous");
    expect(sanOf(resolveMove("a8 promotes to queen", l))).toBe("a8=Q");
    expect(sanOf(resolveMove("a8 equals knight", l))).toBe("a8=N");
  });
});

describe("resolveConfirmation", () => {
  const l = legalFor("4k3/8/8/8/8/8/8/1N2KN2 w - - 0 1");
  const opts = l.filter((m) => m.to === "d2" && m.piece === "N");
  it("ordinal choice", () => {
    const c = resolveConfirmation("the second one", opts);
    expect(c.kind).toBe("chosen");
  });
  it("narrowing by restating", () => {
    const c = resolveConfirmation("knight from b1", opts);
    expect(c.kind).toBe("chosen");
    if (c.kind === "chosen") expect(c.move.san).toBe("Nbd2");
  });
  it("cancel and unclear", () => {
    expect(resolveConfirmation("no", opts).kind).toBe("cancel");
    expect(resolveConfirmation("hmm", opts).kind).toBe("unclear");
  });
  it("yes confirms a single option", () => {
    expect(resolveConfirmation("yes", [opts[0]]).kind).toBe("chosen");
  });
});

describe("misc", () => {
  it("commands", () => {
    expect(parseCommand("reveal the board")).toBe("reveal");
    expect(parseCommand("say that again")).toBe("repeat");
    expect(parseCommand("e4")).toBeNull();
  });
  it("speakMove", () => {
    expect(speakMove("Nf3")).toBe("knight f3");
    expect(speakMove("exd5")).toBe("pawn e takes d5");
    expect(speakMove("Nbd2")).toBe("knight from b, d2");
    expect(speakMove("O-O")).toBe("castles kingside");
    expect(speakMove("Qh5+")).toBe("queen h5, check");
  });

  it("uses concise quiet-mode wording for backend SAN", () => {
    expect(speakQuietMove("e5")).toBe("Pawn e5");
    expect(speakQuietMove("Nf6")).toBe("Knight f6");
    expect(speakQuietMove("Bb5")).toBe("Bishop b5");
    expect(speakQuietMove("Qxd4")).toBe("Queen takes d4");
    expect(speakQuietMove("exd5")).toBe("Pawn e takes d5");
    expect(speakQuietMove("e8=Q+")).toBe("Pawn e8 promotes to queen, check");
    expect(speakQuietMove("O-O")).toBe("Castle kingside");
    expect(speakQuietMove("O-O-O")).toBe("Castle queenside");
    expect(speakQuietMove("Qh5+")).toBe("Queen h5, check");
    expect(speakQuietMove("Qh7#")).toBe("Queen h7, checkmate");
    expect(speakQuietMove("O-O#")).toBe("Castle kingside, checkmate");
  });
});

describe("board questions vs moves", () => {
  it("routes questions to the ask endpoint", () => {
    for (const q of ["where is my knight", "What's on e5?", "anything hanging", "whose move is it", "what's the eval",
      "who is better", "is my bishop hanging", "Are your rooks connected?"]) {
      expect(isBoardQuestion(q)).toBe(true);
    }
  });
  it("leaves moves and commands alone", () => {
    for (const m of ["e4", "knight f3", "castle kingside", "bishop takes c4", "e takes d5", "queen to h5", "first", "reveal"]) {
      expect(isBoardQuestion(m)).toBe(false);
    }
  });
  it("recognizes quiz commands", () => {
    expect(parseCommand("quiz me")).toBe("quiz");
    expect(parseCommand("test me on this position")).toBe("quiz");
    expect(parseCommand("knight f3")).toBe(null);
  });
});

describe("session commands", () => {
  it("quiet mode on/off", () => {
    expect(parseQuietCommand("quiet mode")).toBe(true);
    expect(parseQuietCommand("quiet mode on")).toBe(true);
    expect(parseQuietCommand("be quiet")).toBe(true);
    expect(parseQuietCommand("quiet mode off")).toBe(false);
    expect(parseQuietCommand("turn off quiet mode")).toBe(false);
    expect(parseQuietCommand("knight f3")).toBe(null);
  });
  it("opening requests are not moves", () => {
    expect(isOpeningRequest("play the Caro-Kann against me")).toBe(true);
    expect(isOpeningRequest("play d4 against me")).toBe(true);
    expect(isOpeningRequest("play d4")).toBe(false);
  });
  it("move-shaped utterances, legal or not", () => {
    expect(looksLikeMove("king e2")).toBe(true);
    expect(looksLikeMove("knight e5")).toBe(true);
    expect(looksLikeMove("castle kingside")).toBe(true);
    expect(looksLikeMove("banana")).toBe(false);
    expect(looksLikeMove("hello there")).toBe(false);
    // Illegal at the start: the resolver finds nothing, but the utterance was a move.
    expect(resolveMove("king e2", legalFor()).kind).toBe("none");
  });
});
