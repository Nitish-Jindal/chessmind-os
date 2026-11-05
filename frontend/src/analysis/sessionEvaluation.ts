import type { SessionEvaluation } from "../api";

export const MAX_SESSION_EVALUATION_POLLS = 10;
export const SESSION_EVALUATION_POLL_INTERVAL_MS = 2000;

export function evaluationForFen(
  evaluation: SessionEvaluation | null,
  currentFen: string,
): SessionEvaluation | null {
  return evaluation?.current_fen === currentFen ? evaluation : null;
}

/** Per-mounted-session bounded cache; never shares user data across logins. */
export class SessionEvaluationCache {
  private values = new Map<string, SessionEvaluation>();
  private pending = new Map<string, Promise<SessionEvaluation>>();

  async fetch(sessionId: string, fen: string, fetcher: (id: string) => Promise<SessionEvaluation>) {
    const key = `${sessionId}:${fen}`;
    const cached = this.values.get(key);
    if (cached) return cached;
    const existing = this.pending.get(key);
    if (existing) return existing;
    const request = fetcher(sessionId).then((result) => {
      if (result.current_fen === fen && result.analysis_available) {
        if (this.values.size >= 64) this.values.delete(this.values.keys().next().value!);
        this.values.set(key, result);
      }
      return result;
    }).finally(() => { this.pending.delete(key); });
    this.pending.set(key, request);
    return request;
  }
}

interface WatchSessionEvaluationOptions {
  cache?: SessionEvaluationCache;
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
  const cache = options.cache ?? new SessionEvaluationCache();
  const schedule = options.schedule ?? setTimeout;
  const cancel = options.cancel ?? clearTimeout;
  let active = true;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let polls = 0;

  const poll = async (): Promise<void> => {
    try {
      const result = await cache.fetch(options.sessionId, options.currentFen, options.fetchEvaluation);
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
