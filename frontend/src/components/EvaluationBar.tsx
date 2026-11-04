import type { SessionEvaluation } from "../api";
import { formatMoveClassification, formatSideEvaluation, whiteEvaluationPercent } from "../analysis/evaluation";
import { evaluationForFen } from "../analysis/sessionEvaluation";

interface Props {
  evaluation: SessionEvaluation | null;
  currentFen: string;
  loading: boolean;
  error?: string | null;
  classificationFallback?: string | null;
  onRetry?: () => void;
}

export function EvaluationBar({ evaluation, currentFen, loading, error, classificationFallback, onRetry }: Props) {
  const currentEvaluation = evaluationForFen(evaluation, currentFen);
  const percent = whiteEvaluationPercent(currentEvaluation);
  const white = formatSideEvaluation(currentEvaluation, "white");
  const black = formatSideEvaluation(currentEvaluation, "black");
  const classification = formatMoveClassification(currentEvaluation?.latest_move_classification ?? classificationFallback);

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
      ) : currentEvaluation?.analysis_pending ? (
        <p className="muted" role="status">
          Stockfish analysis is in progress. {onRetry && <button type="button" onClick={onRetry}>Check again</button>}
        </p>
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
            {currentEvaluation?.engine_version && <span>{/stockfish/i.test(currentEvaluation.engine_version) ? currentEvaluation.engine_version : `Stockfish ${currentEvaluation.engine_version}`}</span>}
            {currentEvaluation?.depth != null && <span>Depth {currentEvaluation.depth}</span>}
            {currentEvaluation?.nodes != null && <span>{currentEvaluation.nodes.toLocaleString()} nodes</span>}
          </div>
        </>
      )}
    </section>
  );
}
