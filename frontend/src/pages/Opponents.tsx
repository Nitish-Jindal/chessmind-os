import { Chess } from "chess.js";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Opponent, type OpponentTree } from "../api";
import { Board } from "../components/Board";
import { ImportPanel } from "../components/ImportPanel";

function TreeBrowser({ opp }: { opp: Opponent }) {
  const [path, setPath] = useState<{ fen: string; san: string | null }[]>([{ fen: new Chess().fen(), san: null }]);
  const [tree, setTree] = useState<OpponentTree | null>(null);
  const cur = path[path.length - 1];
  useEffect(() => {
    setPath([{ fen: new Chess().fen(), san: null }]);
  }, [opp.id]);
  useEffect(() => {
    api.opponentTree(opp.id, cur.fen).then(setTree).catch(() => setTree(null));
  }, [opp.id, cur.fen, opp.games_imported]);

  function descend(uci: string) {
    const c = new Chess(cur.fen);
    const m = c.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
    setPath([...path, { fen: c.fen(), san: m.san }]);
  }
  return (
    <div className="grid">
      <div>
        <Board fen={cur.fen} orientation="white" interactive={false} />
        <div className="row">
          <button disabled={path.length < 2} onClick={() => setPath(path.slice(0, -1))}>Back</button>
          <span>{path.slice(1).map((s) => s.san).join(" ")}</span>
        </div>
      </div>
      <div>
        {tree && (
          <>
            <p className="muted">{tree.opponent_color} to move · {tree.sample_total} games at this position</p>
            <table>
              <thead><tr><th>Move</th><th>Games</th><th>Share</th><th>W / D / L</th></tr></thead>
              <tbody>
                {tree.edges.map((e) => (
                  <tr key={e.move_uci}>
                    <td><button onClick={() => descend(e.move_uci)}>{e.move_san}</button></td>
                    <td>{e.games_count}</td>
                    <td>{Math.round(e.confidence * 100)}%</td>
                    <td>{e.wins} / {e.draws} / {e.losses}</td>
                  </tr>
                ))}
                {tree.edges.length === 0 && <tr><td colSpan={4} className="muted">This opponent has no recorded moves here.</td></tr>}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

export function Opponents() {
  const nav = useNavigate();
  const [list, setList] = useState<Opponent[]>([]);
  const [sel, setSel] = useState<Opponent | null>(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState<"white" | "black">("white");
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(async (keep?: string) => {
    const rows = await api.opponents();
    setList(rows);
    setSel((s) => rows.find((r) => r.id === (keep ?? s?.id)) ?? rows[0] ?? null);
  }, []);
  useEffect(() => {
    reload().catch((e: Error) => setErr(e.message));
  }, [reload]);

  async function create() {
    try {
      const o = await api.createOpponent(name);
      setName("");
      await reload(o.id);
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function playTwin() {
    if (!sel) return;
    setErr(null);
    try {
      const s = await api.createSession({ mode: "opponent", player_color: color, sampler_mode: "opponent_twin", opponent_profile_id: sel.id });
      nav(`/session/${s.id}`);
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <>
      <h1>Opponents</h1>
      {err && <p className="error" role="alert">{err}</p>}
      <div className="row">
        <input placeholder="New opponent name" value={name} onChange={(e) => setName(e.target.value)} aria-label="New opponent name" />
        <button disabled={!name.trim()} onClick={create}>Add</button>
        <select value={sel?.id ?? ""} onChange={(e) => setSel(list.find((o) => o.id === e.target.value) ?? null)} aria-label="Opponent">
          {list.map((o) => <option key={o.id} value={o.id}>{o.display_name} ({o.games_imported} games)</option>)}
        </select>
      </div>
      {sel && (
        <>
          <section className="card">
            <h2>Import games for {sel.display_name}</h2>
            <ImportPanel key={sel.id} intent="opponent" opponentId={sel.id} onDone={() => void reload(sel.id)} />
            <div className="row" style={{ marginTop: ".75rem" }}>
              <label>
                You play{" "}
                <select value={color} onChange={(e) => setColor(e.target.value as "white" | "black")} aria-label="Your color against the twin">
                  <option value="white">White</option>
                  <option value="black">Black</option>
                </select>
              </label>
              <button className="primary" disabled={sel.games_imported === 0} onClick={playTwin}>Play the twin</button>
              {sel.games_imported === 0 && <span className="muted">Import some of their games first.</span>}
            </div>
          </section>
          <h2>Opening tree</h2>
          <TreeBrowser opp={sel} />
        </>
      )}
    </>
  );
}
