import { Chess } from "chess.js";
import { useEffect, useState } from "react";
import { api, type OpeningTree } from "../api";
import { Board } from "../components/Board";

interface Step { fen: string; san: string | null }

/** Browse the universal opening graph. Tree data comes from /openings/tree; chess.js is used only to step the display FEN. */
export function Openings() {
  const [path, setPath] = useState<Step[]>([{ fen: new Chess().fen(), san: null }]);
  const [tree, setTree] = useState<OpeningTree | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const cur = path[path.length - 1];

  useEffect(() => {
    setErr(null);
    api.openingTree(cur.fen).then(setTree).catch((e: Error) => {
      setTree(null);
      setErr(e.message);
    });
  }, [cur.fen]);

  function descend(uci: string) {
    const c = new Chess(cur.fen);
    const m = c.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
    setPath([...path, { fen: c.fen(), san: m.san }]);
  }

  return (
    <>
      <h1>Opening tree</h1>
      <div className="grid">
        <div>
          <Board fen={cur.fen} orientation="white" interactive={false} />
          <p>{path.slice(1).map((s) => s.san).join(" ") || "Starting position"}</p>
          <div className="row">
            <button disabled={path.length < 2} onClick={() => setPath(path.slice(0, -1))}>Back</button>
            <button disabled={path.length < 2} onClick={() => setPath(path.slice(0, 1))}>Reset</button>
          </div>
        </div>
        <div>
          {err && <p className="error">{err}</p>}
          {tree && (
            <>
              <p>
                <span className="badge">{tree.root.eco ?? "—"}</span> <strong>{tree.root.opening_name ?? "Unnamed position"}</strong>
              </p>
              <p className="muted">{tree.total_positions} positions · {tree.total_edges} edges in the graph</p>
              <table>
                <thead><tr><th>Move</th><th>Games</th><th>W / D / B</th></tr></thead>
                <tbody>
                  {tree.root.edges.map((e) => (
                    <tr key={e.uci}>
                      <td><button onClick={() => descend(e.uci)}>{e.san}</button></td>
                      <td>{e.games_count}</td>
                      <td>{e.white_wins} / {e.draws} / {e.black_wins}</td>
                    </tr>
                  ))}
                  {tree.root.edges.length === 0 && <tr><td colSpan={3} className="muted">No continuations indexed from here.</td></tr>}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </>
  );
}
