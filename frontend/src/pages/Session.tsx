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
import { createRecognizer, sttSupported, type Recognizer } from "../voice/speech";
import { SessionGeneration } from "../voice/sessionGeneration";
import { MoveRecovery } from "../voice/moveRecovery";
import { shouldListen } from "../voice/turnPolicy";
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
  const { say, caption, clearCaptions, registerSpeechGate, speechPending, cancelQueuedSpeech } = useVoice();
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
  const [syncPending, setSyncPending] = useState(false);
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const syncPendingRef = useRef(false);
  const authoritativePlayerTurn = useRef(false);
  const [commandBusy, setCommandBusy] = useState(false);
  const commandBusyRef = useRef(false);
  const moveRecovery = useRef(new MoveRecovery());
  const [speechGateClosed, setSpeechGateClosed] = useState(false);
  const speechGateRef = useRef(false);
  const [micError, setMicError] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState<LegalMove[] | null>(null);
  const [interim, setInterim] = useState("");
  const [listening, setListening] = useState(false);
  const [text, setText] = useState("");
  // Position questions (handoff §3.3): the player asks about the board, or answers a quiz about it.
  const [quiz, setQuiz] = useState<QuizQuestion | null>(null);
  const [tally, setTally] = useState<QuestionTally | null>(null);
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);
  // Set when the last move attempt matched no legal move; cleared by the next accepted move.
  const [illegal, setIllegal] = useState<string | null>(null);
  const turnStart = useRef(Date.now());
  const sessionGeneration = useRef(new SessionGeneration());

  const isCurrentSession = useCallback(
    (generation: number) => sessionGeneration.current.isCurrent(generation),
    [],
  );

  const beginCommand = useCallback(() => {
    if (commandBusyRef.current || busyRef.current || syncPendingRef.current) return false;
    commandBusyRef.current = true;
    setCommandBusy(true);
    return true;
  }, []);
  const endCommand = useCallback((generation?: number) => {
    if (generation !== undefined && !sessionGeneration.current.isCurrent(generation)) return;
    commandBusyRef.current = false;
    setCommandBusy(false);
  }, []);

  const legalMoves = useMemo(() => (legal ? buildLegalMoves(legal.legal_moves_san, legal.legal_moves_uci) : []), [legal]);
  const blindfold = sess?.mode === "blindfold";
  const showBoard = !blindfold || revealed;
  const color = sess?.state_json.player_color ?? "white";
  const finished = legal?.status === "finished";
  const myTurn = legal?.next_to_move === "player" && !finished;
  const quiet = !!sess?.settings_json.quiet;
  const quietRef = useRef(false);
  quietRef.current = quiet;

  const refresh = useCallback(async (generation: number) => {
    const [s, l] = await Promise.all([api.getSession(id), api.legal(id)]);
    if (!sessionGeneration.current.isCurrent(generation)) return null;
    setSess(s);
    setLegal(l);
    setFen(l.current_fen);
    authoritativePlayerTurn.current = l.status === "active" && l.next_to_move === "player";
    turnStart.current = Date.now();
    return { s, l };
  }, [id]);

  const retrySynchronization = useCallback(async () => {
    if (!syncPendingRef.current || syncBusy) return;
    const generation = sessionGeneration.current.capture();
    setSyncBusy(true);
    try {
      await moveRecovery.current.retrySynchronization(async () => {
        const result = await refresh(generation);
        if (!result) throw new Error("The session changed before synchronization finished.");
        return result;
      });
      if (!isCurrentSession(generation)) return;
      syncPendingRef.current = false;
      setSyncPending(false);
      setSyncError(null);
    } catch {
      if (!isCurrentSession(generation)) return;
      setSyncError("ChessMind OS still cannot confirm the current position. Retry synchronization before making another move.");
    } finally {
      if (isCurrentSession(generation)) setSyncBusy(false);
    }
  }, [refresh, isCurrentSession, syncBusy]);

  // Async callbacks capture this generation; cleanup invalidates it on unmount or session change.
  useEffect(() => {
    const generation = sessionGeneration.current.activate();
    return () => {
      sessionGeneration.current.invalidate(generation);
      cancelQueuedSpeech();
    };
  }, [id, cancelQueuedSpeech]);

  // Initial load; announce an opening opponent move (player is black).
  useEffect(() => {
    const generation = sessionGeneration.current.capture();
    clearCaptions();
    refresh(generation)
      .then((result) => {
        if (!result || !isCurrentSession(generation)) return;
        const { s } = result;
        const om = s.state_json.last_opponent_move;
        if (!s.settings_json.quiet) {
          void say("system", s.mode === "blindfold" ? "Blindfold session. The board is hidden. Your moves are listening automatically." : "Session started. Make your move on the board, or by voice.");
        } else caption("system", "Quiet mode: only the opponent's moves are spoken.");
        const req = s.state_json.opening_request;
        if (req && !s.settings_json.quiet) caption("system", `Opponent's opening: ${req.label}.`);
        if (om && s.state_json.move_history_san.length === 1) {
          void say("opponent", s.settings_json.quiet ? speakQuietMove(om.san) : `Opponent plays ${speakMove(om.san)}.`);
          if (om.announcement && !s.settings_json.quiet) void say("opening", om.announcement.text);
        }
      })
      .catch((e: Error) => { if (isCurrentSession(generation)) setErr(e.message); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    if (!drillPos || !player) return;
    const generation = sessionGeneration.current.capture();
    api.masteryRow(player.id, drillPos)
      .then((before) => { if (isCurrentSession(generation)) setDrill({ before, after: null }); })
      .catch((e: Error) => { if (isCurrentSession(generation)) setErr(e.message); });
  }, [drillPos, player, isCurrentSession]);

  async function nextDrill() {
    if (!player) return;
    const generation = sessionGeneration.current.capture();
    await api.finish(id).catch(() => {});
    if (!isCurrentSession(generation)) return;
    const n = await api.nextDrill(player.id);
    if (!isCurrentSession(generation)) return;
    if (!n.row || !n.due_now) return nav("/mastery");
    const s = await api.createSession({
      mode: "guided",
      player_color: n.row.fen.split(" ")[1] === "b" ? "black" : "white",
      sampler_mode: "mainline",
      novelty_prob: 0,
      starting_fen: n.row.fen,
    });
    if (!isCurrentSession(generation)) return;
    nav(`/session/${s.id}?drill=${n.row.position_id}`);
  }

  const announce = useCallback(
    async (list: OpeningAnnouncement[], generation: number) => {
      if (!quietRef.current) for (const a of list) {
        if (!isCurrentSession(generation)) return;
        await say("opening", a.text);
        if (!isCurrentSession(generation)) return;
      }
    },
    [say, isCurrentSession],
  );

  /** "Opponent plays X", then the opening it reached (if new), then a note when the requested opening ends. */
  const speakOpponent = useCallback(
    async (om: OpponentMove | null | undefined, label: string | undefined, generation: number) => {
      if (!om || !isCurrentSession(generation)) return;
      const quietNow = quietRef.current;
      await say("opponent", quietNow ? speakQuietMove(om.san) : `Opponent plays ${speakMove(om.san)}.`);
      if (!isCurrentSession(generation)) return;
      if (om.announcement && !quietRef.current) await announce([om.announcement], generation);
      if (!isCurrentSession(generation)) return;
      if (!quietRef.current && om.request_event === "out_of_book" && label) caption("system", `Out of book for the ${label}; the opponent plays normally from here.`);
    },
    [say, caption, announce, isCurrentSession],
  );

  /** Every move — dragged, spoken or typed — goes through the backend, which is the only authority. */
  const submit = useCallback(
    async (m: { san?: string; uci?: string }) => {
      if (busyRef.current || commandBusyRef.current || syncPendingRef.current || !authoritativePlayerTurn.current) return;
      const generation = sessionGeneration.current.capture();
      if (!isCurrentSession(generation)) {
        return;
      }
      busyRef.current = true;
      setBusy(true);
      setErr(null);
      setPending(null);
      let accepted = false;
      let authoritativeRecovered = false;
      try {
        const submission = await moveRecovery.current.submitOnce(
          () => api.move(id, { ...m, latency_ms: Date.now() - turnStart.current }),
          async () => {
            const result = await refresh(generation);
            if (!result) throw new Error("The session changed before synchronization finished.");
            return result;
          },
        );
        if (!submission) return;
        if (!isCurrentSession(generation)) return;
        accepted = true;
        const r = submission.move;
        setLast(r);
        setIllegal(null);
        if (drillPos && player && !drillAnswered.current) {
          // First answer in a drill session is the graded recall of the drilled position.
          drillAnswered.current = true;
          try {
            const after = await api.masteryRow(player.id, drillPos);
            if (!isCurrentSession(generation)) return;
            setDrill((d) => (d ? { ...d, after } : d));
          } catch (e) {
            if (!isCurrentSession(generation)) return;
            setErr((e as Error).message);
          }
        }
        setFen(r.current_fen);
        setRevealed(false);
        let refreshed = submission.state;
        if (!refreshed) {
          try {
            refreshed = await moveRecovery.current.reconcile(async () => {
              const result = await refresh(generation);
              if (!result) throw new Error("The session changed before synchronization finished.");
              return result;
            });
          } catch {
            if (!isCurrentSession(generation)) return;
            syncPendingRef.current = true;
            setSyncPending(true);
            setSyncError("Your move was accepted, but ChessMind OS could not confirm the current position. Retry synchronization before making another move.");
            void rec.current?.setDesired(false, quietRef.current);
            return;
          }
        }
        if (!refreshed || !isCurrentSession(generation)) return;
        authoritativeRecovered = true;
        syncPendingRef.current = false;
        setSyncPending(false);
        setSyncError(null);
        await announce(r.announcements.filter((a) => a.by === "player"), generation);
        if (!isCurrentSession(generation)) return;
        await speakOpponent(r.opponent_move, r.opening_request?.label, generation);
        if (!isCurrentSession(generation)) return;
        if (!quietRef.current && !r.quiet && r.coaching.spoken_summary) await say("coach", r.coaching.spoken_summary);
        if (!isCurrentSession(generation)) return;
        if (r.status === "finished") await say("system", "The game is over.");
        if (!isCurrentSession(generation)) return;
      } catch (e) {
        if (!isCurrentSession(generation)) return;
        if (accepted) {
          setErr(`Move accepted, but the response sequence could not finish: ${(e as Error).message}`);
          return;
        }
        const bad = illegalMoveDetail(e);
        if (bad) {
          // python-chess found no legal move matching the input: say so, keep the position.
          setIllegal(bad.message);
          await say("system", bad.message);
          if (!isCurrentSession(generation)) return;
        } else {
          const msg = (e as Error).message;
          setErr(msg);
          caption("system", `Move rejected: ${msg}`);
        }
        if (!isCurrentSession(generation)) return;
        const refreshed = await refresh(generation).catch(() => null);
        if (!isCurrentSession(generation)) return;
        if (refreshed) moveRecovery.current.finishSequence();
      } finally {
        if (isCurrentSession(generation)) {
          busyRef.current = false;
          setBusy(false);
          if (accepted && authoritativeRecovered) moveRecovery.current.finishSequence();
        }
      }
    },
    [id, say, caption, refresh, drillPos, player, announce, speakOpponent, isCurrentSession],
  );

  const toggleQuiet = useCallback(
    async (on: boolean) => {
      if (!beginCommand()) return;
      const generation = sessionGeneration.current.capture();
      const flip = (q: boolean) => setSess((s) => (s ? { ...s, settings_json: { ...s.settings_json, quiet: q } } : s));
      quietRef.current = on;
      flip(on); // optimistic, so the checkbox follows the click; reverted if the PATCH fails
      if (on) cancelQueuedSpeech();
      try {
        const updated = await api.setQuiet(id, on);
        if (!isCurrentSession(generation)) return;
        setSess(updated);
        if (on) setQuiz(null);
        await say("system", on ? "Quiet mode on. I'll only call out the opponent's moves." : "Quiet mode off. Coaching is back on.");
      } catch (e) {
        if (!isCurrentSession(generation)) return;
        quietRef.current = !on;
        flip(!on);
        setErr((e as Error).message);
      } finally {
        endCommand(generation);
      }
    },
    [id, say, cancelQueuedSpeech, beginCommand, endCommand, isCurrentSession],
  );

  /** "Play the Caro-Kann / d4 against me": parsed and validated server-side, then followed by the sampler. */
  const requestOpening = useCallback(
    async (alts: string[]) => {
      if (!beginCommand()) return;
      const generation = sessionGeneration.current.capture();
      try {
        const r = await api.openingCommand(id, alts);
        if (!isCurrentSession(generation)) return;
        await say("system", r.message);
        if (!isCurrentSession(generation)) return;
        if (r.restarted) {
          setLast(null);
          setIllegal(null);
          setRevealed(false);
        }
        await speakOpponent(r.opponent_move, undefined, generation);
        if (!isCurrentSession(generation)) return;
        await refresh(generation);
      } catch (e) {
        if (!isCurrentSession(generation)) return;
        setErr((e as Error).message);
      } finally {
        endCommand(generation);
      }
    },
    [id, say, speakOpponent, refresh, beginCommand, endCommand, isCurrentSession],
  );

  /** Board question → server answers from python-chess / cached Stockfish; spoken and captioned. */
  const askBoard = useCallback(
    async (alts: string[]) => {
      if (!beginCommand()) return;
      const generation = sessionGeneration.current.capture();
      try {
        const r = await api.ask(id, alts);
        if (!isCurrentSession(generation)) return;
        await say(r.understood ? "coach" : "system", r.answer_text);
        if (!isCurrentSession(generation)) return;
        setLastAnswer(r.answer_text);
      } catch (e) {
        if (!isCurrentSession(generation)) return;
        setErr((e as Error).message);
      } finally {
        endCommand(generation);
      }
    },
    [id, say, beginCommand, endCommand, isCurrentSession],
  );

  const startQuiz = useCallback(async () => {
    if (quietRef.current) return caption("system", "Quiet mode is on: no quizzes. Say \"quiet mode off\" to turn coaching back on.");
    if (!beginCommand()) return;
    const generation = sessionGeneration.current.capture();
    try {
      const q = await api.quiz(id);
      if (!isCurrentSession(generation)) return;
      setQuiz(q);
      setLastAnswer(null);
      await say("coach", q.prompt);
      if (!isCurrentSession(generation)) return;
    } catch (e) {
      if (!isCurrentSession(generation)) return;
      setErr((e as Error).message);
    } finally {
      endCommand(generation);
    }
  }, [id, say, caption, beginCommand, endCommand, isCurrentSession]);

  /** Quiz answer → scored server-side against the board captured when the question was asked. */
  const answerQuiz = useCallback(
    async (q: QuizQuestion, alts: string[]) => {
      if (!beginCommand()) return;
      const generation = sessionGeneration.current.capture();
      try {
        const r = await api.answerQuiz(id, q.question_id, alts);
        if (!isCurrentSession(generation)) return;
        if (!r.understood) {
          await say("system", r.feedback);
          return;
        }
        setQuiz(null);
        setTally(r.tally);
        setLastAnswer(r.feedback);
        await say("coach", r.feedback);
      } catch (e) {
        if (!isCurrentSession(generation)) return;
        setErr((e as Error).message);
      } finally {
        endCommand(generation);
      }
    },
    [id, say, beginCommand, endCommand, isCurrentSession],
  );

  // Mutable view of state for callbacks owned by the recognizer.
  const live = useRef({ legalMoves, pending, myTurn, busy, submit, last, quiz });
  live.current = { legalMoves, pending, myTurn, busy, submit, last, quiz };

  const doReveal = useCallback(async () => {
    if (!beginCommand()) return;
    const generation = sessionGeneration.current.capture();
    try {
      const r = await api.reveal(id, "voice-or-button");
      if (!isCurrentSession(generation)) return;
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
      if (!isCurrentSession(generation)) return;
      setErr((e as Error).message);
    } finally {
      endCommand(generation);
    }
  }, [id, caption, beginCommand, endCommand, isCurrentSession]);

  /** Handle one recognized/typed utterance (all STT alternatives, best first). */
  const handleUtterance = useCallback(
    (alts: string[]) => {
      const L = live.current;
      if (syncPendingRef.current) return;
      // Quiet mode keeps the service armed during Stockfish and opponent selection, but a
      // second transcript during that turn can never trigger another move or narration.
      if (quietRef.current && (busyRef.current || commandBusyRef.current || syncPendingRef.current || moveRecovery.current.isLocked)) return;
      const heard = alts[0];
      caption("you", heard);

      const quietCmd = parseQuietCommand(heard);
      if (quietCmd !== null) return void toggleQuiet(quietCmd);
      if (isOpeningRequest(heard)) return void requestOpening(alts);

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
      if (!L.myTurn || L.busy || busyRef.current || commandBusyRef.current || syncPendingRef.current || moveRecovery.current.isLocked) return say("system", "It is not your turn.");

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
      if (alts.some(looksLikeMove)) {
        // A move was said, but python-chess lists no legal move that matches it here.
        setIllegal("That's an illegal move, try again.");
        return say("system", "That's an illegal move, try again.");
      }
      say("system", `I didn't catch a move in "${heard}". Try again.`);
    },
    [blindfold, caption, say, doReveal, startQuiz, answerQuiz, askBoard, toggleQuiet, requestOpening],
  );
  const onUtterance = useRef(handleUtterance);
  onUtterance.current = handleUtterance;

  // One recognizer manager owns all automatic recognition runs for this session.
  const rec = useRef<Recognizer | null>(null);
  useEffect(() => {
    const generation = sessionGeneration.current.capture();
    let disposed = false;
    const activeSession = () => !disposed && isCurrentSession(generation);
    rec.current = createRecognizer({
      isSessionActive: activeSession,
      onInterim: (text) => { if (activeSession()) setInterim(text); },
      onFinal: (alts) => {
        if (!activeSession()) return;
        setInterim("");
        if (quietRef.current && (busyRef.current || commandBusyRef.current || syncPendingRef.current || moveRecovery.current.isLocked)) return;
        onUtterance.current(alts);
      },
      onState: (state) => {
        if (!activeSession()) return;
        setListening(state);
        if (state) setMicError(null);
      },
      onError: (m, code) => {
        if (!activeSession()) return;
        setMicError(code === "not-allowed" || code === "service-not-allowed"
          ? "Microphone permission is blocked. Allow microphone access, then retry."
          : m);
        caption("system", m);
      },
    });
    return () => {
      disposed = true;
      void rec.current?.stop();
      cancelQueuedSpeech();
      rec.current = null;
    };
  }, [caption, isCurrentSession]);

  // Every speech source in VoiceContext (including VoicePicker) closes this gate synchronously.
  useEffect(() => {
    const generation = sessionGeneration.current.capture();
    return registerSpeechGate(async (blocked) => {
      if (!isCurrentSession(generation)) return;
      speechGateRef.current = blocked;
      setSpeechGateClosed(blocked);
      if (blocked) await rec.current?.setDesired(false, quietRef.current);
    });
  }, [registerSpeechGate, isCurrentSession]);

  // The server-reported turn is authoritative. Normal mode is one-shot and turn-gated;
  // quiet mode stays continuously armed while the player thinks or the backend processes.
  useEffect(() => {
    const desired = shouldListen({
      active: !!sess && !finished && !syncPending,
      quiet,
      playerTurn: myTurn,
      processing: busy || commandBusy,
      speechPending,
      speechGateClosed: speechGateRef.current,
    });
    void rec.current?.setDesired(desired, quiet);
  }, [sess, finished, quiet, myTurn, busy, commandBusy, speechPending, speechGateClosed, syncPending]);

  const retryMicrophone = useCallback(() => {
    setMicError(null);
    rec.current?.retry();
  }, []);

  // Board drag: chess.js only pre-checks so illegal drops snap back; the POST is authoritative.
  function onDrop(from: string, to: string): boolean {
    if (!myTurn || busy || commandBusy || syncPending) return false;
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
            <Board fen={fen} orientation={color} interactive={!!myTurn && !busy && !commandBusy && !syncPending} onDrop={onDrop} />
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
            {syncPending && (
              <div role="alert" className="row">
                <span className="error">{syncError ?? "The accepted move still needs synchronization."}</span>
                <button type="button" onClick={() => void retrySynchronization()} disabled={syncBusy}>
                  {syncBusy ? "Synchronizing…" : "Retry synchronization"}
                </button>
              </div>
            )}
            {sttSupported ? (
              <p className="muted" role="status" aria-live="polite">
                {listening ? "Listening automatically" : speechPending || speechGateClosed ? "Waiting for speech to finish" : busy && quiet ? "Ready for your next turn" : "Microphone paused"}
              </p>
            ) : <p className="muted">Speech recognition isn't available in this browser — use the text box.</p>}
            {micError && (
              <div role="alert" className="row">
                <span className="error">{micError}</span>
                <button type="button" onClick={retryMicrophone}>Retry microphone</button>
              </div>
            )}
            <form className="row" onSubmit={onText} style={{ marginTop: ".5rem" }}>
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={pending ? "first / second / the move" : quiz ? "Your answer: f3 and c3 · white knight · none" : "Type a move (Nf3) or ask: where is my knight?"}
                aria-label="Type a move"
                disabled={busy || commandBusy || syncPending}
                style={{ flex: 1 }}
                autoComplete="off"
              />
              <button type="submit" disabled={busy || commandBusy || syncPending || !text.trim()}>Send</button>
            </form>
            {pending && (
              <div className="row" role="group" aria-label="Confirm move" style={{ marginTop: ".5rem" }}>
                <span>Did you mean:</span>
                {pending.map((m) => (
                  <button key={m.uci} disabled={syncPending} onClick={() => void submit({ san: m.san, uci: m.uci })}>{m.san}</button>
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
            <VoicePicker />
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
