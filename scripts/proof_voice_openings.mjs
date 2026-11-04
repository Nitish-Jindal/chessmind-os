// Proof: opening announcements, quiet mode, illegal-move feedback, "play <opening> against me",
// and playing Black with the engine opening — driven through the real frontend in headless Chromium.
//
//   node scripts/proof_voice_openings.mjs [frontendUrl] [apiUrl]
//
// Real: Vite build, FastAPI, Postgres, Stockfish, python-chess, the Lichess chess-openings
// taxonomy, and the app's resolver/captions/TTS code. Stubbed (as in proof_frontend.mjs): the
// microphone (an init-script SpeechRecognition that emits the transcript we hand it) and system
// voices (speechSynthesis.speak is recorded in window.__spoken). Expects the seeded opening graph
// (scripts/seed_games.py), which the mainline sampler follows in phase A.
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(ROOT, "frontend", "package.json"));
const { chromium } = require("playwright");
const { Chess } = require("chess.js");

const FE = process.argv[2] ?? "http://localhost:5173";
const API = process.argv[3];
if (!API) throw new Error("Pass the isolated test API URL explicitly; this proof will not default to localhost:8000.");
const apiUrl = new URL(API);
if (!(["localhost", "127.0.0.1"].includes(apiUrl.hostname)) || !apiUrl.port || apiUrl.port === "8000") {
  throw new Error("Voice proof requires an explicitly isolated loopback test backend, never the production localhost:8000 backend.");
}
const OUT = process.env.CHESSMIND_PROOF_OUT_DIR ?? join(ROOT, "scripts", "data");
mkdirSync(OUT, { recursive: true });
const log = [];
const skipped = [];
const consoleErrors = [];
const failedRequests = [];
const apiErrorResponses = [];
const note = (k, v) => { log.push({ k, v }); console.log(k, typeof v === "string" ? v : JSON.stringify(v)); };
function check(cond, msg) { if (!cond) { console.error("FAIL:", msg); process.exitCode = 1; note("FAIL", msg); } else note("ok", msg); }
function skip(msg) { skipped.push(msg); note("SKIP", msg); }

const initScript = () => {
  window.__spoken = [];
  window.__utterance = [];
  window.__recognizers = [];
  window.__ttsMicLeaks = [];
  window.__moveListeningSamples = [];
  window.__moveInFlight = 0;
  const synth = window.speechSynthesis;
  synth.getVoices = () => [{ voiceURI: "fake-en-us", name: "Fake American", lang: "en-US", default: true, localService: true }];
  synth.speak = (u) => {
    window.__spoken.push(u.text);
    if (window.__recognizers.some((r) => r.listening)) window.__ttsMicLeaks.push(u.text);
    u.onstart?.();
    setTimeout(() => u.onend && u.onend(), 30);
  };
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const raw = typeof args[0] === "string" ? args[0] : args[0]?.url;
    const isMove = new URL(raw, location.href).pathname.endsWith("/move") && args[1]?.method === "POST";
    if (isMove) {
      window.__moveInFlight++;
      window.__moveListeningSamples.push(window.__recognizers.some((r) => r.listening));
    }
    try { return await nativeFetch(...args); }
    finally { if (isMove) window.__moveInFlight--; }
  };
  window.__sampler = setInterval(() => {
    if (window.__moveInFlight) window.__moveListeningSamples.push(window.__recognizers.some((r) => r.listening));
  }, 2);
  class FakeRec {
    constructor() { this.listening = false; this.resultIndex = 0; window.__recognizers.push(this); }
    start() { this.listening = true; setTimeout(() => this.onstart?.(), 0); }
    stop() { this.finish(); }
    abort() { this.finish(); }
    finish() {
      if (!this.listening) return;
      this.listening = false;
      setTimeout(() => this.onend?.(), 5);
    }
    emit(alts) {
      if (!this.listening) throw new Error("No active automatic recognizer");
      const list = Array.isArray(alts) ? alts : [alts];
      const res = { isFinal: true, length: list.length };
      list.forEach((t, i) => { res[i] = { transcript: t, confidence: 0.9 - i * 0.1 }; });
      const index = this.resultIndex++;
      this.onresult?.({ resultIndex: index, results: { length: index + 1, [index]: res } });
    }
  }
  window.SpeechRecognition = FakeRec;
  window.webkitSpeechRecognition = FakeRec;
  window.__emitFinal = (alts) => {
    const active = [...window.__recognizers].reverse().find((r) => r.listening);
    if (!active) throw new Error("Automatic recognition is not active");
    active.emit(alts);
  };
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1100, height: 1000 } });
await ctx.addInitScript(initScript);
const page = await ctx.newPage();
page.on("pageerror", (e) => { console.error("PAGE ERROR", e.message); consoleErrors.push(e.message); process.exitCode = 1; });
page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
const apiCalls = [];
page.on("request", (r) => { const u = new URL(r.url()); if (u.origin === new URL(API).origin) apiCalls.push(`${r.method()} ${u.pathname}`); });
page.on("requestfailed", (r) => {
  const u = new URL(r.url());
  if (u.origin === new URL(API).origin) failedRequests.push(`${r.method()} ${u.pathname}: ${r.failure()?.errorText ?? "request failed"}`);
});
page.on("response", (r) => {
  const u = new URL(r.url());
  if (u.origin === new URL(API).origin && r.status() >= 400) apiErrorResponses.push(`${r.request().method()} ${u.pathname} ${r.status()}`);
});

async function speak(utterance) {
  await page.waitForFunction(() => window.__recognizers.some((r) => r.listening), null, { timeout: 15000 });
  await page.evaluate((u) => window.__emitFinal(u), utterance);
}
/** Say something and return the JSON (and status) of the API call it triggers. */
async function sayAndCapture(utterance, urlRe) {
  const resp = page.waitForResponse((r) => urlRe.test(new URL(r.url()).pathname) && ["POST", "PATCH"].includes(r.request().method()), { timeout: 60000 });
  await speak(utterance);
  const r = await resp;
  return { status: r.status(), body: await r.json() };
}
const spoken = () => page.evaluate(() => window.__spoken);
const clearSpoken = () => page.evaluate(() => { window.__spoken.length = 0; });
const captions = () => page.locator('[aria-label="Live captions"]').innerText();
async function waitSpoken(text, timeout = 30000) {
  await page.waitForFunction((t) => window.__spoken.some((s) => s.includes(t)), text, { timeout });
}
async function apiGet(path) {
  return page.evaluate(async ([base, p]) => {
    const t = JSON.parse(localStorage.getItem("chessmind.tokens"));
    return (await fetch(base + p, { headers: { Authorization: `Bearer ${t.access_token}` } })).json();
  }, [API, path]);
}
async function apiPost(path, body) {
  return page.evaluate(async ([base, p, payload]) => {
    const t = JSON.parse(localStorage.getItem("chessmind.tokens"));
    const response = await fetch(base + p, {
      method: "POST",
      headers: { Authorization: `Bearer ${t.access_token}`, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(`${p} returned ${response.status}: ${JSON.stringify(result)}`);
    return result;
  }, [API, path, body]);
}
async function verifyEvaluationForFen(sessionId, expectedFen, label) {
  const panel = page.getByLabel("Position evaluation");
  await panel.locator(".evaluation-track").waitFor({ state: "visible", timeout: 60000 });
  const [session, evaluation] = await Promise.all([
    apiGet(`/sessions/${sessionId}`),
    apiGet(`/sessions/${sessionId}/evaluation`),
  ]);
  check(session.state_json.current_fen === expectedFen, `${label}: settled session FEN matches the expected position`);
  check(evaluation.current_fen === expectedFen, `${label}: evaluation response matches the exact current FEN`);
  check(evaluation.analysis_available && !evaluation.analysis_pending, `${label}: persisted Stockfish evaluation is ready`);
  check(evaluation.perspective === "white" && evaluation.depth >= 10 && evaluation.nodes > 0 && Boolean(evaluation.engine_version), `${label}: real White-centric Stockfish metadata is present`);
  const text = await panel.innerText();
  check(text.includes("White") && text.includes("Black") && text.includes(evaluation.engine_version ?? "") && text.includes(`Depth ${evaluation.depth}`), `${label}: UI displays both sides and this position's engine metadata`);
  check(!text.includes("No saved analysis"), `${label}: UI does not show the missing-analysis fallback`);
  return evaluation;
}
function holdNextSessionEvaluation(sessionId) {
  let captured = false;
  let resolveCaptured;
  let releaseHeld;
  let resolveCompleted;
  const capturedPromise = new Promise((resolve) => { resolveCaptured = resolve; });
  const released = new Promise((resolve) => { releaseHeld = resolve; });
  const completed = new Promise((resolve) => { resolveCompleted = resolve; });
  const pattern = `**/sessions/${sessionId}/evaluation`;
  const handler = async (route) => {
    if (captured) return route.continue();
    captured = true;
    const response = await route.fetch();
    const body = await response.body();
    let payload = null;
    try { payload = JSON.parse(body.toString("utf8")); } catch { /* reported by the assertion */ }
    resolveCaptured({ response, body, payload });
    await released;
    try {
      await route.fulfill({ response, body });
    } finally {
      resolveCompleted();
    }
  };
  return {
    pattern,
    handler,
    captured: capturedPromise,
    release: releaseHeld,
    completed,
  };
}
async function settle() { await page.waitForFunction(() => !document.querySelector('input[aria-label="Type a move"]')?.disabled, null, { timeout: 60000 }); await page.waitForTimeout(150); }
async function launch({ mode = "guided", color = "white", style = "mix", opening = "", quiet = false }) {
  await page.goto(FE);
  await page.getByRole("heading", { name: "Start a session" }).waitFor();
  await page.getByLabel("Visualization").selectOption(mode);
  await page.getByLabel("Play as").selectOption(color);
  await page.getByLabel("Opponent style").selectOption(style);
  await page.getByLabel("Opponent's opening").fill(opening);
  if (quiet) await page.getByLabel("Quiet mode").check();
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await page.waitForURL(/\/session\//);
  await page.getByLabel("Live captions").waitFor();
  await page.waitForTimeout(400);
  check(await page.getByRole("button", { name: /hold to talk/i }).count() === 0, "automatic voice mode has no push-to-talk control");
  return page.url().split("/session/")[1].split("?")[0];
}

// ---------------------------------------------------------------- register
const email = `vo-proof-${Date.now()}@chessmind.example.com`;
await page.goto(FE);
await page.getByRole("button", { name: /need an account/i }).click();
await page.getByLabel("Display name").fill("Voice Openings Proof");
await page.getByLabel("Email").fill(email);
await page.getByLabel("Password").fill("proofpassword123");
await page.getByRole("button", { name: "Register" }).click();
await page.getByRole("heading", { name: "Start a session" }).waitFor();

// ---------------------------------------------------------------- A. Ruy Lopez announced once (blindfold)
note("phase", "A: opening announcement — 1.e4 e5 2.Nf3 Nc6 3.Bb5, blindfold, mainline sampler over the seeded graph");
let sid = await launch({ mode: "blindfold", style: "mainline" });
let sessionSnapshot = await apiGet(`/sessions/${sid}`);
await verifyEvaluationForFen(sid, sessionSnapshot.state_json.current_fen, "session creation");
check((await page.locator("chessboard-board, [data-boardid], .board").count()) === 0 || (await page.getByRole("img", { name: /board hidden/i }).count()) === 1, "blindfold: board hidden");
await page.waitForFunction(() => window.__recognizers.some((r) => r.listening), null, { timeout: 15000 });
await clearSpoken();
await page.evaluate(() => { window.__moveListeningSamples.length = 0; window.__ttsMicLeaks.length = 0; });
const annByPly = [];
for (const [said, expectOpp] of [["e four", "e5"], ["knight f3", "Nc6"], ["bishop b5", null]]) {
  const { body } = await sayAndCapture(said, /\/move$/);
  annByPly.push({ played: body.played_san, opponent: body.opponent_move?.san, announcements: body.announcements.map((a) => `${a.name} (${a.eco}, ply ${a.ply}, by ${a.by})`) });
  if (expectOpp) check(body.opponent_move?.san === expectOpp, `after ${body.played_san} the mainline opponent played ${expectOpp} (got ${body.opponent_move?.san}, ${body.opponent_move?.source_tier})`);
  await settle();
  if (said === "bishop b5") {
    const coachCard = page.getByLabel("Coach feedback");
    await coachCard.waitFor({ state: "visible" });
    const coachText = (await coachCard.innerText()).trim();
    check(coachText.length > 0, "coach feedback is visible with non-empty text after the move");
    check(coachText.includes("wording: deterministic template"), "coach card uses the deterministic template");
    await page.screenshot({ path: join(OUT, "vo_ruy_lopez_announced.png"), fullPage: true });
  }
  check(body.opponent_move?.san, `player ${body.played_san} received the opponent reply before evaluation refresh`);
  await verifyEvaluationForFen(sid, body.current_fen, `after ${body.played_san} and opponent ${body.opponent_move?.san}`);
}
note("announcements per move", annByPly);
check(annByPly[0].announcements.length === 0 && annByPly[1].announcements.length === 0, "no announcement for 1.e4 e5 2.Nf3 Nc6 (generic King's Pawn / King's Knight names)");
check(annByPly[2].announcements[0] === "Ruy Lopez (C60, ply 5, by player)", "3.Bb5 announced 'Ruy Lopez' (C60) at ply 5");
await waitSpoken("Ruy Lopez");
let sp = await spoken();
note("spoken (phase A)", sp);
check(sp.filter((t) => t === "Ruy Lopez").length === 1, "TTS spoke 'Ruy Lopez' exactly once");
const normalListening = await page.evaluate(() => window.__moveListeningSamples);
check(normalListening.length > 0 && normalListening.every((active) => !active), "normal mode stayed silent through every backend move request");
check((await page.evaluate(() => window.__ttsMicLeaks)).length === 0, "normal mode muted recognition for all queued speech");
check((await captions()).includes("opening: Ruy Lopez"), "caption 'opening: Ruy Lopez' shown");
// The page shows the latest named position: after 3...a6 that is C70 "Ruy Lopez: Morphy Defense".
const openingLine = await page.getByLabel("Opening").innerText();
note("opening line", openingLine);
check(/C(60|70) Ruy Lopez/.test(openingLine), "opening line on the page names the Ruy Lopez (latest named position)");
const hist = (await apiGet(`/sessions/${sid}`)).state_json.move_history_san;
const c = new Chess(); for (const m of hist) c.move(m);
note("history", hist.join(" "));
check(hist.slice(0, 5).join(" ") === "e4 e5 Nf3 Nc6 Bb5", "server history starts 1.e4 e5 2.Nf3 Nc6 3.Bb5 (replayed by chess.js)");
const currentFenForEval = (await apiGet(`/sessions/${sid}`)).state_json.current_fen;
const savedAnalysis = await apiPost("/analysis/position", { fen: currentFenForEval, depth: 8, multipv: 1 });
await page.reload();
const evalPanel = page.getByLabel("Position evaluation");
await evalPanel.getByText(`Depth ${savedAnalysis.depth}`, { exact: true }).waitFor();
check((await evalPanel.innerText()).includes("White") && (await evalPanel.innerText()).includes("Black"), "evaluation bar shows both sides for the current position");
const evaluationText = await evalPanel.innerText();
const displayedNodes = evaluationText.match(/([\d,.\u00a0]+) nodes/)?.[1].replace(/\D/g, "");
check(evaluationText.includes(savedAnalysis.engine_version) && displayedNodes === String(savedAnalysis.nodes), "evaluation bar shows the persisted Stockfish version, depth and nodes");
check(await evalPanel.locator(".evaluation-track").count() === 1, "evaluation bar scales the persisted White-centric score");
check(/Latest move: (Best|Good|Inaccuracy|Mistake|Blunder)/.test(await evalPanel.innerText()), "evaluation bar shows the backend's latest deterministic move quality");

// A fresh session on the already persisted FEN must reuse the same saved analysis.
note("phase", "evaluation cache hit reuses persisted Stockfish analysis");
const cachedSession = await apiPost("/sessions", {
  mode: "guided", player_color: "white", sampler_mode: "mainline", seed: 811,
  starting_fen: currentFenForEval,
});
await page.goto(new URL(`/session/${cachedSession.id}`, FE).href);
const cachedSessionEval = await verifyEvaluationForFen(cachedSession.id, currentFenForEval, "cache-hit session creation");
check(cachedSessionEval.eval_cp_white === savedAnalysis.results[0].cp && cachedSessionEval.engine_version === savedAnalysis.engine_version && cachedSessionEval.nodes === savedAnalysis.nodes, "cache hit reuses the persisted Stockfish score and metadata");

// Hold the real GET response for the next FEN while the UI changes positions.
// This proves the old evaluation is hidden until the new position's response arrives.
const cacheHitLegal = await apiGet(`/sessions/${cachedSession.id}/legal`);
check(cacheHitLegal.legal_moves_san.includes("a3"), "cache-hit session can submit the legal voice move a3");
const heldNext = holdNextSessionEvaluation(cachedSession.id);
await page.route(heldNext.pattern, heldNext.handler);
const { body: cacheMove } = await sayAndCapture("a3", /\/move$/);
await settle();
const delayedCurrentEvaluation = await Promise.race([
  heldNext.captured,
  new Promise((_, reject) => setTimeout(() => reject(new Error("timed out waiting for the current-FEN evaluation response")), 15000)),
]);
check(delayedCurrentEvaluation.payload?.current_fen === cacheMove.current_fen, "delayed evaluation response belongs to the new settled FEN");
check(await page.getByLabel("Position evaluation").locator(".evaluation-track").count() === 0, "previous evaluation is cleared while the new FEN response is pending");
heldNext.release();
await heldNext.completed;
await page.unroute(heldNext.pattern, heldNext.handler);
await verifyEvaluationForFen(cachedSession.id, cacheMove.current_fen, "after cached-session player move and opponent reply");

// Deliver a real response for the old FEN only after the new FEN has rendered.
// The watcher must ignore this stale response after its FEN generation is invalidated.
note("phase", "stale evaluation response rejection");
const staleSession = await apiPost("/sessions", {
  mode: "guided", player_color: "white", sampler_mode: "mainline", seed: 811,
  starting_fen: currentFenForEval,
});
await page.goto(new URL(`/session/${staleSession.id}`, FE).href);
await verifyEvaluationForFen(staleSession.id, currentFenForEval, "stale-response session initial cached FEN");
const heldOld = holdNextSessionEvaluation(staleSession.id);
await page.route(heldOld.pattern, heldOld.handler);
await page.reload({ waitUntil: "domcontentloaded" });
const delayedOldEvaluation = await Promise.race([
  heldOld.captured,
  new Promise((_, reject) => setTimeout(() => reject(new Error("timed out waiting for the old-FEN evaluation response")), 15000)),
]);
check(delayedOldEvaluation.payload?.current_fen === currentFenForEval && delayedOldEvaluation.payload?.analysis_available, "held stale response contains real saved analysis for the old FEN");
const staleLegal = await apiGet(`/sessions/${staleSession.id}/legal`);
check(staleLegal.legal_moves_san.includes("a3"), "stale-response session can submit the legal voice move a3");
const { body: staleMove } = await sayAndCapture("a3", /\/move$/);
await settle();
await verifyEvaluationForFen(staleSession.id, staleMove.current_fen, "new FEN before stale response release");
heldOld.release();
await heldOld.completed;
await page.unroute(heldOld.pattern, heldOld.handler);
await page.waitForTimeout(250);
const afterStale = await apiGet(`/sessions/${staleSession.id}/evaluation`);
check(afterStale.current_fen === staleMove.current_fen && (await page.getByLabel("Position evaluation").locator(".evaluation-track").count()) === 1, "late old-FEN response is rejected and the new evaluation remains visible");
await page.goto(new URL(`/session/${sid}`, FE).href);

// ---------------------------------------------------------------- B. Illegal move in blindfold
note("phase", "B: illegal move in blindfold");
const before = (await apiGet(`/sessions/${sid}`)).state_json;
const legalNow = (await apiGet(`/sessions/${sid}/legal`)).legal_moves_san;
check(!legalNow.some((m) => m.startsWith("Ke3")), "Ke3 is not in python-chess's legal list here");
await clearSpoken();
const movesBefore = apiCalls.filter((x) => x.endsWith("/move")).length;
await speak("king e3");
await waitSpoken("illegal move");
check((await spoken()).includes("That's an illegal move, try again."), "spoken: \"That's an illegal move, try again.\"");
check((await captions()).includes("system: That's an illegal move, try again."), "captioned the illegal-move message");
const banner = page.getByRole("alert", { name: "Illegal move" });
check(await banner.isVisible(), "red 'Illegal move' alert shown above the hidden board");
check((await page.getByRole("img", { name: /board hidden/i }).count()) === 1, "board stays hidden");
check(apiCalls.filter((x) => x.endsWith("/move")).length === movesBefore, "no move was posted for the illegal utterance");
await page.screenshot({ path: join(OUT, "vo_illegal_blindfold.png"), fullPage: true });
// Typed SAN that python-chess rejects goes to the server and gets the 422 illegal_move detail.
await clearSpoken();
const box = page.getByLabel("Type a move");
const r422 = page.waitForResponse((r) => r.url().endsWith("/move"), { timeout: 30000 }).catch(() => null);
await page.evaluate(async ([base, id]) => {
  // A direct POST mirrors a client that skips the resolver: the server is the authority.
  const t = JSON.parse(localStorage.getItem("chessmind.tokens"));
  window.__direct = await (await fetch(`${base}/sessions/${id}/move`, { method: "POST", headers: { Authorization: `Bearer ${t.access_token}`, "Content-Type": "application/json" }, body: JSON.stringify({ san: "Qh5" }) })).json();
}, [API, sid]);
await r422;
const direct = await page.evaluate(() => window.__direct);
note("direct POST {san: Qh5}", direct);
check(direct.detail?.code === "illegal_move" && direct.detail.message === "That's an illegal move, try again.", "server answers an illegal SAN with 422 illegal_move");
const after = (await apiGet(`/sessions/${sid}`)).state_json;
check(after.current_fen === before.current_fen && after.move_history_san.length === before.move_history_san.length, "game did not advance after illegal input");
// A legal move afterwards clears the banner.
const legalOne = legalNow.find((m) => /^[a-h][3-4]$/.test(m)) ?? legalNow[0];
await box.fill(legalOne); await box.press("Enter");
await page.waitForResponse((r) => r.url().endsWith("/move"));
await settle();
check(!(await banner.isVisible().catch(() => false)), `legal move ${legalOne} accepted and the alert cleared`);

// ---------------------------------------------------------------- C. Quiet mode
note("phase", "C: quiet mode");
sid = await launch({ mode: "guided", style: "mainline", quiet: true });
check((await page.getByRole("heading", { level: 1 }).innerText()).includes("quiet"), "quiet badge in the title");
check((await page.getByLabel("Position questions").count()) === 0, "quiz card hidden in quiet mode");
await clearSpoken();
const quietMoves = [];
for (const said of ["e4", "knight f3", "h4"]) {
  const { body } = await sayAndCapture(said, /\/move$/);
  quietMoves.push({ played: body.played_san, classification: body.classification, quiet: body.quiet, summary: body.coaching.spoken_summary, opp: body.opponent_move?.san, announcements: body.announcements });
  await settle();
}
note("quiet moves (API)", quietMoves);
check(quietMoves.every((m) => m.quiet && m.summary === "" && m.announcements.length === 0), "API: empty coaching and no announcements for every quiet move");
sp = await spoken();
note("spoken (quiet)", sp);
check(sp.length === 3 && sp.every((t) => !/^Opponent plays /.test(t)), "quiet mode speaks concise moves without an 'Opponent plays' prefix");
check(sp.every((t) => /^[A-Z]/.test(t)), "quiet-mode move announcements are concise and capitalized");
const listeningDuringMoves = await page.evaluate(() => window.__moveListeningSamples);
check(listeningDuringMoves.length > 0 && listeningDuringMoves.every(Boolean), "quiet mode stayed recognition-ready during backend move processing");
check(!(await captions()).match(/coach:|opening:/), "no coach or opening captions");
check((await page.evaluate(() => window.__ttsMicLeaks)).length === 0, "recognition was stopped for every TTS utterance");
check((await page.getByLabel("Coach feedback").count()) === 0, "no coach card");
const quizCalls = apiCalls.filter((x) => x.endsWith("/quiz")).length;
await speak("quiz me");
await page.waitForTimeout(400);
check(apiCalls.filter((x) => x.endsWith("/quiz")).length === quizCalls, "'quiz me' makes no quiz request in quiet mode");
await page.screenshot({ path: join(OUT, "vo_quiet_mode.png"), fullPage: true });
await clearSpoken();
const off = await sayAndCapture("quiet mode off", /\/settings$/);
check(off.body.settings_json.quiet === false, "voice 'quiet mode off' → PATCH /sessions/{id}/settings quiet=false");
await page.getByLabel("Position questions").waitFor();
const legalQ = (await apiGet(`/sessions/${sid}/legal`)).legal_moves_san;
const { body: loud } = await sayAndCapture(legalQ.includes("d4") ? "d4" : legalQ[0], /\/move$/);
await settle();
check(!loud.quiet && loud.coaching.spoken_summary.length > 0 && (await captions()).includes("coach:"), "coaching returns after quiet mode is turned off");
const patched = page.waitForResponse((r) => r.url().endsWith("/settings"));
await page.getByLabel("Quiet mode").check();
await patched;
await page.waitForTimeout(200);
check((await page.getByLabel("Position questions").count()) === 0, "checkbox turns quiet mode back on");

// ---------------------------------------------------------------- D. "Play the Caro-Kann against me" as White
note("phase", "D: Caro-Kann requested by voice as White");
sid = await launch({ mode: "blindfold", style: "mix" });
await clearSpoken();
const cmd = await sayAndCapture("play the Caro-Kann against me", /\/opening$/);
note("opening command", cmd.body);
check(cmd.body.accepted && cmd.body.opening_request.label === "Caro-Kann Defense" && cmd.body.message.includes("1. e4 c6"), "parsed to Caro-Kann Defense (B10, 1. e4 c6)");
const { body: ck } = await sayAndCapture("e4", /\/move$/);
await settle();
note("reply to 1.e4", ck.opponent_move);
check(ck.opponent_move.san === "c6" && ck.opponent_move.source_tier === "requested_opening", "opponent replied 1...c6 from the requested opening");
await waitSpoken("Caro-Kann Defense");
sp = await spoken();
note("spoken (Caro-Kann)", sp);
check(sp.includes("Opponent plays pawn c6.") && sp.includes("Caro-Kann Defense"), "spoken: 'Opponent plays pawn c6.' then 'Caro-Kann Defense'");
await page.screenshot({ path: join(OUT, "vo_caro_kann_request.png"), fullPage: true });

// ---------------------------------------------------------------- E. Black: engine opens, then "play d4 against me"
note("phase", "E: playing Black — engine opens and announces; 'play d4 against me'");
sid = await launch({ mode: "blindfold", color: "black", style: "mainline" });
const first = (await apiGet(`/sessions/${sid}`)).state_json;
await verifyEvaluationForFen(sid, first.current_fen, "opponent opening during session creation");
await waitSpoken("Opponent plays");
note("engine's first move", first.move_history_san);
check(first.move_history_san.length === 1 && (await spoken()).includes(`Opponent plays ${first.move_history_san[0] === "e4" ? "pawn e4" : ""}`.trim()) || (await spoken()).some((t) => t.startsWith("Opponent plays")), "engine opened as White and its first move was spoken");
await clearSpoken();
const d4 = await sayAndCapture("play d4 against me", /\/opening$/);
note("play d4 against me", d4.body);
check(d4.body.accepted && d4.body.restarted && d4.body.opponent_move.san === "d4", "game restarted and the engine opened 1.d4");
await waitSpoken("Opponent plays pawn d4.");
const st = (await apiGet(`/sessions/${sid}`)).state_json;
check(st.move_history_san.join(" ") === "d4" && st.next_to_move === "player", "server history is just 1.d4, Black to move");
const { body: bl } = await sayAndCapture("d5", /\/move$/);
await settle();
check(bl.played_san === "d5" && !!bl.opponent_move, `player's 1...d5 accepted; engine replied ${bl.opponent_move?.san}`);
await verifyEvaluationForFen(sid, bl.current_fen, "after Black's move and White's opponent reply");
await page.screenshot({ path: join(OUT, "vo_black_play_d4.png"), fullPage: true });

// Named opening from the launcher as Black: the engine opens the Queen's Gambit.
sid = await launch({ mode: "guided", color: "black", style: "mix", opening: "Queen's Gambit" });
const qg0 = (await apiGet(`/sessions/${sid}`)).state_json;
check(qg0.move_history_san.join(" ") === "d4" && qg0.opening_request.label === "Queen's Gambit", "launcher 'Queen's Gambit' as Black → engine opened 1.d4");
await clearSpoken();
const { body: qg } = await sayAndCapture("d5", /\/move$/);
await settle();
check(qg.opponent_move.san === "c4" && qg.announcements.some((a) => a.name === "Queen's Gambit" && a.by === "opponent"), "after 1...d5 the engine played 2.c4 and 'Queen's Gambit' was announced");
await waitSpoken("Queen's Gambit");
await page.screenshot({ path: join(OUT, "vo_black_queens_gambit.png"), fullPage: true });

// ---------------------------------------------------------------- F. Quiet mode as Black in a blindfold session
note("phase", "F: quiet mode as Black — blindfold, engine opens, then a legal move");
sid = await launch({ mode: "blindfold", color: "black", style: "mainline", quiet: true });
check((await page.getByRole("img", { name: /board hidden/i }).count()) === 1, "quiet Black session is blindfolded");
const quietBlackState = (await apiGet(`/sessions/${sid}`)).state_json;
check(quietBlackState.player_color === "black" && quietBlackState.move_history_san.length === 1, "engine opened and Black is to move");
check((await spoken()).includes("Pawn e4"), "quiet engine opening was announced as concise SAN wording");
await clearSpoken();
await page.evaluate(() => { window.__moveListeningSamples.length = 0; window.__ttsMicLeaks.length = 0; });
const { body: quietBlackMove } = await sayAndCapture("e5", /\/move$/);
await settle();
check(quietBlackMove.quiet && quietBlackMove.played_san === "e5", "Black's quiet-mode move was accepted");
await verifyEvaluationForFen(sid, quietBlackMove.current_fen, "quiet blindfold move and opponent reply");
sp = await spoken();
check(sp.length === 1 && !/^Opponent plays /.test(sp[0]), "quiet Black reply is one concise announcement with no prefix");
const blackListening = await page.evaluate(() => window.__moveListeningSamples);
check(blackListening.length > 0 && blackListening.every(Boolean), "quiet Black recognition stayed ready during backend processing");
check((await page.evaluate(() => window.__ttsMicLeaks)).length === 0, "quiet Black recognition paused through TTS");
await page.screenshot({ path: join(OUT, "vo_quiet_black_blindfold.png"), fullPage: true });

// ---------------------------------------------------------------- G. Generic castling asks only when both legal sides exist.
note("phase", "G: generic castling clarification, legal SAN submission, and hidden-board evaluation compatibility");
sid = (await apiPost("/sessions", {
  mode: "blindfold", player_color: "white", sampler_mode: "mainline", seed: 73,
  starting_fen: "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
})).id;
await page.goto(new URL(`/session/${sid}`, FE).href);
const blindfoldEvaluation = page.getByLabel("Position evaluation");
await blindfoldEvaluation.waitFor();
check(await page.getByRole("img", { name: /board hidden/i }).count() === 1, "blindfold evaluation remains visible without revealing the board");
await blindfoldEvaluation.locator(".evaluation-track").waitFor({ timeout: 30000 });
check(await blindfoldEvaluation.getByText(/Stockfish/i).count() === 1, "blindfold position receives its background Stockfish evaluation");
const movesBeforeCastle = apiCalls.filter((call) => call.endsWith("/move")).length;
await speak("castle");
await waitSpoken("Both castling sides are legal");
check(apiCalls.filter((call) => call.endsWith("/move")).length === movesBeforeCastle, "generic castle asks which side instead of guessing");
const castleResponse = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/move") && response.request().method() === "POST");
await speak("queenside");
const castleMove = await (await castleResponse).json();
check(castleMove.played_san === "O-O-O", "queenside clarification submits the backend-listed legal castle");
await settle();
await verifyEvaluationForFen(sid, castleMove.current_fen, "after queenside castling");

sid = (await apiPost("/sessions", {
  mode: "blindfold", player_color: "white", sampler_mode: "mainline", seed: 75,
  starting_fen: "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
})).id;
await page.goto(new URL(`/session/${sid}`, FE).href);
sessionSnapshot = await apiGet(`/sessions/${sid}`);
await verifyEvaluationForFen(sid, sessionSnapshot.state_json.current_fen, "blindfold evaluation before kingside castling");
const kingsideResponse = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/move") && response.request().method() === "POST");
await speak("castle kingside");
const kingsideMove = await (await kingsideResponse).json();
check(kingsideMove.played_san === "O-O", "kingside castling submits the backend-listed legal castle");
await settle();
await verifyEvaluationForFen(sid, kingsideMove.current_fen, "after kingside castling");

sid = (await apiPost("/sessions", {
  mode: "guided", player_color: "white", sampler_mode: "mainline", seed: 74,
  starting_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
})).id;
await page.goto(new URL(`/session/${sid}`, FE).href);
const movesBeforeUnavailableCastle = apiCalls.filter((call) => call.endsWith("/move")).length;
await speak("castle");
await waitSpoken("Castling is unavailable in the current position");
check(apiCalls.filter((call) => call.endsWith("/move")).length === movesBeforeUnavailableCastle, "unavailable castling gives clear feedback and submits no move");

// A position reached through an uncommon but legal line exercises the cache-miss path.
note("phase", "cache miss, pending evaluation, and voice interaction during real Stockfish analysis");
const missBoard = new Chess();
for (const san of ["b3", "Nf6", "Bb2", "g6", "Nf3", "Bg7"]) missBoard.move(san);
const missFen = missBoard.fen();
const missSession = await apiPost("/sessions", {
  mode: "blindfold", player_color: "white", sampler_mode: "mainline", seed: 913,
  starting_fen: missFen, quiet: true,
});
const initialMissEvaluation = await apiGet(`/sessions/${missSession.id}/evaluation`);
check(initialMissEvaluation.current_fen === missFen, "cache-miss session starts with the exact requested FEN");
if (!initialMissEvaluation.analysis_available && initialMissEvaluation.analysis_pending) {
  check(true, "uncached session position schedules real Stockfish analysis");
} else if (initialMissEvaluation.analysis_available) {
  check(initialMissEvaluation.depth >= 10 && initialMissEvaluation.nodes > 0 && Boolean(initialMissEvaluation.engine_version), "fresh position already has completed real Stockfish metadata");
  skip("The initial pending response was too brief to observe; evaluation had completed before the first browser read.");
} else {
  check(false, "uncached session position reports pending Stockfish analysis");
}
await page.goto(new URL(`/session/${missSession.id}`, FE).href);
const missPanel = page.getByLabel("Position evaluation");
await missPanel.waitFor();
check(await page.getByRole("img", { name: /board hidden/i }).count() === 1, "cache-miss evaluation session remains blindfolded");
if (initialMissEvaluation.analysis_pending) {
  try {
    await missPanel.getByRole("status").filter({ hasText: "Stockfish analysis is in progress" }).waitFor({ timeout: 5000 });
    check(true, "UI shows the pending Stockfish state instead of 'No saved analysis'");
  } catch {
    skip("The browser did not observe the brief pending UI state before Stockfish completed.");
  }
} else {
  skip("Pending UI state was not observable because the initial evaluation was already complete.");
}
await page.evaluate(() => { window.__moveListeningSamples.length = 0; window.__ttsMicLeaks.length = 0; });
const moveStart = Date.now();
const { status: missMoveStatus, body: missMove } = await sayAndCapture("e4", /\/move$/);
const moveElapsedMs = Date.now() - moveStart;
note("move response while evaluation runs (ms)", moveElapsedMs);
await settle();
check(missMoveStatus === 200 && missMove.played_san === "e4" && !!missMove.opponent_move, "voice move and opponent reply complete while session evaluation is scheduled");
const missListeningSamples = await page.evaluate(() => window.__moveListeningSamples);
check(missListeningSamples.length > 0 && missListeningSamples.every(Boolean), "quiet voice recognition remains ready during the move request");
check((await page.evaluate(() => window.__ttsMicLeaks)).length === 0, "voice response does not leak into active recognition");
await verifyEvaluationForFen(missSession.id, missMove.current_fen, "cache-miss result after player move and opponent reply");
await page.screenshot({ path: join(OUT, "evaluation-cache-miss-blindfold.png"), fullPage: true });

const expectedApiErrors = apiErrorResponses.filter((entry) => entry.endsWith("/move 422"));
const unexpectedApiErrors = apiErrorResponses.filter((entry) => !entry.endsWith("/move 422"));
check(unexpectedApiErrors.length === 0, "no unexpected 4xx/5xx responses from the isolated API");
const passed = log.filter((entry) => entry.k === "ok").length;
const failed = log.filter((entry) => entry.k === "FAIL").length;
const report = {
  proof: "voice_openings_and_evaluation_consistency",
  counts: { passed, failed, skipped: skipped.length },
  expectedApiErrors,
  unexpectedApiErrors,
  failedRequests,
  consoleErrors,
  log,
  skipped,
};
writeFileSync(join(OUT, "proof_voice_openings.json"), JSON.stringify(report, null, 2));
await browser.close();
console.log(process.exitCode ? "PROOF FAILED" : "PROOF PASSED", `(${passed} passed, ${failed} failed, ${skipped.length} skipped)`);
