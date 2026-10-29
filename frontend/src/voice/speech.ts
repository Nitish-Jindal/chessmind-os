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

export interface Recognizer {
  start(): void;
  stop(): void;
}

/** One utterance per start(): interim transcripts stream out, final alternatives resolve at the end. */
export function createRecognizer(handlers: {
  onInterim: (text: string) => void;
  onFinal: (alternatives: string[]) => void;
  onState: (listening: boolean) => void;
  onError: (message: string) => void;
}): Recognizer | null {
  if (!Ctor) return null;
  let rec: SRInstance | null = null;
  let gotFinal = false;
  return {
    start() {
      if (rec) return;
      const r = new Ctor();
      rec = r;
      gotFinal = false;
      r.lang = "en-US";
      r.interimResults = true;
      r.continuous = false;
      r.maxAlternatives = 5;
      r.onresult = (e) => {
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i];
          if (res.isFinal) {
            gotFinal = true;
            const alts: string[] = [];
            for (let j = 0; j < res.length; j++) alts.push(res[j].transcript);
            handlers.onFinal(alts);
          } else handlers.onInterim(res[0].transcript);
        }
      };
      r.onerror = (e) => {
        if (e.error !== "no-speech" && e.error !== "aborted") handlers.onError(`Speech recognition error: ${e.error}`);
      };
      r.onend = () => {
        rec = null;
        handlers.onState(false);
        if (!gotFinal) handlers.onInterim("");
      };
      handlers.onState(true);
      try {
        r.start();
      } catch {
        rec = null;
        handlers.onState(false);
      }
    },
    stop() {
      rec?.stop();
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
    u.onend = () => resolve();
    u.onerror = () => resolve();
    window.speechSynthesis.speak(u);
  });
}

export function cancelSpeech(): void {
  if (ttsSupported) window.speechSynthesis.cancel();
}
