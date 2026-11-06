import { describe, expect, it, vi } from "vitest";
import type { SessionEvaluation } from "../api";
import { evaluationForFen, watchSessionEvaluation, SessionEvaluationCache } from "./sessionEvaluation";

import recorded from "./fixtures/session-evaluations.json";

const INITIAL_FEN = recorded[0].current_fen;
const NEXT_FEN = recorded[1].current_fen;

function result(currentFen: string, analysisPending = false): SessionEvaluation {
  const real = recorded.find((value) => value.current_fen === currentFen)!;
  return {
    ...real,
    perspective: real.perspective as "white",
    analysis_available: !analysisPending,
    analysis_pending: analysisPending,
    eval_cp_white: analysisPending ? null : real.eval_cp_white,
    depth: analysisPending ? null : real.depth,
    nodes: analysisPending ? null : real.nodes,
    engine_version: analysisPending ? null : real.engine_version,
  };
}

async function flushMicrotasks() {
  for (let turn = 0; turn < 12; turn += 1) await Promise.resolve();
}

describe("session evaluation fetching", () => {
  it("clears a prior evaluation for a new FEN and rejects the stale response", async () => {
    let resolveOld!: (value: SessionEvaluation) => void;
    const oldRequest = new Promise<SessionEvaluation>((resolve) => { resolveOld = resolve; });
    const visible: SessionEvaluation[] = [];
    let currentFen = INITIAL_FEN;
    const stopOld = watchSessionEvaluation({
      sessionId: "session",
      currentFen,
      fetchEvaluation: () => oldRequest,
      isCurrent: () => currentFen === INITIAL_FEN,
      onResult: (value) => visible.push(value),
      onError: () => { throw new Error("unexpected error"); },
      onStopped: () => { throw new Error("unexpected stop"); },
    });

    currentFen = NEXT_FEN;
    stopOld();
    expect(evaluationForFen(result(INITIAL_FEN), currentFen)).toBeNull();

    const stopNew = watchSessionEvaluation({
      sessionId: "session",
      currentFen,
      fetchEvaluation: async () => result(NEXT_FEN),
      isCurrent: () => currentFen === NEXT_FEN,
      onResult: (value) => visible.push(value),
      onError: () => { throw new Error("unexpected error"); },
      onStopped: () => { throw new Error("unexpected stop"); },
    });
    resolveOld(result(INITIAL_FEN));
    await flushMicrotasks();

    expect(visible.map((value) => value.current_fen)).toEqual([NEXT_FEN]);
    expect(evaluationForFen(visible[0], currentFen)?.eval_cp_white).toBe(recorded[1].eval_cp_white);
    stopNew();
  });

  it("polls a pending analysis and publishes the completed evaluation", async () => {
    const scheduled: Array<() => void> = [];
    const values = [result(INITIAL_FEN, true), result(INITIAL_FEN, false)];
    const updates: SessionEvaluation[] = [];
    const stopped = vi.fn();
    const cleanup = watchSessionEvaluation({
      sessionId: "session",
      currentFen: INITIAL_FEN,
      fetchEvaluation: async () => values.shift()!,
      isCurrent: () => true,
      onResult: (value) => updates.push(value),
      onError: () => { throw new Error("unexpected error"); },
      onStopped: stopped,
      schedule: (callback) => {
        scheduled.push(callback);
        return scheduled.length as ReturnType<typeof setTimeout>;
      },
      cancel: vi.fn(),
    });
    await flushMicrotasks();

    expect(updates[0].analysis_pending).toBe(true);
    expect(scheduled).toHaveLength(1);
    scheduled.shift()!();
    await flushMicrotasks();

    expect(updates[1].analysis_available).toBe(true);
    expect(updates[1].eval_cp_white).toBe(recorded[0].eval_cp_white);
    expect(stopped).not.toHaveBeenCalled();
    cleanup();
  });

  it("rejects a response whose server FEN does not match the requested FEN", async () => {
    const updates: SessionEvaluation[] = [];
    const scheduled: Array<() => void> = [];
    const cleanup = watchSessionEvaluation({
      sessionId: "session",
      currentFen: NEXT_FEN,
      fetchEvaluation: async () => result(INITIAL_FEN),
      isCurrent: () => true,
      onResult: (value) => updates.push(value),
      onError: () => { throw new Error("unexpected error"); },
      onStopped: () => { throw new Error("unexpected stop"); },
      schedule: (callback) => {
        scheduled.push(callback);
        return scheduled.length as ReturnType<typeof setTimeout>;
      },
      cancel: vi.fn(),
    });
    await flushMicrotasks();
    expect(updates).toEqual([]);
    expect(scheduled).toHaveLength(1);
    cleanup();
  });
});


describe("position evaluation cache", () => {
  it("deduplicates pending reads, reuses results and fetches changed positions", async () => {
    const cache = new SessionEvaluationCache();
    let release!: (value: SessionEvaluation) => void;
    const fetcher = vi.fn(() => new Promise<SessionEvaluation>((resolve) => { release = resolve; }));
    const first = cache.fetch("session", INITIAL_FEN, fetcher);
    const duplicate = cache.fetch("session", INITIAL_FEN, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
    release(result(INITIAL_FEN));
    expect(await duplicate).toEqual(await first);
    await cache.fetch("session", INITIAL_FEN, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const changed = cache.fetch("session", NEXT_FEN, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
    release(result(NEXT_FEN));
    expect((await changed).current_fen).toBe(NEXT_FEN);
  });

  it("does not cache pending or mismatched responses", async () => {
    const cache = new SessionEvaluationCache();
    const fetcher = vi.fn(async () => result(INITIAL_FEN, true));
    await cache.fetch("session", INITIAL_FEN, fetcher);
    await cache.fetch("session", INITIAL_FEN, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
    const mismatch = vi.fn(async () => result(NEXT_FEN));
    await cache.fetch("session", INITIAL_FEN, mismatch);
    await cache.fetch("session", INITIAL_FEN, mismatch);
    expect(mismatch).toHaveBeenCalledTimes(2);
  });
});
