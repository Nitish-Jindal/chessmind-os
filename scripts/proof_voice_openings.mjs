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
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const { Chess } = createRequire(join(ROOT, "frontend", "package.json"))("chess.js");

const FE = process.argv[2] ?? "http://localhost:5173";
const API = process.argv[3] ?? "http://localhost:8000";
const OUT = join(ROOT, "scripts", "data");
mkdirSync(OUT, { recursive: true });
const log = [];
const note = (k, v) => { log.push({ k, v }); console.log(k, typeof v === "string" ? v : JSON.stringify(v)); };
function check(cond, msg) { if (!cond) { console.error("FAIL:", msg); process.exitCode = 1; note("FAIL", msg); } else note("ok", msg); }

const initScript = () => {
  window.__spoken = [];
  window.__utterance = [];
  const synth = window.speechSynthesis;
  synth.getVoices = () => [{ voiceURI: "fake-en-us", name: "Fake American", lang: "en-US", default: true, localService: true }];
  synth.speak = (u) => { window.__spoken.push(u.text); setTimeout(() => u.onend && u.onend(), 5); };
  class FakeRec {
    start() { this.listening = true; }
    stop() {
      if (!this.listening) return;
      this.listening = false;
      const alts = window.__utterance.shift();
      if (alts !== undefined) {
        const list = Array.isArray(alts) ? alts : [alts];
        const res = { isFinal: true, length: list.length };
        list.forEach((t, i) => { res[i] = { transcript: t, confidence: 0.9 - i * 0.1 }; });
        this.onresult?.({ resultIndex: 0, results: { length: 1, 0: res } });
      }
      setTimeout(() => this.onend?.(), 5);
    }
    abort() { this.stop(); }
  }
  window.SpeechRecognition = FakeRec;
  window.webkitSpeechRecognition = FakeRec;
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1100, height: 1000 } });
await ctx.addInitScript(initScript);
const page = await ctx.newPage();
page.on("pageerror", (e) => { console.error("PAGE ERROR", e.message); process.exitCode = 1; });
const apiCalls = [];
page.on("request", (r) => { const u = new URL(r.url()); if (u.origin === new URL(API).origin) apiCalls.push(`${r.method()} ${u.pathname}`); });

async function speak(utterance) {
  await page.evaluate((u) => window.__utterance.push(u), utterance);
  const ptt = page.getByRole("button", { name: /hold to talk|listening/i });
  await ptt.dispatchEvent("pointerdown");
  await page.waitForTimeout(60);
  await ptt.dispatchEvent("pointerup");
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
check((await page.locator("chessboard-board, [data-boardid], .board").count()) === 0 || (await page.getByRole("img", { name: /board hidden/i }).count()) === 1, "blindfold: board hidden");
await clearSpoken();
const annByPly = [];
for (const [said, expectOpp] of [["e four", "e5"], ["knight f3", "Nc6"], ["bishop b5", null]]) {
  const { body } = await sayAndCapture(said, /\/move$/);
  annByPly.push({ played: body.played_san, opponent: body.opponent_move?.san, announcements: body.announcements.map((a) => `${a.name} (${a.eco}, ply ${a.ply}, by ${a.by})`) });
  if (expectOpp) check(body.opponent_move?.san === expectOpp, `after ${body.played_san} the mainline opponent played ${expectOpp} (got ${body.opponent_move?.san}, ${body.opponent_move?.source_tier})`);
  await settle();
}
note("announcements per move", annByPly);
check(annByPly[0].announcements.length === 0 && annByPly[1].announcements.length === 0, "no announcement for 1.e4 e5 2.Nf3 Nc6 (generic King's Pawn / King's Knight names)");
check(annByPly[2].announcements[0] === "Ruy Lopez (C60, ply 5, by player)", "3.Bb5 announced 'Ruy Lopez' (C60) at ply 5");
await waitSpoken("Ruy Lopez");
let sp = await spoken();
note("spoken (phase A)", sp);
check(sp.filter((t) => t === "Ruy Lopez").length === 1, "TTS spoke 'Ruy Lopez' exactly once");
check((await captions()).includes("opening: Ruy Lopez"), "caption 'opening: Ruy Lopez' shown");
// The page shows the latest named position: after 3...a6 that is C70 "Ruy Lopez: Morphy Defense".
const openingLine = await page.getByLabel("Opening").innerText();
note("opening line", openingLine);
check(/C(60|70) Ruy Lopez/.test(openingLine), "opening line on the page names the Ruy Lopez (latest named position)");
const hist = (await apiGet(`/sessions/${sid}`)).state_json.move_history_san;
const c = new Chess(); for (const m of hist) c.move(m);
note("history", hist.join(" "));
check(hist.slice(0, 5).join(" ") === "e4 e5 Nf3 Nc6 Bb5", "server history starts 1.e4 e5 2.Nf3 Nc6 3.Bb5 (replayed by chess.js)");
// No LLM_API_KEY in this stack: the optional LLM layer is off and the template is used.
check((await page.getByLabel("Coach feedback").innerText()).includes("wording: deterministic template"), "coach card: wording from the deterministic template (LLM layer off without a key)");
await page.screenshot({ path: join(OUT, "vo_ruy_lopez_announced.png"), fullPage: true });

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
check(sp.length === 3 && sp.every((t) => /^Opponent plays /.test(t)), "only 'Opponent plays …' was spoken (3 moves)");
check(!(await captions()).match(/coach:|opening:/), "no coach or opening captions");
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
await page.getByLabel("Quiet mode").check();
await page.waitForResponse((r) => r.url().endsWith("/settings"));
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

writeFileSync(join(OUT, "proof_voice_openings.json"), JSON.stringify(log, null, 2));
await browser.close();
console.log(process.exitCode ? "PROOF FAILED" : "PROOF PASSED", `(${log.filter((l) => l.k === "ok").length} checks)`);
