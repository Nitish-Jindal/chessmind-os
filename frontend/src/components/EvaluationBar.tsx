import type { SessionEvaluation } from "../api";
import { formatMoveClassification, formatSideEvaluation, whiteEvaluationPercent } from "../analysis/evaluation";

interface Props {
  evaluation: SessionEvaluation | null;
  loading: boolean;
  error?: string | null;
  classificationFallback?: string | null;
}

export function EvaluationBar({ evaluation, loading, error, classificationFallback }: Props) {
  const percent = whiteEvaluationPercent(evaluation);
  const white = formatSideEvaluation(evaluation, "white");
  const black = formatSideEvaluation(evaluation, "black");
  const classification = formatMoveClassification(evaluation?.latest_move_classification ?? classificationFallback);

  return (
    <section className="card evaluation" aria-label="Position evaluation">
      <div className="row evaluation-heading">
        <strong>Position evaluation</strong>
        {classification && <span className="badge" aria-label="Latest move quality">Latest move: {classification}</span>}
      </div>
      {loading ? (
        <p className="muted" role="status">Loading saved Stockfish analysis…</p>
      ) : error ? (
        <p className="muted" role="status">{error}</p>
      ) : percent == null || white == null || black == null ? (
        <p className="muted" role="status">No saved analysis for this position.</p>
      ) : (
        <>
          <div className="evaluation-scores" aria-label={`White ${white}; Black ${black}`}>
            <span>White <strong>{white}</strong></span>
            <span>Black <strong>{black}</strong></span>
          </div>
          <div
            className="evaluation-track"
            role="img"
            aria-label={`Evaluation bar: White ${Math.round(percent)}%, Black ${Math.round(100 - percent)}%`}
          >
            <i style={{ width: `${percent}%` }} />
          </div>
          <div className="evaluation-meta muted">
            {evaluation?.engine_version && <span>{/stockfish/i.test(evaluation.engine_version) ? evaluation.engine_version : `Stockfish ${evaluation.engine_version}`}</span>}
            {evaluation?.depth != null && <span>Depth {evaluation.depth}</span>}
            {evaluation?.nodes != null && <span>{evaluation.nodes.toLocaleString()} nodes</span>}
          </div>
        </>
      )}
    </section>
  );
}
