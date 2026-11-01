import type { Recognizer, RecognizerHandlers } from "./speech";

export interface MicrophonePolicy {
  enabled: boolean;
  quiet: boolean;
  anySpeech: boolean;
  opponentSpeech: boolean;
}

export type MicrophoneStatus = "off" | "listening" | "unavailable" | "denied";
export type RecognizerFactory = (handlers: RecognizerHandlers) => Recognizer | null;
const INITIAL_RETRY_DELAY_MS = 250;
const MAX_RETRY_DELAY_MS = 5000;
const STABLE_RUN_MS = 15000;

/** Owns recognition restarts, speech gating, and the normal/quiet turn policy for one session. */
export class AutomaticMicrophone {
  private policy: MicrophonePolicy = { enabled: false, quiet: false, anySpeech: false, opponentSpeech: false };
  private recognizer: Recognizer | null = null;
  private generation = 0;
  private suspended = false;
  private quietTransitionSpeechLock = false;
  private denied = false;
  private disposed = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private retryDelay = INITIAL_RETRY_DELAY_MS;
  private stableRunTimer: ReturnType<typeof setTimeout> | null = null;
  private currentStatus: MicrophoneStatus = "off";

  constructor(
    private readonly create: RecognizerFactory,
    private readonly onUtterance: (alternatives: string[]) => void | Promise<void>,
    private readonly onStatus: (status: MicrophoneStatus) => void,
    private readonly onError: (message: string, denied: boolean) => void,
    private readonly onInterim: (text: string) => void = () => {},
  ) {}

  update(policy: MicrophonePolicy): void {
    const modeChanged = policy.quiet !== this.policy.quiet;
    if (modeChanged && policy.quiet && policy.anySpeech) this.quietTransitionSpeechLock = true;
    if (!policy.quiet || !policy.anySpeech) this.quietTransitionSpeechLock = false;
    this.policy = policy;
    if (policy.quiet) this.suspended = false;
    if (modeChanged && this.recognizer) this.stopRun();
    this.reconcile();
  }

  /** Normal mode resumes only after the complete command/response promise has finished. */
  resume(): void {
    this.suspended = false;
    this.reconcile();
  }

  pause(force = false): void {
    if (this.policy.quiet && !force) return;
    this.suspended = true;
    this.reconcile();
  }

  dispose(): void {
    this.disposed = true;
    this.generation++;
    this.clearRetry();
    this.clearStableRunTimer();
    this.stopRun();
  }

  private get shouldListen(): boolean {
    if (this.disposed || this.denied || !this.policy.enabled) return false;
    if (this.policy.quiet) return !this.policy.opponentSpeech && !this.quietTransitionSpeechLock;
    return !this.suspended && !this.policy.anySpeech;
  }

  private reconcile(): void {
    if (!this.shouldListen) {
      this.clearRetry();
      this.stopRun();
      if (!this.denied) this.setStatus("off");
      return;
    }
    if (this.recognizer || this.retryTimer) return;
    this.startRun();
  }

  private startRun(): void {
    const generation = ++this.generation;
    let finalInRun = false;
    const recognizer = this.create({
      onInterim: this.onInterim,
      onFinal: (alternatives) => {
        if (generation !== this.generation || !this.shouldListen) return;
        // A final result proves the browser recognition service is working again.
        this.retryDelay = INITIAL_RETRY_DELAY_MS;
        this.clearStableRunTimer();
        if (!this.policy.quiet) {
          if (finalInRun) return;
          finalInRun = true;
          this.suspended = true;
          this.stopRun();
        }
        void Promise.resolve(this.onUtterance(alternatives)).catch(() => {}).finally(() => {
          if (!this.disposed) this.resume();
        });
      },
      onState: (listening) => {
        if (generation !== this.generation) return;
        if (listening) {
          this.watchForStableRun(generation);
          this.setStatus("listening");
        } else {
          this.clearStableRunTimer();
          this.recognizer = null;
          // Invalidate every callback from the ended run before scheduling its replacement.
          this.generation++;
          if (!this.denied) this.setStatus("off");
          this.scheduleRetry();
        }
      },
      onError: (message, denied) => {
        if (generation !== this.generation) return;
        if (denied) {
          this.denied = true;
          this.onError(message, true);
          this.clearRetry();
          this.stopRun();
          this.setStatus("denied");
        } else if (message) {
          this.onError(message, false);
          this.stopRun();
          this.scheduleRetry();
        }
      },
    });

    if (!recognizer) {
      this.setStatus("unavailable");
      return;
    }
    this.recognizer = recognizer;
    try {
      recognizer.start(this.policy.quiet);
    } catch (error) {
      this.recognizer = null;
      this.generation++;
      this.clearStableRunTimer();
      this.setStatus("off");
      this.onError(`Speech recognition could not start: ${String(error)}`, false);
      this.scheduleRetry();
    }
  }

  private stopRun(): void {
    if (!this.recognizer) return;
    const current = this.recognizer;
    this.recognizer = null;
    this.generation++;
    this.clearStableRunTimer();
    this.setStatus("off");
    try {
      current.stop();
    } catch {
      // A browser can end recognition between the policy check and stop().
    }
  }

  private scheduleRetry(): void {
    if (!this.shouldListen || this.retryTimer) return;
    const wait = this.retryDelay;
    this.retryDelay = Math.min(this.retryDelay * 2, MAX_RETRY_DELAY_MS);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.reconcile();
    }, wait);
  }

  private clearRetry(): void {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }

  private watchForStableRun(generation: number): void {
    this.clearStableRunTimer();
    this.stableRunTimer = setTimeout(() => {
      this.stableRunTimer = null;
      if (generation === this.generation && this.recognizer) this.retryDelay = INITIAL_RETRY_DELAY_MS;
    }, STABLE_RUN_MS);
  }

  private clearStableRunTimer(): void {
    if (this.stableRunTimer) clearTimeout(this.stableRunTimer);
    this.stableRunTimer = null;
  }

  private setStatus(status: MicrophoneStatus): void {
    if (status === this.currentStatus) return;
    this.currentStatus = status;
    this.onStatus(status);
  }
}
