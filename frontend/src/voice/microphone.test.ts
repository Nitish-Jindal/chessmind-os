import { afterEach, describe, expect, it, vi } from "vitest";
import { AutomaticMicrophone } from "./microphone";
import type { Recognizer, RecognizerHandlers } from "./speech";

class TestRecognizer implements Recognizer {
  continuous = false;
  listening = false;
  starts = 0;
  stops = 0;

  constructor(readonly handlers: RecognizerHandlers) {}

  start(continuous = false) {
    this.continuous = continuous;
    this.starts++;
    this.listening = true;
    this.handlers.onState(true);
  }

  stop() {
    this.stops++;
    this.listening = false;
  }

  final(...alts: string[]) {
    this.handlers.onFinal(alts);
  }

  end() {
    this.listening = false;
    this.handlers.onState(false);
  }

  error(message: string, denied = false) {
    this.handlers.onError(message, denied);
  }
}

function fixture(onUtterance: (alts: string[]) => void | Promise<void> = () => {}) {
  const runs: TestRecognizer[] = [];
  const statuses: string[] = [];
  const errors: Array<[string, boolean]> = [];
  const mic = new AutomaticMicrophone(
    (handlers) => {
      const run = new TestRecognizer(handlers);
      runs.push(run);
      return run;
    },
    onUtterance,
    (status) => statuses.push(status),
    (message, denied) => errors.push([message, denied]),
  );
  return { mic, runs, statuses, errors };
}

afterEach(() => vi.useRealTimers());

describe("AutomaticMicrophone", () => {
  it("starts automatically for the player's turn, including both sides and board modes", () => {
    const { mic, runs } = fixture();
    mic.update({ enabled: true, quiet: false, anySpeech: false, opponentSpeech: false });
    expect(runs).toHaveLength(1);
    expect(runs[0].continuous).toBe(false);

    mic.update({ enabled: true, quiet: true, anySpeech: true, opponentSpeech: false });
    expect(runs[0].stops).toBe(1);
    expect(runs).toHaveLength(1);
    mic.update({ enabled: true, quiet: true, anySpeech: false, opponentSpeech: false });
    expect(runs).toHaveLength(2);
    expect(runs[1].continuous).toBe(true);
    mic.dispose();
  });

  it("stops immediately on a normal-mode result and resumes only after the full response promise", async () => {
    let finishResponse!: () => void;
    const response = new Promise<void>((resolve) => { finishResponse = resolve; });
    const { mic, runs } = fixture(() => response);
    mic.update({ enabled: true, quiet: false, anySpeech: false, opponentSpeech: false });
    runs[0].final("knight f3");
    expect(runs[0].stops).toBe(1);
    expect(runs).toHaveLength(1);
    finishResponse();
    await Promise.resolve();
    await Promise.resolve();
    expect(runs).toHaveLength(2);
    mic.dispose();
  });

  it("stays off during normal engine and TTS work, then reactivates when speech is done", () => {
    const { mic, runs } = fixture();
    mic.update({ enabled: true, quiet: false, anySpeech: false, opponentSpeech: false });
    expect(runs).toHaveLength(1);
    mic.update({ enabled: false, quiet: false, anySpeech: false, opponentSpeech: false });
    expect(runs[0].stops).toBe(1);
    expect(runs).toHaveLength(1);
    mic.update({ enabled: false, quiet: false, anySpeech: true, opponentSpeech: true });
    expect(runs).toHaveLength(1);
    mic.update({ enabled: true, quiet: false, anySpeech: false, opponentSpeech: false });
    mic.resume();
    expect(runs).toHaveLength(2);
    mic.update({ enabled: true, quiet: false, anySpeech: true, opponentSpeech: true });
    expect(runs[1].stops).toBe(1);
    expect(runs).toHaveLength(2);
    mic.update({ enabled: true, quiet: false, anySpeech: false, opponentSpeech: false });
    expect(runs).toHaveLength(3);
    mic.dispose();
  });

  it("keeps quiet-mode listening through processing, gates opponent TTS, then resumes", () => {
    const { mic, runs } = fixture();
    mic.update({ enabled: true, quiet: true, anySpeech: false, opponentSpeech: false });
    runs[0].final("e4");
    expect(runs[0].stops).toBe(0);
    expect(runs).toHaveLength(1);

    mic.update({ enabled: true, quiet: true, anySpeech: true, opponentSpeech: true });
    expect(runs[0].stops).toBe(1);
    mic.update({ enabled: true, quiet: true, anySpeech: false, opponentSpeech: false });
    expect(runs).toHaveLength(2);
    mic.dispose();
  });

  it("retries service restarts and permanently reports permission denial", () => {
    vi.useFakeTimers();
    const { mic, runs, errors } = fixture();
    mic.update({ enabled: true, quiet: true, anySpeech: false, opponentSpeech: false });
    runs[0].error("Speech recognition error: network");
    runs[0].end();
    vi.advanceTimersByTime(250);
    expect(runs).toHaveLength(2);

    runs[1].error("Speech recognition error: not-allowed", true);
    expect(errors.at(-1)?.[1]).toBe(true);
    expect(runs).toHaveLength(2);
    mic.dispose();
  });

  it("increases retry delays on repeated failures up to the five-second cap", () => {
    vi.useFakeTimers();
    const { mic, runs } = fixture();
    mic.update({ enabled: true, quiet: true, anySpeech: false, opponentSpeech: false });

    for (const delay of [250, 500, 1000, 2000, 4000, 5000, 5000]) {
      const failedRun = runs.at(-1)!;
      const countBeforeRetry = runs.length;
      failedRun.error("Speech recognition error: network");
      vi.advanceTimersByTime(delay - 1);
      expect(runs).toHaveLength(countBeforeRetry);
      vi.advanceTimersByTime(1);
      expect(runs).toHaveLength(countBeforeRetry + 1);
    }

    // A successful transcript restores the initial retry interval.
    runs.at(-1)!.final("e4");
    runs.at(-1)!.end();
    const countBeforeSuccessRetry = runs.length;
    vi.advanceTimersByTime(249);
    expect(runs).toHaveLength(countBeforeSuccessRetry);
    vi.advanceTimersByTime(1);
    expect(runs).toHaveLength(countBeforeSuccessRetry + 1);
    mic.dispose();
  });

  it("rejects callbacks from an ended recognizer after its replacement starts", () => {
    vi.useFakeTimers();
    const received: string[][] = [];
    const { mic, runs } = fixture((alts) => { received.push(alts); });
    mic.update({ enabled: true, quiet: true, anySpeech: false, opponentSpeech: false });
    const endedRun = runs[0];
    endedRun.end();
    vi.advanceTimersByTime(250);
    expect(runs).toHaveLength(2);

    endedRun.final("e4");
    expect(received).toEqual([]);
    runs[1].final("d4");
    expect(received).toEqual([["d4"]]);
    mic.dispose();
  });

  it("ignores duplicate and stale results from a previous run", () => {
    const received: string[][] = [];
    const { mic, runs } = fixture((alts) => { received.push(alts); });
    mic.update({ enabled: true, quiet: false, anySpeech: false, opponentSpeech: false });
    const previousRun = runs[0];
    previousRun.final("e4");
    previousRun.final("e4");
    expect(received).toEqual([["e4"]]);
    mic.update({ enabled: true, quiet: false, anySpeech: false, opponentSpeech: true });
    previousRun.final("d4");
    expect(received).toEqual([["e4"]]);
    mic.dispose();
  });
});
