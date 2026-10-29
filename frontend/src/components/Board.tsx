import { Chessboard } from "react-chessboard";

interface Props {
  fen: string;
  orientation: "white" | "black";
  interactive: boolean;
  onDrop?: (from: string, to: string, piece: string) => boolean;
  width?: number;
}

export function Board({ fen, orientation, interactive, onDrop, width = 440 }: Props) {
  return (
    <div className="board" style={{ width: Math.min(width, 440) }}>
      <Chessboard
        position={fen}
        boardOrientation={orientation}
        arePiecesDraggable={interactive}
        onPieceDrop={(from, to, piece) => (interactive && onDrop ? onDrop(from, to, piece) : false)}
        animationDuration={150}
      />
    </div>
  );
}
