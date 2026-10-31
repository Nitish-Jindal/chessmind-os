import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { cancelSpeech, speakText, ttsSupported, watchVoices } from "./speech";

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
  /** Speak and caption. Captions are always written, even when TTS is off or unsupported. */
  say(who: Caption["who"], text: string): void;
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
      if (enabledRef.current && ttsSupported) void speakText(text, uriRef.current);
    },
    [caption],
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
    }),
    [voices, voiceURI, setVoiceURI, ttsEnabled, say, caption, captions],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useVoice(): VoiceCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useVoice outside VoiceProvider");
  return c;
}
