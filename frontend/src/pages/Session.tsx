import { Chess } from "chess.js";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type LegalMoves, type MasteryRow, type MoveResult, type QuestionTally, type QuizQuestion, type SessionState } from "../api";
import { useAuth } from "../auth";
import { Board } from "../components/Board";
import { Captions } from "../components/Captions";
import { MasteryDelta } from "../components/MasteryDelta";
import { VoicePicker } from "../components/VoicePicker";
import {
  buildLegalMoves,
  isBoardQuestion,
  parseCommand,
  resolveAlternatives,
  resolveConfirmation,
  speakMove,
  type LegalMove,
} from "../voice/resolver";
import { createRecognizer, sttSupported, cancelSpeech, type Recognizer } from "../voice/speech";
import { useVoice } from "../voice/VoiceContext";

function fmtEval(cp: number | null | undefined, mate?: number | null): string {
  if (mate != null) return `mate in ${mate}`;
  if (cp == null) return "—";
  return `${cp >= 0 ? "+" : ""}${(cp / 100).toFixed(2)}`;
}

/** Remount per session id so "next drill" starts from clean state. */
export function Session() {
  const { id = "" } = useParams();
  return <SessionView key={id} id={id} />;
}

function SessionView({ id }: { id: string }) {
  const { say, caption, clearCaptions } = useVoice();
  const { player } = useAuth();
  const nav = useNavigate();
  // ?drill=<position_id>: this session was opened from a due mastery item; show its row before/after the answer.
  const drillPos = useSearchParams()[0].get("drill");
  const [drill, setDrill] = useState<{ before: MasteryRow; after: MasteryRow | null } | null>(null);
  const drillAnswered = useRef(false);

  const [sess, setSess] = useState<SessionState | null>(null);
  const [legal, setLegal] = useState<LegalMoves | null>(null);
  const [fen, setFen] = useState<string>("");
  const [last, setLast] = useState<MoveResult | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState<LegalMove[] | null>(null);
  const [interim, setInterim] = useState("");
  const [listening, setListening] = useState(false);
  const [text, setText] = useState("");
  // Position questions (handoff §3.3): the player asks about the board, or answers a quiz about it.
  const [quiz, setQuiz] = useState<QuizQuestion | null>(null);
  const [tally, setTally] = useState<QuestionTally | null>(null);
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  const turnStart = useRef(Date.now());

  const legalMoves = useMemo(() => (legal ? buildLegalMoves(legal.legal_moves_san, legal.legal_moves_uci) : []), [legal]);
  const blindfold = sess?.mode === "blindfold";
  const showBoard = !blindfold || revealed;
  const color = sess?.state_json.player_color ?? "white";
  const finished = legal?.status === "finished";
  const myTurn = legal?.next_to_move === "player" && !finished;

  const refresh = useCallback(async () => {
    const [s, l] = await Promise.all([api.getSession(id), api.legal(id)]);
    setSess(s);
    setLegal(l);
    setFen(l.current_fen);
    turnStart.current = Date.now();
    return { s, l };
  }, [id]);

  // Initial load; announce an opening opponent move (player is black).
  const announced = useRef<string | null>(null);
  useEffect(() => {
    if (announced.current === id) return; // StrictMode runs effects twice in dev
    announced.current = id;
    clearCaptions();
    refresh()
      .then(({ s }) => {
        const om = s.state_json.last_opponent_move;
        say("system", s.mode === "blindfold" ? "Blindfold session. The board is hidden. Hold the talk button and say your move." : "Session started. Make your move on the board, or by voice.");
        if (om && s.state_json.move_history_san.length === 1) say("opponent", `Opponent plays ${speakMove(om.san)}.`);
      })
      .catch((e: Error) => setErr(e.message));
    return () => cancelSpeech();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    if (!drillPos || !player) return;
    api.masteryRow(player.id, drillPos).then((before) => setDrill({ before, after: null })).catch((e: Error) => setErr(e.message));
  }, [drillPos, player]);

  async function nextDrill() {
    if (!player) return;
    await api.finish(id).catch(() => {});
    const n = await api.nextDrill(player.id);
    if (!n.row || !n.due_now) return nav("/mastery");
    const s = await api.createSession({
      mode: "guided",
      player_color: n.row.fen.split(" ")[1] === "b" ? "black" : "white",
      sampler_mode: "mainline",
      novelty_prob: 0,
      starting_fen: n.row.fen,
    });
    nav(`/session/${s.id}?drill=${n.row.position_id}`);
  }

  /** Every move — dragged, spoken or typed — goes through the backend, which is the only authority. */
  const submit = useCallback(
    async (m: { san?: string; uci?: string }) => {
      if (busy) return;
      setBusy(true);
      setErr(null);
      setPending(null);
      try {
        const r = await api.move(id, { ...m, latency_ms: Date.now() - turnStart.current });
        setLast(r);
        if (drillPos && player && !drillAnswered.current) {
          // First answer in a drill session is the graded recall of the drilled position.
          drillAnswered.current = true;
          const after = await api.masteryRow(player.id, drillPos);
          setDrill((d) => (d ? { ...d, after } : d));
        }
        setFen(r.current_fen);
        setRevealed(false);
        if (r.opponent_move) say("opponent", `Opponent plays ${speakMove(r.opponent_move.san)}.`);
        say("coach", r.coaching.spoken_summary);
        if (r.status === "finished") say("system", "The game is over.");
        await refresh();
      } catch (e) {
        const msg = (e as Error).message;
        setErr(msg);
        caption("system", `Move rejected: ${msg}`);
        await refresh().catch(() => {});
      } finally {
        setBusy(false);
      }
    },
    [busy, id, say, caption, refresh, drillPos, player],
  );

  /** Board question → server answers from python-chess / cached Stockfish; spoken and captioned. */
  const askBoard = useCallback(
    async (alts: string[]) => {
      try {
        const r = await api.ask(id, alts);
        say(r.understood ? "coach" : "system", r.answer_text);
        setLastAnswer(r.answer_text);
      } catch (e) {
        setErr((e as Error).message);
      }
    },
    [id, say],
  );

  const startQuiz = useCallback(async () => {
    try {
      const q = await api.quiz(id);
      setQuiz(q);
      setLastAnswer(null);
      say("coach", q.prompt);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [id, say]);

  /** Quiz answer → scored server-side against the board captured when the question was asked. */
  const answerQuiz = useCallback(
    async (q: QuizQuestion, alts: string[]) => {
      try {
        const r = await api.answerQuiz(id, q.question_id, alts);
        if (!r.understood) return say("system", r.feedback);
        setQuiz(null);
        setTally(r.tally);
        setLastAnswer(r.feedback);
        say("coach", r.feedback);
      } catch (e) {
        setErr((e as Error).message);
      }
    },
    [id, say],
  );

  // Mutable view of state for callbacks owned by the recognizer.
  const live = useRef({ legalMoves, pending, myTurn, busy, submit, last, quiz });
  live.current = { legalMoves, pending, myTurn, busy, submit, last, quiz };

  const doReveal = useCallback(async () => {
    try {
      const r = await api.reveal(id, "voice-or-button");
      setFen(r.current_fen);
      setRevealed(true);
      setLegal((l) => (l ? { ...l, reveals_used: r.reveals_used, reveals_budget: r.reveals_budget, hints_used: r.hints_used } : l));
      caption(
        "system",
        r.was_free
          ? `Board revealed (${r.reveals_used} of ${r.reveals_budget} free reveals used).`
          : `Board revealed — beyond the free budget, this counts against your visualization score (hints used: ${r.hints_used}).`,
      );
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [id, caption]);

  /** Handle one recognized/typed utterance (all STT alternatives, best first). */
  const handleUtterance = useCallback(
    (alts: string[]) => {
      const L = live.current;
      const heard = alts[0];
      caption("you", heard);

      const cmd = parseCommand(heard);
      if (cmd === "reveal" && blindfold) return void doReveal();
      if (cmd === "repeat") {
        const t = L.quiz ? L.quiz.prompt : L.last?.opponent_move ? `Opponent played ${speakMove(L.last.opponent_move.san)}.` : "Nothing to repeat yet.";
        return say("system", t);
      }
      if (cmd === "quiz") return void startQuiz();
      if (L.quiz) {
        if (/\b(skip|cancel|never ?mind)\b/i.test(heard)) {
          setQuiz(null);
          return say("system", "Question skipped.");
        }
        return void answerQuiz(L.quiz, alts);
      }
      if (isBoardQuestion(heard)) return void askBoard(alts);
      if (!L.myTurn || L.busy) return say("system", "It is not your turn.");

      if (L.pending) {
        let outcome = resolveConfirmation(heard, L.pending);
        for (let i = 1; outcome.kind === "unclear" && i < alts.length; i++) outcome = resolveConfirmation(alts[i], L.pending);
        if (outcome.kind === "chosen") return void L.submit({ san: outcome.move.san, uci: outcome.move.uci });
        if (outcome.kind === "cancel") {
          setPending(null);
          return say("system", "Cancelled. Say your move again.");
        }
        if (outcome.kind === "narrowed") {
          setPending(outcome.moves);
          return say("system", `Still ambiguous: ${outcome.moves.map((m) => speakMove(m.san)).join(", or ")}. Which one?`);
        }
        return say("system", `Say first, second, or the move. Options: ${L.pending.map((m) => speakMove(m.san)).join(", or ")}.`);
      }

      const r = resolveAlternatives(alts, L.legalMoves);
      if (r.kind === "match") return void L.submit({ san: r.move.san, uci: r.move.uci });
      if (r.kind === "ambiguous") {
        setPending(r.moves);
        return say("system", `More than one move fits. Did you mean ${r.moves.map((m) => speakMove(m.san)).join(", or ")}?`);
      }
      say("system", `I didn't find a legal move for "${heard}". Try again.`);
    },
    [blindfold, caption, say, doReveal, startQuiz, answerQuiz, askBoard],
  );
  const onUtterance = useRef(handleUtterance);
  onUtterance.current = handleUtterance;

  // Push-to-talk recognizer.
  const rec = useRef<Recognizer | null>(null);
  useEffect(() => {
    rec.current = createRecognizer({
      onInterim: setInterim,
      onFinal: (alts) => {
        setInterim("");
        onUtterance.current(alts);
      },
      onState: setListening,
      onError: (m) => caption("system", m),
    });
    return () => rec.current?.stop();
  }, [caption]);

  const startTalking = useCallback(() => {
    cancelSpeech(); // never let TTS leak into the mic
    rec.current?.start();
  }, []);
  const stopTalking = useCallback(() => rec.current?.stop(), []);

  // Space bar = push-to-talk when not typing.
  useEffect(() => {
    const typing = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && !typing(e.target)) {
        e.preventDefault();
        startTalking();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && !typing(e.target)) stopTalking();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [startTalking, stopTalking]);

  // Board drag: chess.js only pre-checks so illegal drops snap back; the POST is authoritative.
  function onDrop(from: string, to: string): boolean {
    if (!myTurn || busy) return false;
    try {
      const c = new Chess(fen);
      const m = c.move({ from, to, promotion: "q" });
      setFen(c.fen()); // optimistic; replaced by the server's current_fen
      void submit({ uci: from + to + (m.promotion ?? "") });
      return true;
    } catch {
      return false;
    }
  }

  function onText(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    setText("");
    handleUtterance([t]);
  }

  if (!sess || !legal) return <p>{err ?? "Loading session…"}</p>;
  const ev = last?.best_lines[0];

  return (
    <>
      <h1>
        {drillPos ? "Mastery drill" : blindfold ? "Blindfold session" : sess.settings_json.sampler_mode === "opponent_twin" ? "Playing the twin" : "Guided session"}{" "}
        <span className="badge">{color}</span>
      </h1>
      <div className="grid">
        <div>
          {showBoard ? (
            <Board fen={fen} orientation={color} interactive={!!myTurn && !busy} onDrop={onDrop} />
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
              <button onClick={() => (revealed ? setRevealed(false) : void doReveal())} disabled={finished}>
                {revealed ? "Hide board" : "Reveal board"}
              </button>
            )}
            {blindfold && (
              <span className="muted" aria-live="polite">
                Reveals {legal.reveals_used}/{legal.reveals_budget} free · hints {legal.hints_used}
              </span>
            )}
          </div>
          {finished && <p><strong>Game over.</strong></p>}
        </div>

        <div>
          <section className="card" aria-label="Voice controls">
            <div className="row">
              <button
                className={`ptt ${listening ? "on" : ""}`}
                disabled={!sttSupported || busy}
                onPointerDown={startTalking}
                onPointerUp={stopTalking}
                onPointerLeave={() => listening && stopTalking()}
                onKeyDown={(e) => e.key === "Enter" && (listening ? stopTalking() : startTalking())}
                aria-pressed={listening}
              >
                {listening ? "Listening… release to send" : "Hold to talk (or hold Space)"}
              </button>
              {!sttSupported && <span className="muted">Speech recognition isn't available in this browser — use the text box.</span>}
            </div>
            <form className="row" onSubmit={onText} style={{ marginTop: ".5rem" }}>
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={pending ? "first / second / the move" : quiz ? "Your answer: f3 and c3 · white knight · none" : "Type a move (Nf3) or ask: where is my knight?"}
                aria-label="Type a move"
                disabled={busy}
                style={{ flex: 1 }}
                autoComplete="off"
              />
              <button type="submit" disabled={busy || !text.trim()}>Send</button>
            </form>
            {pending && (
              <div className="row" role="group" aria-label="Confirm move" style={{ marginTop: ".5rem" }}>
                <span>Did you mean:</span>
                {pending.map((m) => (
                  <button key={m.uci} onClick={() => void submit({ san: m.san, uci: m.uci })}>{m.san}</button>
                ))}
                <button onClick={() => setPending(null)}>Cancel</button>
              </div>
            )}
            <VoicePicker />
          </section>

          <section className="card" aria-label="Position questions">
            <div className="row">
              <button onClick={() => (quiz ? (setQuiz(null), say("system", "Question skipped.")) : void startQuiz())}>
                {quiz ? "Skip question" : "Quiz me on this position"}
              </button>
              {tally && (
                <span className="muted" aria-live="polite">
                  Quiz: {tally.correct}/{tally.answered} correct
                  {tally.mean_score != null && <> · mean {Math.round(tally.mean_score * 100)}%</>}
                </span>
              )}
            </div>
            {quiz && <p><strong>Question:</strong> {quiz.prompt}</p>}
            {lastAnswer && <p className="muted">{lastAnswer}</p>}
            {!quiz && !lastAnswer && (
              <p className="muted">Ask by voice or text: “where is my knight”, “what's on e5”, “anything hanging”, “whose move”, “what's the eval”.</p>
            )}
          </section>

          <h2>Live captions</h2>
          <Captions interim={interim} />
          {err && <p className="error" role="alert">{err}</p>}

          {drill && (
            <section className="card" aria-label="Drill mastery">
              <h2 style={{ marginTop: 0 }}>{drill.before.opening_name ?? "Drilled position"}</h2>
              {!drill.after && <p className="muted">Answer from this position — your result updates the mastery schedule.</p>}
              {drill.after && (
                <p>
                  {drill.after.reps > drill.before.reps
                    ? <>Recalled: reps {drill.before.reps} → <strong>{drill.after.reps}</strong>, next due {new Date(drill.after.due_at).toLocaleString()}.</>
                    : <>Missed: this position stays in rotation and is due {new Date(drill.after.due_at).toLocaleString()}.</>}
                </p>
              )}
              <MasteryDelta before={drill.before} after={drill.after} />
              {drill.after && (
                <div className="row" style={{ marginTop: ".5rem" }}>
                  <button className="primary" onClick={() => void nextDrill()}>Next due drill</button>
                  <Link to="/mastery">Back to mastery</Link>
                </div>
              )}
            </section>
          )}

          {last && (
            <section className="card" aria-label="Coach feedback">
              <p>
                Your move <strong>{last.played_san}</strong> <span className="badge">{last.classification}</span>
                {last.opponent_move && <> · opponent replied <strong>{last.opponent_move.san}</strong> <span className="muted">({last.opponent_move.source_tier})</span></>}
              </p>
              <p>{last.coaching.spoken_summary}</p>
              {last.coaching.correction_san && <p>Suggested: <strong>{last.coaching.correction_san}</strong></p>}
              <p className="muted">
                {/stockfish/i.test(last.engine_version) ? last.engine_version : `Stockfish ${last.engine_version}`} · depth {last.engine_depth} · eval (white) {fmtEval(last.eval_before_cp_white)} → {fmtEval(last.eval_after_cp_white)}
                {ev && showBoard && <> · best line: {ev.pv_san.slice(0, 6).join(" ")}</>}
              </p>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
