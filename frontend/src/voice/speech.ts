// Browser Web Speech API wrappers (STT + TTS). Typed locally because lib.dom does not ship
// webkitSpeechRecognition types.

interface SRAlternative { transcript: string; confidence: number }
interface SRResult { isFinal: boolean; length: number; [i: number]: SRAlternative }
interface SREvent { resultIndex: number; results: { length: number; [i: number]: SRResult } }
interface SRInstance {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onresult: ((e: SREvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type SRCtor = new () => SRInstance;

const browserWindow = typeof window === "undefined" ? undefined : window;
const w = browserWindow as unknown as { SpeechRecognition?: SRCtor; webkitSpeechRecognition?: SRCtor } | undefined;
const Ctor: SRCtor | undefined = w?.SpeechRecognition ?? w?.webkitSpeechRecognition;

export const sttSupported = Ctor !== undefined;
export const ttsSupported = browserWindow !== undefined && "speechSynthesis" in browserWindow;

export interface Recognizer {
  /** Start, pause, or switch recognition mode. Quiet mode uses a continuous run. */
  setDesired(listening: boolean, continuous?: boolean): Promise<void>;
  /** Explicit recovery action after a permission or browser-service failure. */
  retry(): void;
  /** Stop and wait until the browser has ended the active recognition instance. */
  stop(): Promise<void>;
}

export interface RecognitionHandlers {
  /** Optional owner lifetime guard; false suppresses callbacks and automatic restarts. */
  isSessionActive?: () => boolean;
  onInterim: (text: string) => void;
  onFinal: (alternatives: string[], resultKey: string) => void;
  onState: (listening: boolean) => void;
  onError: (message: string, code: string) => void;
}

type Run = {
  id: number;
  rec: SRInstance;
  continuous: boolean;
  intentional: boolean;
  replaceAfterEnd: boolean;
  finalSeen: boolean;
  seenFinals: Set<string>;
  ended: boolean;
  resolveEnd: () => void;
  endPromise: Promise<void>;
};

const RETRY_BASE_MS = 250;
const RETRY_MAX_MS = 8_000;

/**
 * Own one browser recognizer at a time. Every callback is tied to its run id, and replacements
 * wait for onend so an intentional stop cannot race a new instance.
 */
export function createRecognizer(handlers: RecognitionHandlers): Recognizer | null {
  return createRecognizerWithCtor(Ctor, handlers);
}

/** Exported for deterministic lifecycle tests with a browser-event fake. */
export function createRecognizerWithCtor(
  RecognizerCtor: SRCtor | undefined,
  handlers: RecognitionHandlers,
): Recognizer | null {
  if (!RecognizerCtor) return null;

  let desired = false;
  let continuous = false;
  let blocked = false;
  let runCounter = 0;
  let recoverableFailures = 0;
  let current: Run | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  const sessionIsActive = () => handlers.isSessionActive?.() ?? true;

  const clearRetry = () => {
    if (retryTimer !== null) clearTimeout(retryTimer);
    retryTimer = null;
  };

  const scheduleStart = (delay: number) => {
    if (!sessionIsActive()) {
      desired = false;
      clearRetry();
      return;
    }
    if (!desired || blocked || current || retryTimer !== null) return;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      startRun();
    }, delay);
  };

  const finishRun = (ended: Run) => {
    if (current !== ended || ended.ended) return;
    ended.ended = true;
    current = null;
    if (sessionIsActive()) handlers.onState(false);
    ended.resolveEnd();
    if (!sessionIsActive()) {
      desired = false;
      clearRetry();
      return;
    }
    if (!desired || blocked) return;
    if (ended.replaceAfterEnd) scheduleStart(0);
    else if (ended.intentional) return;
    else {
      recoverableFailures++;
      scheduleStart(Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** (recoverableFailures - 1)));
    }
  };

  const requestStop = (active: Run, abort: boolean, replace: boolean) => {
    if (active.ended) return active.endPromise;
    active.intentional = true;
    active.replaceAfterEnd = replace;
    try {
      if (abort) active.rec.abort();
      else active.rec.stop();
    } catch {
      // Some implementations throw when stop/abort races their own onend.
      finishRun(active);
    }
    return active.endPromise;
  };

  function startRun(): void {
    if (!sessionIsActive()) {
      desired = false;
      return;
    }
    if (!desired || blocked || current || !RecognizerCtor) return;
    const instance = new RecognizerCtor();
    let resolveEnd!: () => void;
    const endPromise = new Promise<void>((resolve) => { resolveEnd = resolve; });
    const active: Run = {
      id: ++runCounter,
      rec: instance,
      continuous,
      intentional: false,
      replaceAfterEnd: false,
      finalSeen: false,
      seenFinals: new Set(),
      ended: false,
      resolveEnd,
      endPromise,
    };
    current = active;
    instance.lang = "en-US";
    instance.interimResults = true;
    instance.continuous = active.continuous;
    instance.maxAlternatives = 5;
    instance.onstart = () => {
      if (sessionIsActive() && current === active && !active.intentional && desired) {
        handlers.onState(true);
      }
    };
    instance.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        // A prior result in this same browser event may have submitted a move or queued speech,
        // which synchronously changes the desired listening state. Re-check before each result.
        if (!sessionIsActive() || current !== active || active.intentional || !desired) break;
        const result = event.results[i];
        if (!result.isFinal) {
          handlers.onInterim(result[0]?.transcript ?? "");
          continue;
        }
        const alternatives: string[] = [];
        for (let j = 0; j < result.length; j++) alternatives.push(result[j].transcript);
        const signature = alternatives.map((text) => text.trim().toLowerCase()).join("\u0000");
        const resultKey = `${active.id}:${i}:${signature}`;
        if (active.seenFinals.has(resultKey)) continue;
        active.seenFinals.add(resultKey);
        recoverableFailures = 0;

        if (!active.continuous) {
          if (active.finalSeen) continue;
          active.finalSeen = true;
          // Disarm before invoking app code; the next normal-mode run is explicitly requested
          // by the UI policy after processing/speech completes (or after clarification speech).
          desired = false;
          clearRetry();
          void requestStop(active, false, false);
        }
        handlers.onFinal(alternatives, resultKey);
      }
    };
    instance.onerror = (event) => {
      if (!sessionIsActive() || current !== active || active.ended || active.intentional) return;
      if (event.error === "no-speech") return;
      handlers.onError(`Speech recognition error: ${event.error}`, event.error);
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        blocked = true;
        clearRetry();
        void requestStop(active, true, false);
      }
      // Other errors normally end the browser instance. Its onend handler retries with backoff.
    };
    instance.onend = () => finishRun(active);
    try {
      instance.start();
    } catch (error) {
      current = null;
      active.ended = true;
      active.resolveEnd();
      const name = error && typeof error === "object" && "name" in error
        ? String((error as { name: unknown }).name)
        : "start-failed";
      if (sessionIsActive()) {
        handlers.onState(false);
        handlers.onError(`Speech recognition could not start: ${name}`, name);
      } else {
        desired = false;
        return;
      }
      if (name === "NotAllowedError" || name === "SecurityError") {
        blocked = true;
      } else {
        recoverableFailures++;
        scheduleStart(Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** (recoverableFailures - 1)));
      }
    }
  }

  return {
    setDesired(listening, useContinuous = false) {
      desired = listening;
      continuous = useContinuous;
      if (!listening) {
        clearRetry();
        return current ? requestStop(current, true, false) : Promise.resolve();
      }
      if (blocked) return Promise.resolve();
      if (current?.intentional) current.replaceAfterEnd = true;
      if (current && current.continuous !== continuous) return requestStop(current, true, true);
      scheduleStart(0);
      return Promise.resolve();
    },
    retry() {
      blocked = false;
      recoverableFailures = 0;
      clearRetry();
      if (current) void requestStop(current, true, true);
      else if (desired) scheduleStart(0);
    },
    stop() {
      desired = false;
      clearRetry();
      return current ? requestStop(current, true, false) : Promise.resolve();
    },
  };
}

/** Subscribe to the voice list; Chrome populates it asynchronously and fires `voiceschanged`. */
export function listVoices(): SpeechSynthesisVoice[] {
  return ttsSupported ? window.speechSynthesis.getVoices() : [];
}

export function watchVoices(cb: (voices: SpeechSynthesisVoice[]) => void): () => void {
  if (!ttsSupported) return () => {};
  const emit = () => cb(listVoices());
  window.speechSynthesis.addEventListener("voiceschanged", emit);
  emit();
  return () => window.speechSynthesis.removeEventListener("voiceschanged", emit);
}

let activeUtterance: SpeechSynthesisUtterance | null = null;
let settleActive: (() => void) | null = null;

/** Speak one utterance and settle on end, error, or cancellation. The context serializes calls. */
export function speakText(text: string, voiceURI: string | null): Promise<void> {
  if (!ttsSupported || !text.trim()) return Promise.resolve();
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    const v = voiceURI ? listVoices().find((x) => x.voiceURI === voiceURI) : undefined;
    if (v) {
      try {
        u.voice = v;
        u.lang = v.lang;
      } catch {
        // A voice may disappear between listing and speaking; use the browser default.
      }
    }
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (activeUtterance === u) {
        activeUtterance = null;
        settleActive = null;
      }
      resolve();
    };
    activeUtterance = u;
    settleActive = finish;
    u.onend = finish;
    u.onerror = finish;
    try {
      window.speechSynthesis.speak(u);
    } catch {
      finish();
    }
  });
}

export function cancelSpeech(): void {
  if (!ttsSupported) return;
  try {
    window.speechSynthesis.cancel();
  } finally {
    settleActive?.();
  }
}

/** A FIFO speech queue with a synchronous microphone gate and cancellation generation. */
export class SpeechQueue {
  private tail: Promise<void> = Promise.resolve();
  private generation = 0;
  private pending = 0;
  private pendingByGeneration = new Map<number, number>();
  private gateBarriers = new Map<number, Promise<void>>();
  private cancellation: Promise<void>;
  private resolveCancellation!: () => void;

  constructor(
    private readonly speak: (text: string, voiceURI: string | null) => Promise<void>,
    private readonly cancel: () => void,
    private readonly onPending: (count: number) => void,
    private readonly gate: (blocked: boolean) => Promise<void> | void,
  ) {
    this.cancellation = new Promise<void>((resolve) => { this.resolveCancellation = resolve; });
  }

  enqueue(text: string, voiceURI: string | null, enabled: boolean): Promise<void> {
    if (!enabled || !text.trim()) return Promise.resolve();
    const generation = this.generation;
    const pendingForGeneration = (this.pendingByGeneration.get(generation) ?? 0) + 1;
    this.pendingByGeneration.set(generation, pendingForGeneration);
    if (!this.gateBarriers.has(generation)) {
      // Invoke synchronously so recognition is disarmed before any queued utterance can start.
      try {
        this.gateBarriers.set(generation, Promise.resolve(this.gate(true)).then(() => undefined));
      } catch (error) {
        this.gateBarriers.set(generation, Promise.reject(error));
      }
    }
    const gateBarrier = this.gateBarriers.get(generation)!;
    const cancellation = this.cancellation;
    this.pending++;
    this.onPending(this.pending);
    const run = this.tail.then(async () => {
      try {
        const cancelled = await Promise.race([
          gateBarrier.then(() => false),
          cancellation.then(() => true),
        ]);
        if (cancelled || generation !== this.generation) return;
        await this.speak(text, voiceURI);
      } catch {
        // A browser speech error should settle this queue item and allow later turns to proceed.
      }
    });
    this.tail = run.catch(() => undefined);
    return run.finally(() => {
      this.pending = Math.max(0, this.pending - 1);
      const generationPending = (this.pendingByGeneration.get(generation) ?? 1) - 1;
      if (generationPending <= 0) {
        this.pendingByGeneration.delete(generation);
        this.gateBarriers.delete(generation);
      } else {
        this.pendingByGeneration.set(generation, generationPending);
      }
      this.onPending(this.pending);
      // The gate fans out to currently registered session owners. Their generation guards make
      // this a no-op for a disposed session while still releasing a replacement session that
      // registered during cancellation and inherited the pending-speech gate.
      if (this.pending === 0) {
        try {
          void Promise.resolve(this.gate(false)).catch(() => undefined);
        } catch {
          // A gate teardown failure must not reject the utterance promise that just settled.
        }
      }
    });
  }

  cancelAll(): void {
    const resolveCancellation = this.resolveCancellation;
    this.generation++;
    this.cancellation = new Promise<void>((resolve) => { this.resolveCancellation = resolve; });
    resolveCancellation();
    try {
      this.cancel();
    } catch {
      // Cancellation must still release queued promises if the browser API throws.
    }
  }
}
