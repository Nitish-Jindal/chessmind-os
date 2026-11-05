import type { ReactNode } from "react";

interface ManualBoardRevealProps {
  blindfold: boolean;
  revealed: boolean;
  finished: boolean;
  onReveal: () => void;
  onHide: () => void;
  children: ReactNode;
}

/** Keeps a blindfold board hidden until the player explicitly reveals it. */
export function ManualBoardReveal({
  blindfold,
  revealed,
  finished,
  onReveal,
  onHide,
  children,
}: ManualBoardRevealProps) {
  const showBoard = !blindfold || revealed;

  return (
    <>
      {showBoard ? (
        children
      ) : (
        <div className="blind" role="img" aria-label="Board hidden for blindfold play">
          <div>
            <p><strong>Board hidden</strong></p>
            <p className="muted">Speak or type your move. Visualize the position.</p>
          </div>
        </div>
      )}
      <div className="row" style={{ marginTop: ".75rem" }}>
        {blindfold && (
          <button onClick={revealed ? onHide : onReveal} disabled={finished}>
            {revealed ? "Hide board" : "Reveal board"}
          </button>
        )}
      </div>
    </>
  );
}
