import { Chess } from "chess.js";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  api,
  illegalMoveDetail,
  type LegalMoves,
  type MasteryRow,
  type MoveResult,
  type OpeningAnnouncement,
  type OpponentMove,
  type QuestionTally,
  type QuizQuestion,
  type SessionState,
} from "../api";
import { useAuth } from "../auth";
import { Board } from "../components/Board";
import { Captions } from "../components/Captions";
import { MasteryDelta } from "../components/MasteryDelta";
import { VoicePicker } from "../components/VoicePicker";
import {
  buildLegalMoves,
  isBoardQuestion,
  isOpeningRequest,
  looksLikeMove,
  parseCommand,
  parseQuietCommand,
  resolveAlternatives,
  resolveConfirmation,
  speakMove,
  speakQuietMove,
  type LegalMove,
} from "../voice/resolver";
import { createRecognizer, sttSupported, cancelSpeech } from "../voice/speech";
import { AutomaticMicrophone, type MicrophonePolicy, type MicrophoneStatus } from "../voice/microphone";
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
  const { say, caption, clearCaptions, speechCounts, subscribeSpeech } = useVoice();
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
  const busyRef = useRef(false);
  const moveSubmission = useRef(false);
  const resumeAfterSubmit = useRef(false);
  const [sessionReady, setSessionReady] = useState(false);
  const sessionReadyRef = useRef(false);
  const [micStatus, setMicStatus] = useState<MicrophoneStatus>(sttSupported ? "off" : "unavailable");
  const [micError, setMicError] = useState<string | null>(null);
  const mic = useRef<AutomaticMicrophone | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState<LegalMove[] | null>(null);
  const [interim, setInterim] = useState("");
  const [text, setText] = useState("");
  // Position questions (handoff §3.3): the player asks about the board, or answers a quiz about it.
  const [quiz, setQuiz] = useState<QuizQuestion | null>(null);
  const [tally, setTally] = useState<QuestionTally | null>(null);
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  // Set when the last move attempt matched no legal move; cleared by the next accepted move.
  const [illegal, setIllegal] = useState<string | null>(null);
  const turnStart = useRef(Date.now());

  const legalMoves = useMemo(() => (legal ? buildLegalMoves(legal.legal_moves_san, legal.legal_moves_uci) : []), [legal]);
  const blindfold = sess?.mode === "blindfold";
  const showBoard = !blindfold || revealed;
  const color = sess?.state_json.player_color ?? "white";
  const finished = legal?.status === "finished";
  const myTurn = legal?.next_to_move === "player" && !finished;
  const quiet = !!sess?.settings_json.quiet;
  const quietRef = useRef(false);
  quietRef.current = quiet;
  const micPolicyRef = useRef<MicrophonePolicy>({ enabled: false, quiet: false, anySpeech: false, opponentSpeech: false });

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
      .then(async ({ s }) => {
        const om = s.state_json.last_opponent_move;
        if (s.settings_json.quiet) caption("system", "Quiet mode: only the opponent's moves are spoken.");
        else await say("system", s.mode === "blindfold" ? "Blindfold session. The board is hidden. Say your move when you are ready." : "Session started. Say your move when you are ready.");
        const req = s.state_json.opening_request;
        if (req && !s.settings_json.quiet) caption("system", `Opponent's opening: ${req.label}.`);
        if (om && s.state_json.move_history_san.length === 1) {
          await say("opponent", s.settings_json.quiet ? speakQuietMove(om.san) : `Opponent plays ${speakMove(om.san)}.`);
          if (om.announcement && !s.settings_json.quiet) await say("opening", om.announcement.text);
        }
        sessionReadyRef.current = true;
        setSessionReady(true);
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

  const announce = useCallback(
    async (list: OpeningAnnouncement[]) => {
      if (!quietRef.current) for (const a of list) await say("opening", a.text);
    },
    [say],
  );

  const respond = useCallback(
    (who: "coach" | "system" | "opening", text: string) => {
      if (quietRef.current) {
        caption(who, text);
        return Promise.resolve();
      }
      return say(who, text);
    },
    [say, caption],
  );

  /** The returned SAN is authoritative; quiet mode speaks only its concise verbalization. */
  const speakOpponent = useCallback(
    async (om: OpponentMove | null | undefined, label?: string) => {
      if (!om) return;
      await say("opponent", quietRef.current ? speakQuietMove(om.san) : `Opponent plays ${speakMove(om.san)}.`);
      if (om.announcement && !quietRef.current) await announce([om.announcement]);
      if (om.request_event === "out_of_book" && label && !quietRef.current) caption("system", `Out of book for the ${label}; the opponent plays normally from here.`);
    },
    [say, caption, announce],
  );

  /** Every move — dragged, spoken or typed — goes through the backend, which is the only authority. */
  const submit = useCallback(
    async (m: { san?: string; uci?: string }, fromVoice = false) => {
      if (busyRef.current || moveSubmission.current) return;
      moveSubmission.current = true;
      busyRef.current = true;
      mic.current?.pause();
      setBusy(true);
      setErr(null);
      setPending(null);
      try {
        const r = await api.move(id, { ...m, latency_ms: Date.now() - turnStart.current });
        setLast(r);
        setIllegal(null);
        if (drillPos && player && !drillAnswered.current) {
          // First answer in a drill session is the graded recall of the drilled position.
          drillAnswered.current = true;
          const after = await api.masteryRow(player.id, drillPos);
          setDrill((d) => (d ? { ...d, after } : d));
        }
        setFen(r.current_fen);
        setRevealed(false);
        await announce(r.announcements.filter((a) => a.by === "player"));
        await speakOpponent(r.opponent_move, r.opening_request?.label);
        if (!r.quiet && !quietRef.current && r.coaching.spoken_summary) await say("coach", r.coaching.spoken_summary);
        if (r.status === "finished") await respond("system", "The game is over.");
        await refresh();
      } catch (e) {
        const bad = illegalMoveDetail(e);
        if (bad) {
          // python-chess found no legal move matching the input: say so, keep the position.
          setIllegal(bad.message);
          await respond("system", bad.message);
        } else {
          const msg = (e as Error).message;
          setErr(msg);
          caption("system", `Move rejected: ${msg}`);
        }
        await refresh().catch(() => {});
      } finally {
        busyRef.current = false;
        moveSubmission.current = false;
        if (!fromVoice) resumeAfterSubmit.current = true;
        setBusy(false);
      }
    },
    [id, say, caption, respond, refresh, drillPos, player, announce, speakOpponent],
  );

  const toggleQuiet = useCallback(
    async (on: boolean) => {
      const flip = (q: boolean) => setSess((s) => (s ? { ...s, settings_json: { ...s.settings_json, quiet: q } } : s));
      if (!on) mic.current?.pause(true);
      quietRef.current = on;
      const policy = {
        ...micPolicyRef.current,
        enabled: sessionReadyRef.current && !finished && (on || (myTurn && !busyRef.current)),
        quiet: on,
      };
      micPolicyRef.current = policy;
      mic.current?.update(policy);
      flip(on); // optimistic, so the checkbox follows the click; reverted if the PATCH fails
      try {
        setSess(await api.setQuiet(id, on));
        if (on) setQuiz(null);
        if (on) caption("system", "Quiet mode on. Only opponent moves are spoken.");
        else {
          await say("system", "Quiet mode off. Coaching is back on.");
          mic.current?.resume();
        }
      } catch (e) {
        quietRef.current = !on;
        micPolicyRef.current = { ...micPolicyRef.current, quiet: !on };
        mic.current?.update(micPolicyRef.current);
        flip(!on);
        setErr((e as Error).message);
      }
    },
    [id, say, caption, finished, myTurn],
  );

  /** "Play the Caro-Kann / d4 against me": parsed and validated server-side, then followed by the sampler. */
  const requestOpening = useCallback(
    async (alts: string[]) => {
      try {
        const r = await api.openingCommand(id, alts);
        await respond("system", r.message);
        if (r.restarted) {
          setLast(null);
          setIllegal(null);
          setRevealed(false);
        }
        await speakOpponent(r.opponent_move);
        await refresh();
      } catch (e) {
        setErr((e as Error).message);
      }
    },
    [id, respond, speakOpponent, refresh],
  );

  /** Board question → server answers from python-chess / cached Stockfish; spoken and captioned. */
  const askBoard = useCallback(
    async (alts: string[]) => {
      try {
        const r = await api.ask(id, alts);
        await say(r.understood ? "coach" : "system", r.answer_text);
        setLastAnswer(r.answer_text);
      } catch (e) {
        setErr((e as Error).message);
      }
    },
    [id, say],
  );

  const startQuiz = useCallback(async () => {
    if (quietRef.current) return caption("system", "Quiet mode is on: no quizzes. Say \"quiet mode off\" to turn coaching back on.");
    try {
      const q = await api.quiz(id);
      setQuiz(q);
      setLastAnswer(null);
      await say("coach", q.prompt);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [id, say, caption]);

  /** Quiz answer → scored server-side against the board captured when the question was asked. */
  const answerQuiz = useCallback(
    async (q: QuizQuestion, alts: string[]) => {
      try {
        const r = await api.answerQuiz(id, q.question_id, alts);
        if (!r.understood) return await say("system", r.feedback);
        setQuiz(null);
        setTally(r.tally);
        setLastAnswer(r.feedback);
        await say("coach", r.feedback);
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
    async (alts: string[], fromVoice = false) => {
      if (!fromVoice && !quietRef.current) mic.current?.pause();
      try {
        const L = live.current;
        const heard = alts[0];
        caption("you", heard);

        const quietCmd = parseQuietCommand(heard);
        if (quietCmd !== null) return await toggleQuiet(quietCmd);
        if (quietRef.current && (!L.myTurn || L.busy || busyRef.current)) return;
        if (isOpeningRequest(heard)) return await requestOpening(alts);

        const cmd = parseCommand(heard);
        if (cmd === "reveal" && blindfold) return await doReveal();
        if (quietRef.current && (cmd !== null || L.quiz || isBoardQuestion(heard))) return;
        if (cmd === "repeat") {
          const t = L.quiz ? L.quiz.prompt : L.last?.opponent_move ? `Opponent played ${speakMove(L.last.opponent_move.san)}.` : "Nothing to repeat yet.";
          return await respond("system", t);
        }
        if (cmd === "quiz") return await startQuiz();
        if (L.quiz) {
          if (/\b(skip|cancel|never ?mind)\b/i.test(heard)) {
            setQuiz(null);
            return await respond("system", "Question skipped.");
          }
          return await answerQuiz(L.quiz, alts);
        }
        if (isBoardQuestion(heard)) return await askBoard(alts);
        if (moveSubmission.current || busyRef.current) return;
        if (!L.myTurn || L.busy) return await respond("system", "It is not your turn.");

        if (L.pending) {
          let outcome = resolveConfirmation(heard, L.pending);
          for (let i = 1; outcome.kind === "unclear" && i < alts.length; i++) outcome = resolveConfirmation(alts[i], L.pending);
          if (outcome.kind === "chosen") return await L.submit({ san: outcome.move.san, uci: outcome.move.uci }, fromVoice);
          if (outcome.kind === "cancel") {
            setPending(null);
            return await respond("system", "Cancelled. Say your move again.");
          }
          if (outcome.kind === "narrowed") {
            setPending(outcome.moves);
            return await respond("system", `Still ambiguous: ${outcome.moves.map((m) => speakMove(m.san)).join(", or ")}. Which one?`);
          }
          return await respond("system", `Say first, second, or the move. Options: ${L.pending.map((m) => speakMove(m.san)).join(", or ")}.`);
        }

        const r = resolveAlternatives(alts, L.legalMoves);
        if (r.kind === "match") return await L.submit({ san: r.move.san, uci: r.move.uci }, fromVoice);
        if (r.kind === "ambiguous") {
          setPending(r.moves);
          return await respond("system", `More than one move fits. Did you mean ${r.moves.map((m) => speakMove(m.san)).join(", or ")}?`);
        }
        if (alts.some(looksLikeMove)) {
          setIllegal("That's an illegal move, try again.");
          return await respond("system", "That's an illegal move, try again.");
        }
        await respond("system", `I didn't catch a move in "${heard}". Try again.`);
      } finally {
        if (!fromVoice && !moveSubmission.current && !resumeAfterSubmit.current && !quietRef.current) mic.current?.resume();
      }
    },
    [blindfold, caption, respond, doReveal, startQuiz, answerQuiz, askBoard, toggleQuiet, requestOpening],
  );
  const onUtterance = useRef(handleUtterance);
  onUtterance.current = handleUtterance;

  const speechActive = speechCounts.you + speechCounts.opponent + speechCounts.coach + speechCounts.opening + speechCounts.system > 0;
  const microphonePolicy = useMemo<MicrophonePolicy>(() => ({
    enabled: sessionReady && !finished && (quiet || (myTurn && !busy)),
    quiet,
    anySpeech: speechActive,
    opponentSpeech: speechCounts.opponent > 0,
  }), [sessionReady, finished, quiet, myTurn, busy, speechActive, speechCounts.opponent]);
  micPolicyRef.current = microphonePolicy;

  useEffect(() => {
    const controller = new AutomaticMicrophone(
      createRecognizer,
      (alts) => {
        setInterim("");
        return onUtterance.current(alts, true);
      },
      (status) => {
        setMicStatus(status);
        if (status === "listening") setMicError(null);
      },
      (message, denied) => setMicError(denied ? "Microphone permission was denied. Allow microphone access for this site, then reload the session." : message),
      setInterim,
    );
    mic.current = controller;
    const unsubscribe = subscribeSpeech((counts) => {
      const total = counts.you + counts.opponent + counts.coach + counts.opening + counts.system;
      controller.update({ ...micPolicyRef.current, anySpeech: total > 0, opponentSpeech: counts.opponent > 0 });
    });
    controller.update(micPolicyRef.current);
    return () => {
      unsubscribe();
      controller.dispose();
      cancelSpeech();
      mic.current = null;
    };
  }, [subscribeSpeech]);

  useEffect(() => {
    mic.current?.update(microphonePolicy);
    if (resumeAfterSubmit.current && !busy) {
      resumeAfterSubmit.current = false;
      mic.current?.resume();
    }
  }, [microphonePolicy, busy]);

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
  const opponentSpeaking = speechCounts.opponent > 0;
  const applicationSpeaking = speechActive && (!quiet || opponentSpeaking);
  const microphoneMessage = !sttSupported
    ? "Microphone unavailable in this browser; use the text box."
    : micStatus === "denied"
      ? "Microphone permission denied. Use the text box or allow microphone access and reload."
      : micStatus === "unavailable"
        ? "Speech recognition is unavailable; use the text box."
        : finished
          ? "Session complete · microphone off"
        : applicationSpeaking
          ? quiet ? "Opponent speaking · microphone paused" : "ChessMind is speaking or coaching · microphone paused"
          : !quiet && busy
            ? "Processing your move · microphone paused"
            : micStatus === "listening"
              ? "Listening for your move"
              : sessionReady ? "Preparing microphone…" : "Starting session…";

  return (
    <>
      <h1>
        {drillPos
          ? "Mastery drill"
          : sess.settings_json.sampler_mode === "opponent_twin"
            ? blindfold ? "Blindfold vs the twin" : "Playing the twin"
            : blindfold ? "Blindfold session" : "Guided session"}{" "}
        <span className="badge">{color}</span>
        {quiet && <> <span className="badge">quiet</span></>}
      </h1>
      <div className="grid">
        <div>
          {illegal && (
            <p className="illegal" role="alert" aria-label="Illegal move">
              <strong>Illegal move — try again.</strong> No legal move matches that; the position has not changed.
            </p>
          )}
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
          <p className="muted" aria-label="Opening">
            {!quiet && sess.state_json.opening && <>Opening: <strong>{sess.state_json.opening.eco} {sess.state_json.opening.name}</strong></>}
            {sess.state_json.opening_request && (
              <> · Opponent's opening: <strong>{sess.state_json.opening_request.label}</strong>{" "}
                <span className="badge">{sess.state_json.opening_request.status.replace(/_/g, " ")}</span></>
            )}
          </p>
        </div>

        <div>
          <section className="card" aria-label="Voice controls">
            <p className={`mic-status ${micStatus === "denied" || micStatus === "unavailable" ? "error" : ""}`} role="status" aria-live="polite" aria-label="Microphone status" data-state={micStatus}>
              {microphoneMessage}
            </p>
            {micError && micStatus !== "denied" && <p className="muted" role="status">{micError}</p>}
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
            <div className="row" style={{ marginTop: ".5rem" }}>
              <label>
                <input type="checkbox" checked={quiet} onChange={(e) => void toggleQuiet(e.target.checked)} aria-label="Quiet mode" />{" "}
                Quiet mode <span className="muted">(only the opponent's moves; no coaching or quizzes)</span>
              </label>
            </div>
            <p className="muted" style={{ marginBottom: 0 }}>
              Set the opponent's opening: say “play the Caro-Kann against me” or “play d4 against me”.
            </p>
            <VoicePicker quiet={quiet} />
          </section>

          {!quiet && <section className="card" aria-label="Position questions">
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
          </section>}

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

          {last && last.quiet && (
            <section className="card" aria-label="Last moves">
              <p>
                Your move <strong>{last.played_san}</strong>
                {last.opponent_move && <> · opponent replied <strong>{last.opponent_move.san}</strong></>}
              </p>
            </section>
          )}

          {last && !last.quiet && (
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
                {" "}· wording: {last.coaching.explanation_source?.startsWith("llm:") ? `LLM (${last.coaching.explanation_source.slice(4)}), checked against the engine facts` : "deterministic template"}
              </p>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
