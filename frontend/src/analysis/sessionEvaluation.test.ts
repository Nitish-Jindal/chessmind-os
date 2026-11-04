import { describe, expect, it, vi } from "vitest";
import type { SessionEvaluation } from "../api";
import { evaluationForFen, watchSessionEvaluation } from "./sessionEvaluation";

function result(currentFen: string, analysisPending = false): SessionEvaluation {
  return {
    session_id: "session",
    current_fen: currentFen,
    analysis_available: !analysisPending,
    analysis_pending: analysisPending,
    eval_cp_white: analysisPending ? null : 125,
    mate_white: null,
    depth: analysisPending ? null : 10,
    nodes: analysisPending ? null : 1200,
    engine_version: analysisPending ? null : "Stockfish 17.1",
    perspective: analysisPending ? null : "white",
    analyzed_at: null,
    latest_move_classification: null,
  };
}

async function flushMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
}

describe("session evaluation fetching", () => {
  it("clears a prior evaluation for a new FEN and rejects the stale response", async () => {
    let resolveOld!: (value: SessionEvaluation) => void;
    const oldRequest = new Promise<SessionEvaluation>((resolve) => { resolveOld = resolve; });
    const visible: SessionEvaluation[] = [];
    let currentFen = "old-fen";
    const stopOld = watchSessionEvaluation({
      sessionId: "session",
      currentFen,
      fetchEvaluation: () => oldRequest,
      isCurrent: () => currentFen === "old-fen",
      onResult: (value) => visible.push(value),
      onError: () => { throw new Error("unexpected error"); },
      onStopped: () => { throw new Error("unexpected stop"); },
    });

    currentFen = "new-fen";
    stopOld();
    expect(evaluationForFen(result("old-fen"), currentFen)).toBeNull();

    const stopNew = watchSessionEvaluation({
      sessionId: "session",
      currentFen,
      fetchEvaluation: async () => result("new-fen"),
      isCurrent: () => currentFen === "new-fen",
      onResult: (value) => visible.push(value),
      onError: () => { throw new Error("unexpected error"); },
      onStopped: () => { throw new Error("unexpected stop"); },
    });
    resolveOld(result("old-fen"));
    await flushMicrotasks();

    expect(visible.map((value) => value.current_fen)).toEqual(["new-fen"]);
    expect(evaluationForFen(visible[0], currentFen)?.eval_cp_white).toBe(125);
    stopNew();
  });

  it("polls a pending analysis and publishes the completed evaluation", async () => {
    const scheduled: Array<() => void> = [];
    const values = [result("fen", true), result("fen", false)];
    const updates: SessionEvaluation[] = [];
    const stopped = vi.fn();
    const cleanup = watchSessionEvaluation({
      sessionId: "session",
      currentFen: "fen",
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
    expect(updates[1].eval_cp_white).toBe(125);
    expect(stopped).not.toHaveBeenCalled();
    cleanup();
  });

  it("rejects a response whose server FEN does not match the requested FEN", async () => {
    const updates: SessionEvaluation[] = [];
    const scheduled: Array<() => void> = [];
    const cleanup = watchSessionEvaluation({
      sessionId: "session",
      currentFen: "new-fen",
      fetchEvaluation: async () => result("old-fen"),
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
