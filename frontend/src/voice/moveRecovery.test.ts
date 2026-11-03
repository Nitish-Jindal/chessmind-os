import { describe, expect, it, vi } from "vitest";
import { shouldListen } from "./turnPolicy";
import { MoveRecovery } from "./moveRecovery";

describe("accepted move synchronization recovery", () => {
  it("keeps the accepted move locked through failed refreshes and recovers without posting twice", async () => {
    const recovery = new MoveRecovery();
    const postMove = vi.fn(async () => ({ played_san: "e4" }));
    const fetchState = vi.fn<() => Promise<{ next_to_move: "player" }>>()
      .mockRejectedValueOnce(new Error("normal refresh failed"))
      .mockRejectedValueOnce(new Error("recovery refresh failed"))
      .mockResolvedValueOnce({ next_to_move: "player" });

    const accepted = await recovery.submitOnce(postMove, fetchState);
    expect(accepted).toEqual({ move: { played_san: "e4" }, state: null });
    await expect(recovery.reconcile(fetchState)).rejects.toThrow("recovery refresh failed");
    expect(recovery.isLocked).toBe(true);
    expect(recovery.needsSynchronization).toBe(true);
    expect(recovery.beginSubmission()).toBe(false);
    expect(shouldListen({
      active: !recovery.needsSynchronization,
      quiet: true,
      playerTurn: true,
      processing: false,
      speechPending: false,
      speechGateClosed: false,
    })).toBe(false);

    const authoritative = await recovery.retrySynchronization(fetchState);
    expect(authoritative.next_to_move).toBe("player");
    expect(postMove).toHaveBeenCalledTimes(1);
    expect(fetchState).toHaveBeenCalledTimes(3);
    expect(recovery.isLocked).toBe(false);
    expect(recovery.needsSynchronization).toBe(false);
    expect(shouldListen({
      active: true,
      quiet: true,
      playerTurn: true,
      processing: false,
      speechPending: false,
      speechGateClosed: false,
    })).toBe(true);
  });

  it("keeps submission locked until authoritative sync and the response sequence finish", async () => {
    const recovery = new MoveRecovery();
    const accepted = await recovery.submitOnce(
      async () => "move accepted",
      async () => ({ next_to_move: "player" }),
    );
    expect(accepted?.state?.next_to_move).toBe("player");
    expect(recovery.needsSynchronization).toBe(false);
    expect(recovery.isLocked).toBe(true);
    recovery.finishSequence();
    expect(recovery.isLocked).toBe(false);
  });
});
