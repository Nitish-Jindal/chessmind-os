import type { SessionEvaluation } from "../api";

export type ChessSide = "white" | "black";

function whiteScore(value: number | null, perspective: string | null): number | null {
  if (value == null || !Number.isFinite(value)) return null;
  if (perspective === "white") return value;
  if (perspective === "black") return -value;
  return null;
}

export function formatSideEvaluation(evaluation: SessionEvaluation | null, side: ChessSide): string | null {
  if (!evaluation?.analysis_available) return null;
  const multiplier = side === "white" ? 1 : -1;
  const mate = whiteScore(evaluation.mate_white, evaluation.perspective);
  if (mate != null) {
    const sideMate = mate * multiplier;
    if (sideMate === 0) return "Mate";
    return sideMate > 0 ? `Mate in ${sideMate}` : `Mated in ${Math.abs(sideMate)}`;
  }
  const cp = whiteScore(evaluation.eval_cp_white, evaluation.perspective);
  if (cp == null) return null;
  const sideCp = cp * multiplier;
  if (sideCp === 0) return "0.00";
  const value = (Math.abs(sideCp) / 100).toFixed(2);
  return `${sideCp > 0 ? "+" : "−"}${value}`;
}

/** White's share of the display bar, derived only from a saved Stockfish score. */
export function whiteEvaluationPercent(evaluation: SessionEvaluation | null): number | null {
  if (!evaluation?.analysis_available) return null;
  const mate = whiteScore(evaluation.mate_white, evaluation.perspective);
  if (mate != null) return mate > 0 ? 100 : mate < 0 ? 0 : 50;
  const cp = whiteScore(evaluation.eval_cp_white, evaluation.perspective);
  if (cp == null) return null;
  return Math.max(0, Math.min(100, 50 + 50 * Math.tanh(cp / 400)));
}

export function formatMoveClassification(classification: string | null | undefined): string | null {
  if (!classification) return null;
  const known: Record<string, string> = {
    best: "Best",
    playable: "Good",
    good: "Good",
    inaccuracy: "Inaccuracy",
    mistake: "Mistake",
    blunder: "Blunder",
  };
  return known[classification.toLowerCase()] ?? `${classification[0].toUpperCase()}${classification.slice(1)}`;
}
