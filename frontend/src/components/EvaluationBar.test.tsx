import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SessionEvaluation } from "../api";
import { EvaluationBar } from "./EvaluationBar";

const base: SessionEvaluation = {
  session_id: "session",
  current_fen: "fen",
  analysis_available: true,
  eval_cp_white: 125,
  mate_white: null,
  depth: 18,
  nodes: 42000,
  engine_version: "Stockfish 17.1",
  perspective: "white",
  analyzed_at: null,
  latest_move_classification: "mistake",
};
const plainText = (html: string) => html.replace(/<[^>]*>/g, "");

describe("EvaluationBar", () => {
  it("renders the real scores, scaled bar, engine metadata and move quality", () => {
    const html = renderToStaticMarkup(<EvaluationBar evaluation={base} loading={false} />);
    const text = plainText(html);
    expect(text).toContain("White +1.25");
    expect(text).toContain("Black −1.25");
    expect(html).toContain("Evaluation bar: White 65%, Black 35%");
    expect(text).toContain("Stockfish 17.1");
    expect(text).toContain("Depth 18");
    expect(text).toContain("42,000 nodes");
    expect(text).toContain("Latest move: Mistake");
  });

  it("shows mate notation and does not display a fabricated score when analysis is absent", () => {
    const mate = plainText(renderToStaticMarkup(<EvaluationBar evaluation={{ ...base, eval_cp_white: null, mate_white: 3 }} loading={false} />));
    expect(mate).toContain("White Mate in 3");
    expect(mate).toContain("Black Mated in 3");

    const missing = plainText(renderToStaticMarkup(<EvaluationBar evaluation={{ ...base, analysis_available: false, eval_cp_white: null }} loading={false} />));
    expect(missing).toContain("No saved analysis for this position.");
    expect(missing).not.toContain("White +1.25");
    expect(missing).not.toContain("evaluation-track");
  });

  it("keeps loading and fetch errors free of scores", () => {
    const loading = plainText(renderToStaticMarkup(<EvaluationBar evaluation={null} loading />));
    expect(loading).toContain("Loading saved Stockfish analysis");
    expect(loading).not.toContain("evaluation-track");
    const error = plainText(renderToStaticMarkup(<EvaluationBar evaluation={null} loading={false} error="Analysis unavailable" />));
    expect(error).toContain("Analysis unavailable");
    expect(error).not.toContain("evaluation-track");
  });
});
