import { Chess } from "chess.js";
import { useEffect, useState } from "react";
import { api, type MastersStats, type OpeningTree } from "../api";
import { Board } from "../components/Board";

interface Step { fen: string; san: string | null }

const pct = (n: number, total: number) => (total ? `${Math.round((100 * n) / total)}%` : "—");

const STATUS_TEXT: Record<MastersStats["status"], string> = {
  live: "fetched just now",
  cached: "cached",
  stale: "cached copy (Lichess unreachable)",
  unavailable: "unavailable",
  disabled: "disabled",
};

/** Real master statistics from the Lichess explorer, with source, sample size and attribution. */
function MastersCard({ m, onPick }: { m: MastersStats; onPick: (uci: string) => void }) {
  const ok = m.status !== "unavailable" && m.status !== "disabled";
  return (
    <section className="card" aria-label="Master games">
      <h2 style={{ marginTop: 0 }}>
        Master games <span className="badge">{STATUS_TEXT[m.status]}</span>
      </h2>
      {ok ? (
        <>
          <p>
            <strong>{m.total_games.toLocaleString()}</strong> master games from this position
            {m.opening_name && <> · <span className="badge">{m.opening_eco}</span> {m.opening_name}</>}
          </p>
          <table>
            <thead><tr><th>Move</th><th>Games</th><th>White / Draw / Black</th><th>Avg rating</th></tr></thead>
            <tbody>
              {m.moves.map((x) => (
                <tr key={x.uci}>
                  <td><button onClick={() => onPick(x.uci)}>{x.san}</button></td>
                  <td>{x.games.toLocaleString()}</td>
                  <td>{pct(x.white, x.games)} / {pct(x.draws, x.games)} / {pct(x.black, x.games)}</td>
                  <td>{x.average_rating ?? "—"}</td>
                </tr>
              ))}
              {m.moves.length === 0 && <tr><td colSpan={4} className="muted">No master games reach this position.</td></tr>}
            </tbody>
          </table>
        </>
      ) : (
        <p className="muted">
          Master statistics are {m.status} right now{m.error ? ` (${m.error})` : ""}. Showing local counts only.
        </p>
      )}
      <p className="muted" aria-label="Attribution">
        Source: {m.attribution}
        {m.fetched_at && <> · fetched {new Date(m.fetched_at).toLocaleString()}</>} ·{" "}
        <a href={m.explorer_url} target="_blank" rel="noreferrer">open on lichess.org</a>
      </p>
    </section>
  );
}

/** Browse the universal opening graph. Tree data comes from /openings/tree; chess.js is used only to step the display FEN. */
export function Openings() {
  const [path, setPath] = useState<Step[]>([{ fen: new Chess().fen(), san: null }]);
  const [tree, setTree] = useState<OpeningTree | null>(null);
  const [masters, setMasters] = useState<MastersStats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const cur = path[path.length - 1];

  useEffect(() => {
    setErr(null);
    api
      .openingTree(cur.fen)
      .then((t) => {
        setTree(t);
        setMasters(t.masters);
      })
      .catch((e: Error) => {
        // Not in the local graph: master stats still exist for any legal position.
        setTree(null);
        setErr(e.message);
        api.masters(cur.fen).then(setMasters).catch(() => setMasters(null));
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
          {masters && <MastersCard m={masters} onPick={descend} />}
          <section className="card" aria-label="Local games">
            <h2 style={{ marginTop: 0 }}>Local games</h2>
            {err && <p className="muted">{err === "position not in graph yet" ? "No imported games reach this position." : err}</p>}
            {tree && (
              <>
                <p>
                  <span className="badge">{tree.root.eco ?? "—"}</span> <strong>{tree.root.opening_name ?? "Unnamed position"}</strong>
                </p>
                <p className="muted">{tree.local_stats_note} {tree.total_positions} positions · {tree.total_edges} edges.</p>
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
          </section>
        </div>
      </div>
    </>
  );
}
