import type { SessionEvaluation } from "../api";

export const MAX_SESSION_EVALUATION_POLLS = 40;
export const SESSION_EVALUATION_POLL_INTERVAL_MS = 500;

export function evaluationForFen(
  evaluation: SessionEvaluation | null,
  currentFen: string,
): SessionEvaluation | null {
  return evaluation?.current_fen === currentFen ? evaluation : null;
}

interface WatchSessionEvaluationOptions {
  sessionId: string;
  currentFen: string;
  fetchEvaluation: (sessionId: string) => Promise<SessionEvaluation>;
  isCurrent: () => boolean;
  onResult: (result: SessionEvaluation) => void;
  onError: () => void;
  onStopped: (reason: "pending" | "fen-mismatch") => void;
  schedule?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  cancel?: (timer: ReturnType<typeof setTimeout>) => void;
}

/** Fetch one position's evaluation, polling only while its background job is pending. */
export function watchSessionEvaluation(options: WatchSessionEvaluationOptions): () => void {
  const schedule = options.schedule ?? setTimeout;
  const cancel = options.cancel ?? clearTimeout;
  let active = true;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let polls = 0;

  const poll = async (): Promise<void> => {
    try {
      const result = await options.fetchEvaluation(options.sessionId);
      if (!active || !options.isCurrent()) return;
      if (result.current_fen !== options.currentFen) {
        if (polls < MAX_SESSION_EVALUATION_POLLS) {
          polls += 1;
          timer = schedule(() => void poll(), SESSION_EVALUATION_POLL_INTERVAL_MS);
        } else {
          options.onStopped("fen-mismatch");
        }
        return;
      }

      options.onResult(result);
      if (!result.analysis_pending) return;
      if (polls < MAX_SESSION_EVALUATION_POLLS) {
        polls += 1;
        timer = schedule(() => void poll(), SESSION_EVALUATION_POLL_INTERVAL_MS);
      } else {
        options.onStopped("pending");
      }
    } catch {
      if (active && options.isCurrent()) options.onError();
    }
  };

  void poll();
  return () => {
    active = false;
    if (timer !== null) cancel(timer);
  };
}
