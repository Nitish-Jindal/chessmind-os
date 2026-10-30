import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Opponent, type SamplerMode, type SessionMode } from "../api";
import { useAuth } from "../auth";
import { VoicePicker } from "../components/VoicePicker";

function AuthForm() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, name || email.split("@")[0]);
    } catch (x) {
      setErr((x as Error).message);
    }
  }
  return (
    <form className="card" onSubmit={submit}>
      <h2>{mode === "login" ? "Log in" : "Create account"}</h2>
      <div className="row">
        {mode === "register" && (
          <input placeholder="Display name" value={name} onChange={(e) => setName(e.target.value)} aria-label="Display name" />
        )}
        <input type="email" required placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Email" />
        <input type="password" required minLength={8} placeholder="Password (8+)" value={password} onChange={(e) => setPassword(e.target.value)} aria-label="Password" />
        <button className="primary" type="submit">{mode === "login" ? "Log in" : "Register"}</button>
        <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "Need an account?" : "Have an account?"}
        </button>
      </div>
      {err && <p className="error" role="alert">{err}</p>}
    </form>
  );
}

function Launcher() {
  const nav = useNavigate();
  const [mode, setMode] = useState<SessionMode>("guided");
  const [color, setColor] = useState<"white" | "black">("white");
  const [sampler, setSampler] = useState<SamplerMode>("mix");
  const [opponents, setOpponents] = useState<Opponent[]>([]);
  const [opp, setOpp] = useState("");
  const [quiet, setQuiet] = useState(false);
  const [opening, setOpening] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.opponents().then(setOpponents).catch(() => setOpponents([]));
  }, []);

  async function start() {
    setErr(null);
    try {
      const s = await api.createSession({
        mode,
        player_color: color,
        sampler_mode: opp ? "opponent_twin" : sampler,
        opponent_profile_id: opp || null,
        quiet,
        opening_request: opening.trim() || null,
      });
      nav(`/session/${s.id}`);
    } catch (x) {
      setErr((x as Error).message);
    }
  }
  return (
    <section className="card">
      <h2>Start a session</h2>
      <div className="row">
        <label>
          Visualization{" "}
          <select value={mode} onChange={(e) => setMode(e.target.value as SessionMode)}>
            <option value="guided">Full board</option>
            <option value="blindfold">Blindfold (voice only)</option>
          </select>
        </label>
        <label>
          Play as{" "}
          <select value={color} onChange={(e) => setColor(e.target.value as "white" | "black")}>
            <option value="white">White</option>
            <option value="black">Black</option>
          </select>
        </label>
        <label>
          Opponent style{" "}
          <select value={sampler} onChange={(e) => setSampler(e.target.value as SamplerMode)} disabled={!!opp}>
            <option value="mix">Mix (book + variety)</option>
            <option value="mainline">Mainline</option>
            <option value="surprise">Surprise</option>
          </select>
        </label>
        <label>
          Digital twin{" "}
          <select value={opp} onChange={(e) => setOpp(e.target.value)}>
            <option value="">None</option>
            {opponents.map((o) => (
              <option key={o.id} value={o.id}>{o.display_name} ({o.games_imported} games)</option>
            ))}
          </select>
        </label>
        <label>
          Opponent's opening{" "}
          <input
            value={opening}
            onChange={(e) => setOpening(e.target.value)}
            placeholder="any · Caro-Kann · London System · d4"
            aria-label="Opponent's opening"
          />
        </label>
        <label>
          <input type="checkbox" checked={quiet} onChange={(e) => setQuiet(e.target.checked)} aria-label="Quiet mode" /> Quiet mode
        </label>
        <button className="primary" onClick={start}>Start</button>
      </div>
      <p className="muted">
        Playing Black? The opponent opens and announces its first move. You can also say “play d4 against me” or
        “play the Queen's Gambit against me” before your first move to have it open differently.
      </p>
      {err && <p className="error" role="alert">{err}</p>}
    </section>
  );
}

export function Home() {
  const { player } = useAuth();
  return (
    <>
      <h1>Train your openings by voice</h1>
      <p className="muted">
        Speak your moves, hear the reply, and get Stockfish-grounded coaching. Blindfold mode hides the board entirely.
      </p>
      {player ? (
        <>
          <Launcher />
          <section className="card">
            <h2>Voice settings</h2>
            <VoicePicker />
            <p className="muted">Your voice choice is saved to your profile.</p>
          </section>
        </>
      ) : (
        <AuthForm />
      )}
    </>
  );
}
