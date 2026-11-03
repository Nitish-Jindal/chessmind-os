import { describe, expect, it } from "vitest";
import { SessionGeneration } from "./sessionGeneration";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("session generation guards", () => {
  it("ignores a pending request after navigation activates a newer session", async () => {
    const sessions = new SessionGeneration();
    const oldGeneration = sessions.activate();
    const request = deferred<string>();
    const updates: string[] = [];
    const callback = request.promise.then((result) => {
      if (sessions.isCurrent(oldGeneration)) updates.push(result);
    });

    sessions.invalidate(oldGeneration);
    sessions.activate();
    request.resolve("old session response");
    await callback;

    expect(updates).toEqual([]);
  });

  it("ignores a pending request after the session unmounts", async () => {
    const sessions = new SessionGeneration();
    const generation = sessions.activate();
    const request = deferred<string>();
    const updates: string[] = [];
    const callback = request.promise.then((result) => {
      if (sessions.isCurrent(generation)) updates.push(result);
    });

    sessions.invalidate(generation);
    request.resolve("late session response");
    await callback;

    expect(updates).toEqual([]);
    expect(sessions.isCurrent(generation)).toBe(false);
  });
});
