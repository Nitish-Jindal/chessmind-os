import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type MasteryDashboard, type MasteryRow } from "../api";
import { useAuth } from "../auth";

function Score({ v }: { v: number }) {
  return (
    <div className="bar" title={v.toFixed(2)} role="img" aria-label={`${Math.round(v * 100)} percent`}>
      <i style={{ width: `${Math.round(v * 100)}%` }} />
    </div>
  );
}

function Table({ rows }: { rows: MasteryRow[] }) {
  if (rows.length === 0) return <p className="muted">Nothing here yet — play a session first.</p>;
  return (
    <table>
      <thead><tr><th>Position</th><th>State</th><th>Recall</th><th>Understanding</th><th>Visualization</th><th>Speed</th><th>Due</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.position_id}>
            <td>{r.opening_name ?? r.fen.split(" ")[0]}</td>
            <td><span className="badge">{r.state}</span></td>
            <td><Score v={r.recall_score} /></td>
            <td><Score v={r.understanding_score} /></td>
            <td><Score v={r.visualization_score} /></td>
            <td><Score v={r.speed_score} /></td>
            <td>{new Date(r.due_at).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Mastery() {
  const { player } = useAuth();
  const [d, setD] = useState<MasteryDashboard | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const nav = useNavigate();
  useEffect(() => {
    if (player) api.mastery(player.id).then(setD).catch((e: Error) => setErr(e.message));
  }, [player]);

  /** POST /mastery/next picks the next due item; the drill is a real session started at that position. */
  async function startDrill() {
    if (!player) return;
    setErr(null);
    setNote(null);
    try {
      const n = await api.nextDrill(player.id);
      if (!n.row) return setNote("Nothing to drill yet — play a session first and your mistakes will show up here.");
      if (!n.due_now) return setNote(`Nothing is due right now. Next item is due ${new Date(n.row.due_at).toLocaleString()}.`);
      const s = await api.createSession({
        mode: "guided",
        player_color: n.row.fen.split(" ")[1] === "b" ? "black" : "white",
        sampler_mode: "mainline",
        novelty_prob: 0,
        starting_fen: n.row.fen,
      });
      nav(`/session/${s.id}?drill=${n.row.position_id}`);
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  return (
    <>
      <h1>Mastery</h1>
      <div className="row">
        <button className="primary" onClick={() => void startDrill()}>Start due drill</button>
        {d && <span className="muted">{d.due_now} due now</span>}
        {note && <span role="status">{note}</span>}
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      {d && (
        <>
          <div className="row">
            {Object.entries(d.counts_by_state).map(([k, v]) => (
              <span key={k} className="card"><strong>{v}</strong> {k}</span>
            ))}
            <span className="card"><strong>{d.due_now}</strong> due now</span>
            <span className="card"><strong>{d.due_next_24h}</strong> due in 24h</span>
          </div>
          <h2>Weakest positions</h2>
          <Table rows={d.weakest} />
          <h2>Due soon</h2>
          <Table rows={d.due_soon} />
        </>
      )}
    </>
  );
}
