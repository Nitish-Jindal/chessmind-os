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
  onresult: ((e: SREvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type SRCtor = new () => SRInstance;

const w = window as unknown as { SpeechRecognition?: SRCtor; webkitSpeechRecognition?: SRCtor };
const Ctor: SRCtor | undefined = w.SpeechRecognition ?? w.webkitSpeechRecognition;

export const sttSupported = Ctor !== undefined;
export const ttsSupported = "speechSynthesis" in window;

const pendingSpeech = new Set<() => void>();

export interface Recognizer {
  start(continuous?: boolean): void;
  stop(): void;
}

export interface RecognizerHandlers {
  onInterim: (text: string) => void;
  onFinal: (alternatives: string[]) => void;
  onState: (listening: boolean) => void;
  onError: (message: string, denied: boolean) => void;
}

/** A recognizer run; the session controller starts a fresh run after browser service restarts. */
export function createRecognizer(handlers: RecognizerHandlers): Recognizer | null {
  if (!Ctor) return null;
  let rec: SRInstance | null = null;
  let finalIndices = new Set<number>();
  return {
    start(continuous = false) {
      if (rec) return;
      const r = new Ctor();
      rec = r;
      finalIndices = new Set();
      r.lang = "en-US";
      r.interimResults = true;
      r.continuous = continuous;
      r.maxAlternatives = 5;
      r.onresult = (e) => {
        if (rec !== r) return;
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i];
          if (res.isFinal) {
            if (finalIndices.has(i)) continue;
            finalIndices.add(i);
            const alts: string[] = [];
            for (let j = 0; j < res.length; j++) alts.push(res[j].transcript);
            handlers.onFinal(alts);
          } else handlers.onInterim(res[0].transcript);
        }
      };
      r.onerror = (e) => {
        if (rec !== r || e.error === "no-speech" || e.error === "aborted") return;
        const denied = e.error === "not-allowed" || e.error === "service-not-allowed";
        handlers.onError(`Speech recognition error: ${e.error}`, denied);
      };
      r.onend = () => {
        if (rec !== r) return;
        rec = null;
        handlers.onState(false);
        handlers.onInterim("");
      };
      try {
        r.start();
        handlers.onState(true);
      } catch {
        rec = null;
        handlers.onState(false);
        handlers.onError("Speech recognition could not start. Check microphone access and try again.", false);
      }
    },
    stop() {
      const current = rec;
      rec = null;
      try {
        current?.stop();
      } catch {
        // Recognition may already have ended in the browser.
      }
    },
  };
}

export function listVoices(): SpeechSynthesisVoice[] {
  return ttsSupported ? window.speechSynthesis.getVoices() : [];
}

/** Subscribe to the voice list; Chrome populates it asynchronously and fires `voiceschanged`. */
export function watchVoices(cb: (voices: SpeechSynthesisVoice[]) => void): () => void {
  if (!ttsSupported) return () => {};
  const emit = () => cb(listVoices());
  window.speechSynthesis.addEventListener("voiceschanged", emit);
  emit();
  return () => window.speechSynthesis.removeEventListener("voiceschanged", emit);
}

/** Queue an utterance (does not cancel what is already being said). Resolves when finished. */
export function speakText(text: string, voiceURI: string | null): Promise<void> {
  if (!ttsSupported || !text.trim()) return Promise.resolve();
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      pendingSpeech.delete(finish);
      resolve();
    };
    pendingSpeech.add(finish);
    const u = new SpeechSynthesisUtterance(text);
    const v = voiceURI ? listVoices().find((x) => x.voiceURI === voiceURI) : undefined;
    if (v) {
      try {
        u.voice = v;
        u.lang = v.lang;
      } catch {
        /* a voice that vanished between listing and speaking: fall back to the default */
      }
    }
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
  if (ttsSupported) window.speechSynthesis.cancel();
  for (const finish of [...pendingSpeech]) finish();
}
