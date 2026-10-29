// Spoken-move resolver.
//
// Input: a transcript ("knight f3", "Nf3", "castle kingside", "e four") and the
// legal move lists returned by GET /sessions/{id}/legal. Output: the legal move(s)
// that match. The resolver never invents a move — it only *selects* among moves the
// backend (python-chess) already declared legal, and callers must ask for
// confirmation when more than one matches.

export interface LegalMove {
  san: string;
  uci: string;
  piece: "P" | "N" | "B" | "R" | "Q" | "K";
  from: string;
  to: string;
  capture: boolean;
  promotion?: "q" | "r" | "b" | "n";
  castle?: "k" | "q";
}

export type Resolution =
  | { kind: "match"; move: LegalMove }
  | { kind: "ambiguous"; moves: LegalMove[] }
  | { kind: "none" };

/** Build structured moves from the two parallel arrays the backend returns. */
export function buildLegalMoves(sans: string[], ucis: string[]): LegalMove[] {
  return sans.map((san, i) => {
    const uci = ucis[i];
    const castle = san.startsWith("O-O-O") ? "q" : san.startsWith("O-O") ? "k" : undefined;
    const first = san[0];
    const piece = castle ? "K" : "NBRQK".includes(first) ? (first as LegalMove["piece"]) : "P";
    const promo = uci.length === 5 ? (uci[4] as LegalMove["promotion"]) : undefined;
    return {
      san,
      uci,
      piece,
      from: uci.slice(0, 2),
      to: uci.slice(2, 4),
      capture: san.includes("x"),
      promotion: promo,
      castle,
    };
  });
}

const PIECE_WORDS: Record<string, string> = {
  knight: "N", night: "N", horse: "N",
  bishop: "B",
  rook: "R",
  queen: "Q",
  king: "K",
  pawn: "P",
};

// Letter names and the way recognizers commonly mishear them.
const FILE_WORDS: Record<string, string> = {
  a: "a", ay: "a", eh: "a", alpha: "a", alfa: "a",
  b: "b", be: "b", bee: "b", bravo: "b",
  c: "c", see: "c", sea: "c", charlie: "c",
  d: "d", dee: "d", delta: "d",
  e: "e", echo: "e",
  f: "f", eff: "f", ef: "f", foxtrot: "f",
  g: "g", gee: "g", golf: "g",
  h: "h", age: "h", aitch: "h", hotel: "h",
};

const NUMBER_WORDS: Record<string, string> = {
  one: "1", won: "1", two: "2", three: "3", four: "4", for: "4", fore: "4",
  five: "5", six: "6", seven: "7", eight: "8", ate: "8",
};

const DROP = new Set(["the", "a", "an", "move", "play", "my", "i", "will", "and", "check", "checkmate", "mate", "plus", "please", "go", "on", "at", "square", "with"]);

export interface Descriptor {
  castle?: "k" | "q";
  piece?: LegalMove["piece"];
  capture: boolean;
  promotion?: LegalMove["promotion"];
  files: string[]; // bare files seen, in order
  squares: string[]; // complete squares seen, in order
  fromSquare?: string; // square introduced by an explicit "from"
  ranks: string[]; // bare ranks seen
}

/** Lowercase, strip punctuation, and expand SAN glued together ("Nf3", "exd5", "e8=Q"). */
function tokenize(raw: string): string[] {
  let t = raw.toLowerCase().replace(/[.,!?;:]/g, " ");
  t = t.replace(/zero zero zero|oh oh oh/g, "0-0-0").replace(/zero zero|oh oh/g, "0-0");
  t = t.replace(/0-0-0|o-o-o|0 0 0|o o o/g, " castle queenside ").replace(/0-0|o-o|0 0|o o/g, " castle kingside ");
  t = t.replace(/[-_]/g, " ").replace(/=/g, " equals ").replace(/x/g, (m, i, s) => {
    // "x" is a capture marker only when it glues two SAN parts, not inside a word like "next"
    const prev = s[i - 1], next = s[i + 1];
    return prev && next && /[a-h1-8nbrqk]/.test(prev) && /[a-h]/.test(next) ? " takes " : m;
  });
  // Split glued SAN: "nf3" -> "n f3", "exd5" -> "e takes d5", "e4" stays.
  t = t.replace(/\b([nbrqk])([a-h][1-8])\b/g, "$1 $2");
  t = t.replace(/\b([nbrqk])([a-h])([a-h][1-8])\b/g, "$1 $2 $3");
  t = t.replace(/\b([a-h])([a-h][1-8])\b/g, "$1 $2");
  t = t.replace(/\b([a-h][1-8])([a-h][1-8])\b/g, "$1 $2"); // uci "e2e4"
  t = t.replace(/\b([a-h][1-8])(q|r|b|n)\b/g, "$1 equals $2"); // "e8q"
  t = t.replace(/\b([a-h])\s*([1-8])\b/g, "$1$2"); // "e 4" -> "e4"
  return t.split(/\s+/).filter(Boolean);
}

export function describe(raw: string): Descriptor {
  const d: Descriptor = { capture: false, files: [], squares: [], ranks: [] };
  const toks = tokenize(raw);
  // Convert "e four"/"f three" spoken forms: file word followed by number word.
  const out: string[] = [];
  for (let i = 0; i < toks.length; i++) {
    const tk = toks[i];
    const file = FILE_WORDS[tk];
    const nextNum = NUMBER_WORDS[toks[i + 1] ?? ""] ?? (/^[1-8]$/.test(toks[i + 1] ?? "") ? toks[i + 1] : undefined);
    if (file && nextNum) {
      out.push(file + nextNum);
      i++;
    } else out.push(tk);
  }

  let promoNext = false;
  let fromNext = false;
  for (let i = 0; i < out.length; i++) {
    const tk = out[i];
    if (tk === "castle" || tk === "castles" || tk === "castling") {
      const rest = out.slice(i + 1).join(" ");
      if (/queen|long/.test(rest)) d.castle = "q";
      else if (/king|short/.test(rest)) d.castle = "k";
      else if (!d.castle) d.castle = undefined;
      if (d.castle) return d;
      continue;
    }
    if (tk === "long" && out[i + 1] === "castle") { d.castle = "q"; return d; }
    if (tk === "short" && out[i + 1] === "castle") { d.castle = "k"; return d; }
    if (tk === "takes" || tk === "capture" || tk === "captures" || tk === "taking" || tk === "x") { d.capture = true; continue; }
    if (tk === "equals" || tk === "promotes" || tk === "promote" || tk === "promoting") { promoNext = true; continue; }
    if (tk === "from") { fromNext = true; continue; }
    if (DROP.has(tk)) continue;
    if (promoNext || (d.squares.length > 0 && /^(queen|rook|bishop|knight)$/.test(tk))) {
      const p = tk.length === 1 ? tk : PIECE_WORDS[tk]?.toLowerCase();
      if (p && "qrbn".includes(p)) { d.promotion = p as Descriptor["promotion"]; promoNext = false; continue; }
    }
    if (/^[a-h][1-8]$/.test(tk)) {
      if (fromNext) d.fromSquare = tk;
      else d.squares.push(tk);
      fromNext = false;
      continue;
    }
    if (tk === "b") { d.files.push("b"); continue; } // bishop or b-file: decided after the loop
    if (/^[nrqk]$/.test(tk) && d.piece === undefined && d.squares.length === 0) {
      d.piece = tk.toUpperCase() as Descriptor["piece"];
      continue;
    }
    if (PIECE_WORDS[tk]) { d.piece = PIECE_WORDS[tk] as Descriptor["piece"]; continue; }
    if (FILE_WORDS[tk]) { d.files.push(FILE_WORDS[tk]); continue; }
    if (/^[1-8]$/.test(tk)) { d.ranks.push(tk); continue; }
    if (NUMBER_WORDS[tk]) { d.ranks.push(NUMBER_WORDS[tk]); continue; }
  }
  // A lone "b" followed by a square is the bishop ("b c4"), but "b takes d5" is the b-pawn.
  if (d.files[0] === "b" && d.piece === undefined && d.squares.length >= 1 && !d.capture && d.files.length === 1) {
    d.piece = "B";
    d.files = [];
  }
  return d;
}

/** Select the legal moves matching what was said. */
export function resolveMove(transcript: string, legal: LegalMove[]): Resolution {
  const d = describe(transcript);

  if (d.castle) {
    const m = legal.filter((l) => l.castle === d.castle);
    return pick(m);
  }

  let pool = legal.filter((l) => !l.castle);
  const toSquare = d.squares.length >= 2 ? d.squares[1] : d.squares[0];
  const fromSquare = d.fromSquare ?? (d.squares.length >= 2 ? d.squares[0] : undefined);
  if (!toSquare && !fromSquare) return { kind: "none" };

  if (toSquare) pool = pool.filter((l) => l.to === toSquare);
  if (fromSquare) pool = pool.filter((l) => l.from === fromSquare);
  if (d.promotion) pool = pool.filter((l) => l.promotion === d.promotion);

  // File disambiguator: "knight b d2" / "b takes d5" / "exd5".
  const fileHint = d.files.length ? d.files[d.files.length - 1] : undefined;
  if (d.piece) {
    pool = pool.filter((l) => l.piece === d.piece);
    if (fileHint) {
      const f = pool.filter((l) => l.from[0] === fileHint);
      if (f.length) pool = f;
    }
  } else if (fileHint) {
    pool = pool.filter((l) => l.piece === "P" && l.from[0] === fileHint);
  } else {
    // Bare square: a pawn move by default; fall back to any piece if no pawn can go there.
    const pawns = pool.filter((l) => l.piece === "P");
    if (pawns.length) pool = pawns;
  }
  if (d.ranks.length) {
    const r = pool.filter((l) => l.from[1] === d.ranks[0]);
    if (r.length) pool = r;
  }
  if (d.capture) {
    const c = pool.filter((l) => l.capture);
    if (c.length) pool = c;
  }
  return pick(pool);
}

function pick(m: LegalMove[]): Resolution {
  if (m.length === 0) return { kind: "none" };
  if (m.length === 1) return { kind: "match", move: m[0] };
  return { kind: "ambiguous", moves: m };
}

/** Try each STT alternative in order; first that yields a hit wins. */
export function resolveAlternatives(alts: string[], legal: LegalMove[]): Resolution {
  for (const a of alts) {
    const r = resolveMove(a, legal);
    if (r.kind !== "none") return r;
  }
  return { kind: "none" };
}

const ORDINALS: Record<string, number> = {
  first: 0, one: 0, "1": 0, "1st": 0, second: 1, two: 1, to: 1, "2": 1, "2nd": 1,
  third: 2, three: 2, "3": 2, "3rd": 2, fourth: 3, four: 3, for: 3, "4": 3, "4th": 3,
};

export type Confirmation =
  | { kind: "chosen"; move: LegalMove }
  | { kind: "narrowed"; moves: LegalMove[] }
  | { kind: "cancel" }
  | { kind: "unclear" };

/** Interpret the reply to "Did you mean A or B?". */
export function resolveConfirmation(transcript: string, options: LegalMove[]): Confirmation {
  const t = transcript.toLowerCase().replace(/[.,!?]/g, " ").trim();
  const words = t.split(/\s+/);
  if (/^(no|nope|cancel|never mind|nevermind|neither|stop)\b/.test(t)) return { kind: "cancel" };
  if (options.length === 1 && /^(yes|yeah|yep|yup|correct|confirm|right|ok|okay|sure)\b/.test(t)) {
    return { kind: "chosen", move: options[0] };
  }
  for (const w of words) {
    const idx = ORDINALS[w];
    if (idx !== undefined && idx < options.length && words.length <= 3) return { kind: "chosen", move: options[idx] };
  }
  const r = resolveMove(transcript, options);
  if (r.kind === "match") return { kind: "chosen", move: r.move };
  if (r.kind === "ambiguous") return { kind: "narrowed", moves: r.moves };
  return { kind: "unclear" };
}

/** Voice-level commands that are not moves. */
export type VoiceCommand = "reveal" | "repeat" | "resign" | "quiz" | null;
export function parseCommand(transcript: string): VoiceCommand {
  const t = transcript.toLowerCase();
  if (/\b(reveal|show (me )?the board|show board)\b/.test(t)) return "reveal";
  if (/\b(repeat|say (that )?again|what was (that|the last))\b/.test(t)) return "repeat";
  if (/\b(resign|i give up)\b/.test(t)) return "resign";
  if (/\b(quiz me|test me|ask me)\b|^quiz\b/.test(t)) return "quiz";
  return null;
}

/**
 * True when an utterance is a question about the board ("where is my knight", "what's on e5",
 * "anything hanging?", "whose move", "what's the eval") rather than a move. Questions go to
 * POST /sessions/{id}/ask, which parses and answers them from the server's board.
 */
export function isBoardQuestion(transcript: string): boolean {
  const t = transcript.toLowerCase().trim();
  if (!t) return false;
  if (/\?$/.test(t)) return true;
  if (/^(where|what|what's|whats|which|whose|who|who's|is|are|any|anything|how|tell|can you|do i|does)\b/.test(t)) return true;
  return /\b(hanging|undefended|eval|evaluation|whose|better|winning)\b/.test(t);
}

/** Natural phrasing of a move for TTS ("Nf3" -> "knight f3"). */
export function speakMove(san: string): string {
  if (san.startsWith("O-O-O")) return "castles queenside";
  if (san.startsWith("O-O")) return "castles kingside";
  const names: Record<string, string> = { N: "knight", B: "bishop", R: "rook", Q: "queen", K: "king" };
  let s = san.replace(/[+#]/g, "");
  const suffix = san.endsWith("#") ? ", checkmate" : san.endsWith("+") ? ", check" : "";
  let promo = "";
  const pm = s.match(/=([QRBN])/);
  if (pm) { promo = ` promotes to ${names[pm[1]]}`; s = s.replace(/=[QRBN]/, ""); }
  const piece = names[s[0]];
  if (piece) s = s.slice(1);
  const dis = piece && s.match(/^([a-h1-8])(x?[a-h][1-8])$/); // Nbd2 -> "knight from b, d2"
  if (dis) s = `from ${dis[1]}, ${dis[2]}`;
  s = s.replace("x", " takes ");
  return `${piece ?? "pawn"} ${s}`.replace(/\s+/g, " ").trim() + promo + suffix;
}
