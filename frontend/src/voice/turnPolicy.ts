export interface ListeningPolicyInput {
  active: boolean;
  quiet: boolean;
  playerTurn: boolean;
  processing: boolean;
  speechPending: boolean;
  speechGateClosed: boolean;
}

/** Quiet mode stays armed through backend work; all modes yield to any queued application speech. */
export function shouldListen(input: ListeningPolicyInput): boolean {
  if (!input.active || input.speechPending || input.speechGateClosed) return false;
  if (input.quiet) return input.playerTurn || input.processing;
  return input.playerTurn && !input.processing;
}

/** Synchronous claim used before any asynchronous move submission begins. */
export class TurnSubmissionLock {
  private locked = false;

  claim(): boolean {
    if (this.locked) return false;
    this.locked = true;
    return true;
  }

  release(): void {
    this.locked = false;
  }

  get isLocked(): boolean {
    return this.locked;
  }
}
