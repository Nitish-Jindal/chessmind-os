import { describe, expect, it } from "vitest";
import type { SessionEvaluation } from "../api";
import { formatMoveClassification, formatSideEvaluation, whiteEvaluationPercent } from "./evaluation";

function analysis(overrides: Partial<SessionEvaluation> = {}): SessionEvaluation {
  return {
    session_id: "session",
    current_fen: "fen",
    analysis_available: true,
    analysis_pending: false,
    eval_cp_white: 125,
    mate_white: null,
    depth: 18,
    nodes: 42_000,
    engine_version: "17.1",
    perspective: "white",
    analyzed_at: null,
    latest_move_classification: "best",
    ...overrides,
  };
}

describe("evaluation display formatting", () => {
  it("formats White-centric centipawns for both sides", () => {
    const value = analysis();
    expect(formatSideEvaluation(value, "white")).toBe("+1.25");
    expect(formatSideEvaluation(value, "black")).toBe("−1.25");
  });

  it("normalizes a Black-perspective score back to the White-centric convention", () => {
    const value = analysis({ eval_cp_white: -80, perspective: "black" });
    expect(formatSideEvaluation(value, "white")).toBe("+0.80");
    expect(formatSideEvaluation(value, "black")).toBe("−0.80");
  });

  it("formats mate scores from the stored perspective", () => {
    expect(formatSideEvaluation(analysis({ eval_cp_white: null, mate_white: 3 }), "white")).toBe("Mate in 3");
    expect(formatSideEvaluation(analysis({ eval_cp_white: null, mate_white: -2 }), "white")).toBe("Mated in 2");
    expect(formatSideEvaluation(analysis({ eval_cp_white: null, mate_white: 3 }), "black")).toBe("Mated in 3");
  });

  it("scales White share monotonically and handles mate endpoints", () => {
    expect(whiteEvaluationPercent(analysis({ eval_cp_white: 0 }))).toBe(50);
    expect(whiteEvaluationPercent(analysis({ eval_cp_white: 400 }))).toBeGreaterThan(50);
    expect(whiteEvaluationPercent(analysis({ eval_cp_white: -400 }))).toBeLessThan(50);
    expect(whiteEvaluationPercent(analysis({ eval_cp_white: null, mate_white: 2 }))).toBe(100);
    expect(whiteEvaluationPercent(analysis({ eval_cp_white: null, mate_white: -2 }))).toBe(0);
  });

  it("does not invent a score when analysis or its perspective is missing", () => {
    expect(formatSideEvaluation(analysis({ analysis_available: false }), "white")).toBeNull();
    expect(whiteEvaluationPercent(analysis({ analysis_available: false }))).toBeNull();
    expect(formatSideEvaluation(analysis({ perspective: null }), "white")).toBeNull();
    expect(whiteEvaluationPercent(analysis({ perspective: null }))).toBeNull();
  });

  it.each([["best", "Best"], ["playable", "Good"], ["inaccuracy", "Inaccuracy"], ["mistake", "Mistake"], ["blunder", "Blunder"]])(
    "labels backend move quality %s as %s",
    (source, label) => expect(formatMoveClassification(source)).toBe(label),
  );
});
