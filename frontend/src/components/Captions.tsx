import { useEffect, useRef } from "react";
import { useVoice } from "../voice/VoiceContext";

/** Live captions: everything spoken (by the user, opponent or coach) is also written here. */
export function Captions({ interim }: { interim?: string }) {
  const { captions } = useVoice();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [captions, interim]);
  return (
    <div className="captions" role="log" aria-live="polite" aria-label="Live captions">
      {captions.map((c) => (
        <p key={c.id} className={`cap cap-${c.who}`}>
          <strong>{c.who}:</strong> {c.text}
        </p>
      ))}
      {interim ? (
        <p className="cap cap-interim" aria-live="off">
          <em>hearing: {interim}…</em>
        </p>
      ) : null}
      <div ref={end} />
    </div>
  );
}
