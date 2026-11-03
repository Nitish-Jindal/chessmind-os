/** Holds a submitted move until its authoritative session state has been fetched. */
export class MoveRecovery {
  private locked = false;
  private accepted = false;
  private syncing = false;
  private syncPending = false;

  get isLocked(): boolean {
    return this.locked;
  }

  get needsSynchronization(): boolean {
    return this.syncPending;
  }

  beginSubmission(): boolean {
    if (this.locked || this.syncPending || this.syncing) return false;
    this.locked = true;
    return true;
  }

  async submitOnce<TMove, TState>(
    submit: () => Promise<TMove>,
    fetchState: () => Promise<TState>,
  ): Promise<{ move: TMove; state: TState | null } | null> {
    if (!this.beginSubmission()) return null;
    let move: TMove;
    try {
      move = await submit();
    } catch (error) {
      this.locked = false;
      throw error;
    }

    this.accepted = true;
    this.syncPending = true;
    try {
      const state = await this.fetchAuthoritative(fetchState);
      return { move, state };
    } catch {
      // The move is accepted. Keep it and its lock; a later operation only fetches state.
      return { move, state: null };
    }
  }

  async reconcile<TState>(fetchState: () => Promise<TState>): Promise<TState> {
    if (!this.accepted || this.syncing) throw new Error("No accepted move is ready for synchronization.");
    return this.fetchAuthoritative(fetchState);
  }

  async retrySynchronization<TState>(fetchState: () => Promise<TState>): Promise<TState> {
    if (!this.accepted || !this.syncPending || this.syncing) {
      throw new Error("There is no pending move synchronization to retry.");
    }
    const state = await this.fetchAuthoritative(fetchState);
    this.accepted = false;
    this.locked = false;
    return state;
  }

  /** Release after the full response sequence once authoritative state has been fetched. */
  finishSequence(): void {
    if (this.syncPending) return;
    this.accepted = false;
    this.locked = false;
  }

  private async fetchAuthoritative<TState>(fetchState: () => Promise<TState>): Promise<TState> {
    if (this.syncing) throw new Error("Session synchronization is already in progress.");
    this.syncing = true;
    try {
      const state = await fetchState();
      this.syncPending = false;
      return state;
    } catch (error) {
      this.syncPending = true;
      throw error;
    } finally {
      this.syncing = false;
    }
  }
}
