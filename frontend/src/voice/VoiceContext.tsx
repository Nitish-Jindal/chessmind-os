import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { cancelSpeech, speakText, ttsSupported, watchVoices } from "./speech";

export interface Caption {
  id: number;
  who: "you" | "opponent" | "coach" | "opening" | "system";
  text: string;
}

export type SpeechCounts = Record<Caption["who"], number>;
const emptySpeechCounts = (): SpeechCounts => ({ you: 0, opponent: 0, coach: 0, opening: 0, system: 0 });

interface VoiceCtx {
  voices: SpeechSynthesisVoice[];
  voiceURI: string | null;
  setVoiceURI(uri: string): void;
  ttsEnabled: boolean;
  setTtsEnabled(on: boolean): void;
  /** Speak and caption; resolves only after this utterance has finished playback. */
  say(who: Caption["who"], text: string): Promise<void>;
  caption(who: Caption["who"], text: string): void;
  captions: Caption[];
  clearCaptions(): void;
  speechCounts: SpeechCounts;
  subscribeSpeech(listener: (counts: SpeechCounts) => void): () => void;
}

const Ctx = createContext<VoiceCtx | null>(null);

export function VoiceProvider({ children }: { children: ReactNode }) {
  const { player, setPlayer } = useAuth();
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [speechCounts, setSpeechCounts] = useState<SpeechCounts>(emptySpeechCounts);
  const [localURI, setLocalURI] = useState<string | null>(null);
  const nextId = useRef(1);
  const speechCountsRef = useRef<SpeechCounts>(emptySpeechCounts());
  const speechListeners = useRef(new Set<(counts: SpeechCounts) => void>());

  useEffect(() => watchVoices(setVoices), []);

  // The persisted choice lives in players.settings_json.tts_voice.
  const savedURI = (player?.settings_json?.tts_voice as string | undefined) ?? null;
  const voiceURI = localURI ?? savedURI;
  const uriRef = useRef<string | null>(null);
  uriRef.current = voiceURI;
  const enabledRef = useRef(true);
  enabledRef.current = ttsEnabled;

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

  const updateSpeechCount = useCallback((who: Caption["who"], delta: number) => {
    const next = { ...speechCountsRef.current, [who]: Math.max(0, speechCountsRef.current[who] + delta) };
    speechCountsRef.current = next;
    setSpeechCounts(next);
    for (const listener of speechListeners.current) listener(next);
  }, []);

  const subscribeSpeech = useCallback((listener: (counts: SpeechCounts) => void) => {
    speechListeners.current.add(listener);
    listener(speechCountsRef.current);
    return () => speechListeners.current.delete(listener);
  }, []);

  const say = useCallback(
    async (who: Caption["who"], text: string) => {
      caption(who, text);
      if (!enabledRef.current || !ttsSupported || !text.trim()) return;
      // Notify the session synchronously so recognition stops before queued TTS can begin.
      updateSpeechCount(who, 1);
      try {
        await speakText(text, uriRef.current);
      } finally {
        updateSpeechCount(who, -1);
      }
    },
    [caption, updateSpeechCount],
  );

  useEffect(() => () => cancelSpeech(), []);

  const value = useMemo<VoiceCtx>(
    () => ({
      voices,
      voiceURI,
      setVoiceURI,
      ttsEnabled,
      setTtsEnabled,
      say,
      caption,
      captions,
      clearCaptions: () => setCaptions([]),
      speechCounts,
      subscribeSpeech,
    }),
    [voices, voiceURI, setVoiceURI, ttsEnabled, say, caption, captions, speechCounts, subscribeSpeech],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useVoice(): VoiceCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useVoice outside VoiceProvider");
  return c;
}
