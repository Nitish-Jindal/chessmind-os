import { describe, expect, it } from "vitest";
import { shouldListen, TurnSubmissionLock } from "./turnPolicy";

const base = {
  active: true,
  quiet: false,
  playerTurn: true,
  processing: false,
  speechPending: false,
  speechGateClosed: false,
};

describe("automatic listening policy", () => {
  it("listens on the player's normal-mode turn only", () => {
    expect(shouldListen(base)).toBe(true);
    expect(shouldListen({ ...base, playerTurn: false })).toBe(false);
    expect(shouldListen({ ...base, processing: true })).toBe(false);
  });

  it("keeps quiet mode ready while the backend processes", () => {
    expect(shouldListen({ ...base, quiet: true, processing: true, playerTurn: false })).toBe(true);
  });

  it("blocks both modes for queued speech, finished sessions, and the explicit speech gate", () => {
    expect(shouldListen({ ...base, quiet: true, speechPending: true })).toBe(false);
    expect(shouldListen({ ...base, speechGateClosed: true })).toBe(false);
    expect(shouldListen({ ...base, active: false })).toBe(false);
  });
});

describe("TurnSubmissionLock", () => {
  it("claims synchronously once and unlocks only when released", () => {
    const lock = new TurnSubmissionLock();
    expect(lock.claim()).toBe(true);
    expect(lock.claim()).toBe(false);
    expect(lock.isLocked).toBe(true);
    lock.release();
    expect(lock.claim()).toBe(true);
  });
});
