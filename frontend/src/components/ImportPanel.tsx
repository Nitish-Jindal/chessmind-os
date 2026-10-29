import { useState, type ChangeEvent } from "react";
import { api, type ImportIntent, type ImportJob } from "../api";

const POLL_MS = 500;
const POLL_LIMIT_MS = 10 * 60 * 1000;

export function ImportResult({ job }: { job: ImportJob }) {
  const a = job.artifacts_json ?? {};
  const errors = a.errors ?? (job.error ? job.error.split("\n") : []);
  const done = job.status !== "queued" && job.status !== "running";
  return (
    <div role="status" aria-label="Import result">
      <p>
        <span className="badge">{job.status}</span>{" "}
        {done ? (
          <>
            <strong>{job.progress_games}</strong> of {job.total_games} games imported
            {a.positions_touched != null && <> · {a.positions_touched} positions</>}
            {a.edges != null && <> · {a.edges} move edges</>}
            {a.repertoire_lines != null && job.kind === "player_pgn" && <> · {a.repertoire_lines} repertoire lines</>}
          </>
        ) : (
          <>importing… (job {job.id.slice(0, 8)})</>
        )}
      </p>
      {errors.length > 0 && (
        <details open={errors.length <= 5}>
          <summary className="error">{errors.length} error{errors.length === 1 ? "" : "s"}</summary>
          <ul>{errors.slice(0, 50).map((e, i) => <li key={i} className="error">{e}</li>)}</ul>
          {errors.length > 50 && <p className="muted">…and {errors.length - 50} more.</p>}
        </details>
      )}
    </div>
  );
}

/** PGN file + paste box -> POST /imports/pgn -> poll GET /imports/{id} until the job settles. */
export function ImportPanel({
  intent,
  opponentId,
  onDone,
}: {
  intent: ImportIntent;
  opponentId?: string;
  onDone?: (job: ImportJob) => void;
}) {
  const [pgn, setPgn] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function onFile(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFileName(f.name);
    void f.text().then(setPgn);
  }

  async function run() {
    setErr(null);
    setJob(null);
    setBusy(true);
    try {
      let j = await api.importPgn(pgn, intent, opponentId, fileName ? "upload:file" : "upload:paste");
      setJob(j);
      const t0 = Date.now();
      while (j.status === "queued" || j.status === "running") {
        if (Date.now() - t0 > POLL_LIMIT_MS) throw new Error("Import is taking too long; check back on the job later.");
        await new Promise((r) => setTimeout(r, POLL_MS));
        j = await api.importJob(j.id);
        setJob(j);
      }
      setPgn("");
      setFileName(null);
      onDone?.(j);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const gameCount = (pgn.match(/^\[Event /gm) ?? []).length;
  return (
    <div>
      <input type="file" accept=".pgn,text/plain" onChange={onFile} aria-label="PGN file" disabled={busy} />
      <textarea
        rows={6}
        style={{ width: "100%", marginTop: ".5rem" }}
        placeholder="…or paste PGN here"
        aria-label="PGN text"
        value={pgn}
        onChange={(e) => { setPgn(e.target.value); setFileName(null); }}
        disabled={busy}
      />
      <div className="row">
        <button className="primary" disabled={busy || !pgn.trim() || (intent === "opponent" && !opponentId)} onClick={run}>
          {busy ? "Importing…" : "Import"}
        </button>
        {pgn.trim() && <span className="muted">{fileName ?? "pasted text"} · ~{gameCount} game{gameCount === 1 ? "" : "s"} detected</span>}
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      {job && <ImportResult job={job} />}
    </div>
  );
}
