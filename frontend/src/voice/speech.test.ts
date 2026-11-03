import { describe, expect, it, vi } from "vitest";
import { createRecognizerWithCtor, SpeechQueue } from "./speech";
import { SessionGeneration } from "./sessionGeneration";

type ResultAlt = { transcript: string; confidence: number };
type Result = { isFinal: boolean; length: number; [index: number]: ResultAlt };

class FakeRecognition {
  static instances: FakeRecognition[] = [];
  lang = "";
  interimResults = false;
  continuous = false;
  maxAlternatives = 0;
  onstart: (() => void) | null = null;
  onresult: ((event: { resultIndex: number; results: { length: number; [index: number]: Result } }) => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  stopCalls = 0;
  abortCalls = 0;

  constructor() { FakeRecognition.instances.push(this); }
  start() { this.onstart?.(); }
  stop() { this.stopCalls++; }
  abort() { this.abortCalls++; }

  final(index: number, ...transcripts: string[]) {
    const result = { isFinal: true, length: transcripts.length } as Result;
    transcripts.forEach((transcript, i) => { result[i] = { transcript, confidence: 0.9 - i / 10 }; });
    this.onresult?.({ resultIndex: index, results: { length: index + 1, [index]: result } });
  }

  finalBatch(...transcripts: string[]) {
    const results = transcripts.map((transcript) => ({
      isFinal: true,
      length: 1,
      0: { transcript, confidence: 0.9 },
    })) as Result[];
    const indexed: { length: number; [index: number]: Result } = { length: results.length };
    results.forEach((result, index) => { indexed[index] = result; });
    this.onresult?.({ resultIndex: 0, results: indexed });
  }

  end() { this.onend?.(); }
  error(error: string) { this.onerror?.({ error }); }
}

function harness(onFinal?: (recognizer: ReturnType<typeof createRecognizerWithCtor>, alternatives: string[]) => void) {
  FakeRecognition.instances = [];
  const finals: Array<{ alternatives: string[]; key: string }> = [];
  const errors: string[] = [];
  const states: boolean[] = [];
  const recognizer = createRecognizerWithCtor(FakeRecognition, {
    onInterim: () => {},
    onFinal: (alternatives, key) => {
      finals.push({ alternatives, key });
      onFinal?.(recognizer, alternatives);
    },
    onState: (state) => states.push(state),
    onError: (_message, code) => errors.push(code),
  })!;
  return { recognizer, finals, errors, states };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 1));

describe("recognition lifecycle", () => {
  it("consumes one normal-mode final per run and stops immediately", async () => {
    const h = harness();
    void h.recognizer.setDesired(true, false);
    await tick();
    const run = FakeRecognition.instances[0];
    run.final(0, "e4");
    run.final(0, "e4");
    expect(run.continuous).toBe(false);
    expect(run.stopCalls).toBe(1);
    expect(h.finals.map((r) => r.alternatives[0])).toEqual(["e4"]);
    run.end();
    expect(FakeRecognition.instances).toHaveLength(1);
  });

  it("accepts distinct continuous quiet results while deduplicating repeated result events", async () => {
    const h = harness();
    void h.recognizer.setDesired(true, true);
    await tick();
    const run = FakeRecognition.instances[0];
    expect(run.continuous).toBe(true);
    run.final(0, "e4");
    run.final(0, "e4");
    run.final(1, "d4");
    expect(h.finals.map((r) => r.alternatives[0])).toEqual(["e4", "d4"]);
    expect(run.stopCalls).toBe(0);
  });

  it("stops processing remaining results when the first final closes the listening gate", async () => {
    const h = harness((recognizer) => { void recognizer?.setDesired(false, true); });
    void h.recognizer!.setDesired(true, true);
    await tick();
    FakeRecognition.instances[0].finalBatch("e4", "d4", "Nf3");
    expect(h.finals.map((result) => result.alternatives[0])).toEqual(["e4"]);
    expect(FakeRecognition.instances[0].abortCalls).toBe(1);
  });

  it("does not restart after an intentional stop or let an old run affect its replacement", async () => {
    const h = harness();
    void h.recognizer.setDesired(true, false);
    await tick();
    const oldRun = FakeRecognition.instances[0];
    const stopped = h.recognizer.setDesired(true, true);
    expect(oldRun.abortCalls).toBe(1);
    oldRun.error("aborted");
    oldRun.end();
    await stopped;
    await tick();
    const current = FakeRecognition.instances[1];
    oldRun.final(1, "stale");
    expect(h.finals).toHaveLength(0);
    expect(current.continuous).toBe(true);

    const stoppingCurrent = h.recognizer.stop();
    current.error("aborted");
    current.end();
    await stoppingCurrent;
    await tick();
    expect(FakeRecognition.instances).toHaveLength(2);
  });

  it("suppresses error, result, and end effects from an intentionally stopped run", async () => {
    const h = harness();
    void h.recognizer.setDesired(true, true);
    await tick();
    const run = FakeRecognition.instances[0];
    const stopped = h.recognizer.stop();
    run.error("network");
    run.final(0, "e4");
    run.end();
    await stopped;
    expect(h.errors).toEqual([]);
    expect(h.finals).toEqual([]);
    expect(h.states).toEqual([true, false]);
    await tick();
    expect(FakeRecognition.instances).toHaveLength(1);
  });

  it("suppresses error, result, and end effects after the owning session is invalidated", async () => {
    FakeRecognition.instances = [];
    const sessions = new SessionGeneration();
    const generation = sessions.activate();
    const errors: string[] = [];
    const finals: string[] = [];
    const states: boolean[] = [];
    const recognizer = createRecognizerWithCtor(FakeRecognition, {
      isSessionActive: () => sessions.isCurrent(generation),
      onInterim: () => {},
      onFinal: (alts) => finals.push(alts[0]),
      onState: (state) => states.push(state),
      onError: (_message, code) => errors.push(code),
    })!;
    void recognizer.setDesired(true, true);
    await tick();
    const run = FakeRecognition.instances[0];
    sessions.invalidate(generation);
    run.error("network");
    run.final(0, "e4");
    run.end();
    await recognizer.stop();
    expect(errors).toEqual([]);
    expect(finals).toEqual([]);
    expect(states).toEqual([true]);
    await tick();
    expect(FakeRecognition.instances).toHaveLength(1);
  });

  it("blocks after permission denial and only resumes after explicit retry", async () => {
    const h = harness();
    void h.recognizer.setDesired(true, true);
    await tick();
    FakeRecognition.instances[0].error("not-allowed");
    FakeRecognition.instances[0].end();
    expect(h.errors).toContain("not-allowed");
    await tick();
    expect(FakeRecognition.instances).toHaveLength(1);
    h.recognizer.retry();
    await tick();
    expect(FakeRecognition.instances).toHaveLength(2);
  });

  it("does not let retry restart recognition after listening policy turns off", async () => {
    const h = harness();
    void h.recognizer!.setDesired(true, true);
    await tick();
    const run = FakeRecognition.instances[0];
    run.error("not-allowed");
    run.end();
    await h.recognizer!.setDesired(false, true);
    h.recognizer!.retry();
    await tick();
    expect(FakeRecognition.instances).toHaveLength(1);
  });

  it("does not let retry bypass a closed speech gate", async () => {
    const h = harness();
    void h.recognizer!.setDesired(true, true);
    await tick();
    const run = FakeRecognition.instances[0];
    run.error("not-allowed");
    run.end();
    await h.recognizer!.setDesired(false, true); // the shared TTS gate closes
    h.recognizer!.retry();
    await tick();
    expect(FakeRecognition.instances).toHaveLength(1);
  });

  it("retries recoverable service interruptions with backoff while still desired", async () => {
    vi.useFakeTimers();
    try {
      const h = harness();
      void h.recognizer.setDesired(true, true);
      await vi.advanceTimersByTimeAsync(0);
      const first = FakeRecognition.instances[0];
      first.error("network");
      first.end();
      await vi.advanceTimersByTimeAsync(249);
      expect(FakeRecognition.instances).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(FakeRecognition.instances).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports one start failure once and emits only one state-off callback", async () => {
    class DeniedRecognition extends FakeRecognition {
      start() { throw Object.assign(new Error("denied"), { name: "NotAllowedError" }); }
    }
    FakeRecognition.instances = [];
    const errors: string[] = [];
    const states: boolean[] = [];
    const recognizer = createRecognizerWithCtor(DeniedRecognition, {
      onInterim: () => {},
      onFinal: () => {},
      onState: (state) => states.push(state),
      onError: (_message, code) => errors.push(code),
    })!;
    void recognizer.setDesired(true, true);
    await tick();
    expect(errors).toEqual(["NotAllowedError"]);
    expect(states).toEqual([false]);
    expect(FakeRecognition.instances).toHaveLength(1);
  });

  it("reports one current-run browser error only once", async () => {
    const h = harness();
    void h.recognizer.setDesired(true, true);
    await tick();
    const run = FakeRecognition.instances[0];
    run.error("network");
    expect(h.errors).toEqual(["network"]);
    run.end();
    await h.recognizer.stop();
    expect(h.errors).toEqual(["network"]);
    expect(h.states).toEqual([true, false]);
  });
});

describe("central speech queue", () => {
  it("keeps the microphone gate closed until every queued utterance completes", async () => {
    const events: string[] = [];
    let finishFirst!: () => void;
    const queue = new SpeechQueue(
      async (text) => {
        events.push(`start:${text}`);
        if (text === "first") await new Promise<void>((resolve) => { finishFirst = resolve; });
        events.push(`end:${text}`);
      },
      () => events.push("cancel"),
      (count) => events.push(`pending:${count}`),
      async (blocked) => { events.push(`gate:${blocked}`); },
    );
    const first = queue.enqueue("first", null, true);
    const second = queue.enqueue("second", null, true);
    await tick();
    expect(events).toContain("gate:true");
    expect(events.filter((event) => event === "gate:true")).toHaveLength(1);
    expect(events).toContain("start:first");
    expect(events).not.toContain("start:second");
    expect(events).not.toContain("gate:false");
    finishFirst();
    await Promise.all([first, second]);
    expect(events.indexOf("end:second")).toBeLessThan(events.indexOf("gate:false"));
    expect(events[events.length - 1]).toBe("gate:false");
  });

  it("settles speech errors and ignores queued utterances cancelled before playback", async () => {
    const events: string[] = [];
    let failFirst!: (error: Error) => void;
    const queue = new SpeechQueue(
      async (text) => {
        events.push(text);
        if (text === "error") await new Promise<void>((_resolve, reject) => { failFirst = reject; });
      },
      () => events.push("cancel"),
      () => {},
      () => {},
    );
    const first = queue.enqueue("error", null, true);
    const cancelled = queue.enqueue("cancelled", null, true);
    await tick();
    queue.cancelAll();
    failFirst(new Error("speech error"));
    await Promise.all([first, cancelled]);
    expect(events).toEqual(["error", "cancel"]);
  });

  it("settles all queued promises when cancellation interrupts a pending microphone shutdown", async () => {
    const events: string[] = [];
    let pendingCount = 0;
    let sessionActive = true;
    const queue = new SpeechQueue(
      async (text) => { events.push(`speak:${text}`); },
      () => events.push("cancel"),
      (count) => { pendingCount = count; },
      (blocked) => {
        if (!sessionActive) return;
        events.push(`gate:${blocked}`);
        if (blocked) return new Promise<void>(() => {}); // recognizer onend never arrives
      },
    );
    const first = queue.enqueue("first", null, true);
    const second = queue.enqueue("second", null, true);
    await tick();
    expect(pendingCount).toBe(2);
    expect(events).toContain("gate:true");
    expect(events.some((event) => event.startsWith("speak:"))).toBe(false);

    queue.cancelAll();
    sessionActive = false;
    await Promise.all([first, second]);
    expect(pendingCount).toBe(0);
    expect(events).not.toContain("gate:false");
    expect(events.some((event) => event.startsWith("speak:"))).toBe(false);
  });

  it("keeps generations isolated when old shutdown resolves after new speech is queued", async () => {
    const events: string[] = [];
    let pendingCount = 0;
    let activeOwner = "old";
    let resolveOldGate!: () => void;
    let resolveNewGate!: () => void;
    let finishNewFirst!: () => void;
    const queue = new SpeechQueue(
      async (text) => {
        events.push(`speak:${text}`);
        if (text === "new-first") await new Promise<void>((resolve) => { finishNewFirst = resolve; });
      },
      () => {},
      (count) => { pendingCount = count; },
      (blocked) => {
        events.push(`gate:${activeOwner}:${blocked}`);
        if (!blocked) return;
        return new Promise<void>((resolve) => {
          if (activeOwner === "old") resolveOldGate = resolve;
          else resolveNewGate = resolve;
        });
      },
    );

    const oldFirst = queue.enqueue("old-first", null, true);
    const oldSecond = queue.enqueue("old-second", null, true);
    await tick();
    expect(events.filter((event) => event === "gate:old:true")).toHaveLength(1);

    queue.cancelAll();
    activeOwner = "new";
    const newFirst = queue.enqueue("new-first", null, true);
    const newSecond = queue.enqueue("new-second", null, true);
    await Promise.all([oldFirst, oldSecond]);
    expect(pendingCount).toBe(2);
    expect(events).toContain("gate:new:true");

    resolveOldGate();
    await tick();
    expect(events).not.toContain("gate:new:false");
    expect(events.some((event) => event.startsWith("speak:"))).toBe(false);
    expect(pendingCount).toBe(2);

    resolveNewGate();
    await tick();
    expect(events).toContain("speak:new-first");
    expect(events).not.toContain("speak:new-second");
    expect(events).not.toContain("gate:new:false");
    finishNewFirst();
    await Promise.all([newFirst, newSecond]);

    expect(events.indexOf("speak:new-first")).toBeLessThan(events.indexOf("speak:new-second"));
    expect(events.indexOf("speak:new-second")).toBeLessThan(events.indexOf("gate:new:false"));
    expect(pendingCount).toBe(0);
  });

  it("does not gate the microphone when TTS is disabled or the item is empty", async () => {
    const gate = vi.fn();
    const speak = vi.fn(async () => {});
    const queue = new SpeechQueue(speak, () => {}, () => {}, gate);
    await queue.enqueue("not spoken", null, false);
    await queue.enqueue("  ", null, true);
    expect(gate).not.toHaveBeenCalled();
    expect(speak).not.toHaveBeenCalled();
  });
});
