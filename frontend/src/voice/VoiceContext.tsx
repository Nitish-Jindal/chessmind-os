import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { cancelSpeech as cancelSynthesis, SpeechQueue, speakText, ttsSupported, watchVoices } from "./speech";

export interface Caption {
  id: number;
  who: "you" | "opponent" | "coach" | "opening" | "system";
  text: string;
}

interface VoiceCtx {
  voices: SpeechSynthesisVoice[];
  voiceURI: string | null;
  setVoiceURI(uri: string): void;
  ttsEnabled: boolean;
  setTtsEnabled(on: boolean): void;
  /** Caption and enqueue speech; resolves after this utterance has left the shared FIFO queue. */
  say(who: Caption["who"], text: string): Promise<void>;
  /** Register the active session's synchronous mic gate for every TTS source in the app. */
  registerSpeechGate(gate: (blocked: boolean) => Promise<void> | void): () => void;
  /** True while any queued or active utterance is pending. */
  speechPending: boolean;
  cancelQueuedSpeech(): void;
  caption(who: Caption["who"], text: string): void;
  captions: Caption[];
  clearCaptions(): void;
}

const Ctx = createContext<VoiceCtx | null>(null);

export function VoiceProvider({ children }: { children: ReactNode }) {
  const { player, setPlayer } = useAuth();
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [speechPending, setSpeechPending] = useState(false);
  const speechPendingRef = useRef(false);
  const [localURI, setLocalURI] = useState<string | null>(null);
  const nextId = useRef(1);

  useEffect(() => watchVoices(setVoices), []);

  // The persisted choice lives in players.settings_json.tts_voice.
  const savedURI = (player?.settings_json?.tts_voice as string | undefined) ?? null;
  const voiceURI = localURI ?? savedURI;
  const uriRef = useRef<string | null>(null);
  uriRef.current = voiceURI;
  const enabledRef = useRef(true);
  enabledRef.current = ttsEnabled;
  const speechGates = useRef(new Set<(blocked: boolean) => Promise<void> | void>());
  const queueRef = useRef<SpeechQueue | null>(null);
  if (!queueRef.current) {
    queueRef.current = new SpeechQueue(
      speakText,
      cancelSynthesis,
      (count) => {
        const pending = count > 0;
        speechPendingRef.current = pending;
        setSpeechPending(pending);
      },
      async (blocked) => {
        await Promise.all([...speechGates.current].map((gate) => gate(blocked)));
      },
    );
  }

  const setVoiceURI = useCallback(
    (uri: string) => {
      setLocalURI(uri);
      api
        .patchSettings({ tts_voice: uri })
        .then(setPlayer)
        .catch(() => {
          /* keep the local choice for this tab even if persisting failed */
        });
    },
    [setPlayer],
  );

  const caption = useCallback((who: Caption["who"], text: string) => {
    setCaptions((c) => [...c.slice(-39), { id: nextId.current++, who, text }]);
  }, []);

  const say = useCallback(
    (who: Caption["who"], text: string) => {
      caption(who, text);
      return queueRef.current!.enqueue(text, uriRef.current, enabledRef.current && ttsSupported);
    },
    [caption],
  );

  const registerSpeechGate = useCallback((gate: (blocked: boolean) => Promise<void> | void) => {
    speechGates.current.add(gate);
    if (speechPendingRef.current) void gate(true);
    return () => { speechGates.current.delete(gate); };
  }, []);

  const cancelQueuedSpeech = useCallback(() => queueRef.current?.cancelAll(), []);

  useEffect(() => () => queueRef.current?.cancelAll(), []);

  const value = useMemo<VoiceCtx>(
    () => ({
      voices,
      voiceURI,
      setVoiceURI,
      ttsEnabled,
      setTtsEnabled,
      say,
      registerSpeechGate,
      speechPending,
      cancelQueuedSpeech,
      caption,
      captions,
      clearCaptions: () => setCaptions([]),
    }),
    [voices, voiceURI, setVoiceURI, ttsEnabled, say, registerSpeechGate, speechPending, cancelQueuedSpeech, caption, captions],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useVoice(): VoiceCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useVoice outside VoiceProvider");
  return c;
}
