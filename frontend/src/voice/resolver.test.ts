import { describe, expect, it } from "vitest";
import { Chess } from "chess.js";
import { buildLegalMoves, parseCommand, resolveConfirmation, resolveMove, speakMove } from "./resolver";

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

  it("castling phrasings", () => {
    const fen = "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1";
    const l = legalFor(fen);
    expect(sanOf(resolveMove("castle kingside", l))).toBe("O-O");
    expect(sanOf(resolveMove("short castle", l))).toBe("O-O");
    expect(sanOf(resolveMove("castles queenside", l))).toBe("O-O-O");
    expect(sanOf(resolveMove("O-O-O", l))).toBe("O-O-O");
    expect(sanOf(resolveMove("zero zero", l))).toBe("O-O");
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
});
