import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ImportIntent, type ImportJob, type Opponent } from "../api";
import { ImportPanel } from "../components/ImportPanel";

const INTENTS: { value: ImportIntent; label: string; help: string }[] = [
  { value: "repertoire", label: "My repertoire", help: "Your own games or study lines. Feeds the opening graph and your repertoire lines." },
  { value: "corpus", label: "Reference corpus", help: "Master or database games. Feeds opening-graph statistics only; no repertoire lines." },
  { value: "opponent", label: "An opponent", help: "Games by someone you will face. Builds their conditional opening tree for twin play." },
];

export function ImportPage() {
  const [intent, setIntent] = useState<ImportIntent>("repertoire");
  const [opponents, setOpponents] = useState<Opponent[]>([]);
  const [oppId, setOppId] = useState("");
  const [newName, setNewName] = useState("");
  const [done, setDone] = useState<ImportJob | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.opponents().then((rows) => { setOpponents(rows); setOppId((cur) => cur || rows[0]?.id || ""); }).catch((e: Error) => setErr(e.message));
  }, []);

  async function addOpponent() {
    try {
      const o = await api.createOpponent(newName.trim());
      setOpponents((rows) => [o, ...rows]);
      setOppId(o.id);
      setNewName("");
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  const help = INTENTS.find((i) => i.value === intent)!.help;
  const succeeded = done && (done.status === "succeeded" || done.status === "partial") && done.progress_games > 0;
  return (
    <>
      <h1>Import PGN</h1>
      <section className="card">
        <div className="row">
          <label>
            Import as{" "}
            <select value={intent} onChange={(e) => { setIntent(e.target.value as ImportIntent); setDone(null); }} aria-label="Import intent">
              {INTENTS.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
            </select>
          </label>
          {intent === "opponent" && (
            <>
              <label>
                Opponent{" "}
                <select value={oppId} onChange={(e) => setOppId(e.target.value)} aria-label="Opponent profile">
                  {opponents.length === 0 && <option value="">(none yet)</option>}
                  {opponents.map((o) => <option key={o.id} value={o.id}>{o.display_name} ({o.games_imported} games)</option>)}
                </select>
              </label>
              <input placeholder="New opponent name" aria-label="New opponent name" value={newName} onChange={(e) => setNewName(e.target.value)} />
              <button disabled={!newName.trim()} onClick={addOpponent}>Add</button>
            </>
          )}
        </div>
        <p className="muted">{help}</p>
        {err && <p className="error" role="alert">{err}</p>}
        <ImportPanel intent={intent} opponentId={intent === "opponent" ? oppId : undefined} onDone={setDone} />
      </section>
      {succeeded && done.kind === "player_pgn" && (
        <p><Link className="badge" to="/openings">Browse the opening tree →</Link> Your {done.progress_games} games are now in the graph.</p>
      )}
      {succeeded && done.kind === "corpus_pgn" && (
        <p><Link className="badge" to="/openings">Browse the opening tree →</Link> The reference games are now in the graph.</p>
      )}
      {succeeded && done.kind === "opponent_pgn" && (
        <p><Link className="badge" to="/opponents">Open the opponent's tree →</Link> The opponent profile now has {done.progress_games} more games.</p>
      )}
    </>
  );
}
