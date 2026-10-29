import { useVoice } from "../voice/VoiceContext";
import { ttsSupported } from "../voice/speech";

export function VoicePicker() {
  const { voices, voiceURI, setVoiceURI, ttsEnabled, setTtsEnabled, say } = useVoice();
  if (!ttsSupported) return <p className="muted">Speech synthesis is not available in this browser; captions are shown instead.</p>;
  return (
    <div className="row">
      <label>
        Voice{" "}
        <select
          value={voiceURI ?? ""}
          onChange={(e) => {
            setVoiceURI(e.target.value);
          }}
          aria-label="Text-to-speech voice"
        >
          <option value="">Browser default</option>
          {voices.map((v) => (
            <option key={v.voiceURI} value={v.voiceURI}>
              {v.name} ({v.lang})
            </option>
          ))}
        </select>
      </label>
      <button type="button" onClick={() => say("system", "This is how I will sound.")}>
        Test voice
      </button>
      <label>
        <input type="checkbox" checked={ttsEnabled} onChange={(e) => setTtsEnabled(e.target.checked)} /> Speak aloud
      </label>
    </div>
  );
}
