// Proof: voice position questions + quiz scoring (handoff §3.3 / acceptance #8) and the
// Lichess Masters panel (acceptance #11), driven through the real frontend in headless Chromium.
//
//   node scripts/proof_position_questions.mjs [frontendUrl] [apiUrl] [explorerReplayPort]
//
// Real: Vite build, FastAPI, Postgres, Stockfish, python-chess, the app's resolver/captions/TTS
// code. Stubbed (as in proof_frontend.mjs): the microphone (an init-script SpeechRecognition
// that emits the transcript we hand it) and system voices (speechSynthesis.speak is recorded).
//
// Every answer the app gives is checked against chess.js, an implementation independent of
// the python-chess code that produced it.
//
// Masters panel: the backend must run with LICHESS_EXPLORER_URL=http://127.0.0.1:<port> and a
// short LICHESS_EXPLORER_BACKOFF_S. Phase A leaves that port closed (a real connection failure
// -> the UI falls back to local counts). Phase B starts an HTTP server on it that replays
// backend/tests/fixtures/lichess_masters_startpos.json, which is HAND-WRITTEN in the documented
// response shape (Lichess is unreachable from the build sandbox), so phase B proves the
// fetch -> parse -> cache -> UI path, not real-world numbers.
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const { Chess } = createRequire(join(ROOT, "frontend", "package.json"))("chess.js");

const FE = process.argv[2] ?? "http://localhost:5173";
const API = process.argv[3] ?? "http://localhost:8000";
const REPLAY_PORT = Number(process.argv[4] ?? 8765);
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

// ---------------------------------------------------------------- chess.js ground truth
const NAMES = { p: "pawn", n: "knight", b: "bishop", r: "rook", q: "queen", k: "king" };
const LETTER = Object.fromEntries(Object.entries(NAMES).map(([k, v]) => [v, k]));
const ALL = "abcdefgh".split("").flatMap((f) => "12345678".split("").map((r) => f + r));
const piecesOf = (c, type, color) => ALL.filter((sq) => { const p = c.get(sq); return p && p.type === type && p.color === color; }).sort();
function hangingOf(c, color) {
  const opp = color === "w" ? "b" : "w";
  return ALL.filter((sq) => {
    const p = c.get(sq);
    if (!p || p.color !== color || p.type === "k") return false;
    const att = c.attackers(sq, opp).length;
    return att > 0 && att > c.attackers(sq, color).length;
  }).sort();
}
const spokenSquare = (sq) => `${sq[0]} ${["", "one", "two", "three", "four", "five", "six", "seven", "eight"][Number(sq[1])]}`;

// ---------------------------------------------------------------- browser helpers
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1100, height: 950 } });
await ctx.addInitScript(initScript);
const page = await ctx.newPage();
page.on("pageerror", (e) => { console.error("PAGE ERROR", e.message); process.exitCode = 1; });

async function speak(utterance) {
  await page.evaluate((u) => window.__utterance.push(u), utterance);
  const ptt = page.getByRole("button", { name: /hold to talk|listening/i });
  await ptt.dispatchEvent("pointerdown");
  await page.waitForTimeout(60);
  await ptt.dispatchEvent("pointerup");
}
const captions = () => page.locator('[aria-label="Live captions"]').innerText();
const spoken = () => page.evaluate(() => window.__spoken);
async function apiGet(path) {
  return page.evaluate(async ([base, p]) => {
    const t = JSON.parse(localStorage.getItem("chessmind.tokens"));
    return (await fetch(base + p, { headers: { Authorization: `Bearer ${t.access_token}` } })).json();
  }, [API, path]);
}
async function apiPost(path, body) {
  return page.evaluate(async ([base, p, b]) => {
    const t = JSON.parse(localStorage.getItem("chessmind.tokens"));
    return (await fetch(base + p, { method: "POST", headers: { Authorization: `Bearer ${t.access_token}`, "Content-Type": "application/json" }, body: JSON.stringify(b) })).json();
  }, [API, path, body]);
}
/** Say something and return the JSON of the API call it triggers. */
async function sayAndCapture(utterance, urlRe) {
  const resp = page.waitForResponse((r) => urlRe.test(new URL(r.url()).pathname) && r.request().method() === "POST", { timeout: 60000 });
  await speak(utterance);
  return (await resp).json();
}
async function typeAndCapture(text, urlRe) {
  const resp = page.waitForResponse((r) => urlRe.test(new URL(r.url()).pathname) && r.request().method() === "POST", { timeout: 60000 });
  const box = page.getByLabel("Type a move");
  await box.fill(text);
  await box.press("Enter");
  return (await resp).json();
}
async function captionHas(text) {
  await page.waitForFunction((t) => document.querySelector('[aria-label="Live captions"]')?.textContent.includes(t), text, { timeout: 15000 });
  return true;
}

// ---------------------------------------------------------------- register, blindfold session
const email = `pq-proof-${Date.now()}@chessmind.example.com`;
await page.goto(FE);
await page.getByRole("button", { name: /need an account/i }).click();
await page.getByLabel("Display name").fill("Questions Proof");
await page.getByLabel("Email").fill(email);
await page.getByLabel("Password").fill("proofpassword123");
await page.getByRole("button", { name: "Register" }).click();
await page.getByRole("heading", { name: "Start a session" }).waitFor();
await page.getByLabel("Visualization").selectOption("blindfold");
await page.getByLabel("Opponent style").selectOption("mainline");
await page.getByRole("button", { name: "Start", exact: true }).click();
await page.waitForURL(/\/session\//);
const sid = page.url().split("/session/")[1].split("?")[0];
await page.getByText("Board hidden", { exact: true }).waitFor();

await sayAndCapture(["e four"], /\/move$/);
await sayAndCapture(["knight f three"], /\/move$/);
await page.waitForFunction(() => (document.querySelector('[aria-label="Live captions"]')?.textContent.match(/Opponent plays/g) ?? []).length >= 2);
check((await page.locator("[data-square]").count()) === 0, "blindfold: board hidden while asking questions");

const legal = await apiGet(`/sessions/${sid}/legal`);
const board = new Chess(legal.current_fen);
note("position", legal.current_fen);

// ---------------------------------------------------------------- player asks, app answers
let r = await sayAndCapture(["where is my knight"], /\/ask$/);
check(r.understood && r.kind === "piece_location", "voice 'where is my knight' parsed as a piece-location question");
check(JSON.stringify(r.facts.squares) === JSON.stringify(piecesOf(board, "n", "w")), `knight squares ${r.facts.squares} match chess.js ${piecesOf(board, "n", "w")}`);
check(await captionHas(r.answer_text), "answer captioned");
check((await spoken()).includes(r.answer_text), `answer spoken by TTS: "${r.answer_text}"`);

r = await sayAndCapture(["where are your bishops"], /\/ask$/);
check(JSON.stringify(r.facts.squares) === JSON.stringify(piecesOf(board, "b", "b")), `opponent bishops ${r.facts.squares} match chess.js`);

r = await typeAndCapture("what's on e4", /\/ask$/);
const e4 = board.get("e4");
check(r.kind === "square_content" && r.facts.piece === (e4 ? NAMES[e4.type] : null), `typed 'what's on e4' -> ${r.answer_text}`);
r = await sayAndCapture(["what is on see six"], /\/ask$/);
const c6 = board.get("c6");
check(r.facts.square === "c6" && r.facts.piece === (c6 ? NAMES[c6.type] : null), `spoken 'see six' -> ${r.answer_text}`);

for (const [utter, color] of [["anything hanging", "w"], ["are your pieces hanging", "b"]]) {
  r = await sayAndCapture([utter], /\/ask$/);
  const got = r.facts.hanging.map((h) => h.square).sort();
  check(r.kind === "hanging_pieces" && JSON.stringify(got) === JSON.stringify(hangingOf(board, color)), `'${utter}' -> [${got}] matches chess.js [${hangingOf(board, color)}]`);
}

r = await sayAndCapture(["whose move is it"], /\/ask$/);
check(r.facts.turn === (board.turn() === "w" ? "white" : "black"), `'whose move' -> ${r.answer_text}`);

r = await sayAndCapture(["what's the eval"], /\/ask$/);
const an = await apiPost("/analysis/position", { fen: legal.current_fen, depth: 10, multipv: 3 });
check(r.kind === "evaluation" && r.facts.cp_white === an.results[0].cp && r.facts.depth === 10, `eval ${r.answer_text} matches the engine_analysis row (cp ${an.results[0].cp}, cached=${an.cached})`);
check(/Stockfish .* at depth 10/.test(r.answer_text), "eval answer names engine + depth");

let askCalls = 0;
const countAsk = (req) => { if (/\/ask$/.test(new URL(req.url()).pathname)) askCalls++; };
page.on("request", countAsk);
await speak(["blah blah"]);
await captionHas("I didn't catch a move");
page.off("request", countAsk);
check(askCalls === 0, "non-question utterance is not sent to /ask (handled as a move attempt)");

// ---------------------------------------------------------------- quiz: app asks, player answers
function truthFor(prompt, c) {
  let m;
  if ((m = prompt.match(/holding (your opponent's|your) (\w+)s\./))) {
    const color = m[1] === "your" ? "w" : "b";
    return { kind: "piece_location", squares: piecesOf(c, LETTER[m[2]], color) };
  }
  if ((m = prompt.match(/What is on ([a-h][1-8])\?/))) {
    const p = c.get(m[1]);
    return { kind: "square_content", square: m[1], piece: p ? NAMES[p.type] : null, color: p ? (p.color === "w" ? "white" : "black") : null };
  }
  if ((m = prompt.match(/Which of (your opponent's|your) pieces are hanging/))) {
    return { kind: "hanging_pieces", squares: hangingOf(c, m[1] === "your" ? "w" : "b") };
  }
  if (/Whose move/.test(prompt)) return { kind: "side_to_move", turn: c.turn() === "w" ? "white" : "black" };
  if (/who is better/.test(prompt)) {
    const top = an.results[0];
    const bucket = top.mate != null ? (top.mate > 0 ? "white" : "black") : Math.abs(top.cp) <= 50 ? "equal" : top.cp > 0 ? "white" : "black";
    return { kind: "evaluation", bucket };
  }
  throw new Error(`unrecognized prompt ${prompt}`);
}
function rightAnswer(t) {
  switch (t.kind) {
    case "piece_location": case "hanging_pieces": return t.squares.length ? t.squares.map(spokenSquare).join(" and ") : "none";
    case "square_content": return t.piece ? `${t.color} ${t.piece}` : "empty";
    case "side_to_move": return t.turn;
    case "evaluation": return t.bucket === "equal" ? "about equal" : `${t.bucket} is better`;
  }
}
function wrongAnswer(t) {
  switch (t.kind) {
    case "piece_location": case "hanging_pieces": return t.squares.includes("h5") ? "a5" : "h five";
    case "square_content": return t.piece === "queen" ? "black rook" : "white queen";
    case "side_to_move": return t.turn === "white" ? "black" : "white";
    case "evaluation": return t.bucket === "black" ? "white is winning" : "black is winning";
  }
}

const kinds = new Set();
let correct = 0;
const N = 10;
for (let i = 0; i < N; i++) {
  const qResp = page.waitForResponse((x) => /\/quiz$/.test(new URL(x.url()).pathname));
  if (i % 2 === 0) await page.getByRole("button", { name: "Quiz me on this position" }).click();
  else await speak(["quiz me"]);
  const q = await (await qResp).json();
  kinds.add(q.kind);
  await captionHas(q.prompt);
  const truth = truthFor(q.prompt, board);
  check(truth.kind === q.kind, `quiz ${i + 1} (${q.kind}): "${q.prompt}"`);
  const wrong = i === N - 1;
  const said = wrong ? wrongAnswer(truth) : rightAnswer(truth);
  const a = await sayAndCapture([said], /\/answer$/);
  note(`quiz-${i + 1}`, { said, correct: a.correct, score: a.score, feedback: a.feedback });
  check(a.understood && a.correct === !wrong, wrong ? `deliberately wrong answer "${said}" scored ${a.score}` : `spoken answer "${said}" scored correct`);
  await captionHas(a.feedback);
  if (a.correct) correct++;
}
check(kinds.size >= 3, `quiz covered ${kinds.size} question kinds: ${[...kinds].join(", ")}`);
await page.getByText(`Quiz: ${correct}/${N} correct`).waitFor();
check(correct === N - 1, `UI tally shows ${correct}/${N}`);
const tally = await apiGet(`/sessions/${sid}/questions`);
note("tally", tally);
check(tally.answered === N && tally.correct === N - 1 && tally.asked >= 8, "answers persisted and scored in position_question");
check((await page.locator("[data-square]").count()) === 0, "blindfold: board never revealed during questions");
await page.screenshot({ path: join(OUT, "pq_session.png") });
note("session-captions", await captions());

// ---------------------------------------------------------------- masters panel: outage, then replay
const pre = await apiGet(`/openings/masters?fen=${encodeURIComponent("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")}`);
if (pre.status === "cached" || pre.status === "stale") {
  throw new Error("start position already in master_explorer_cache: run this proof against a fresh database");
}
await page.getByRole("link", { name: "Openings" }).click();
await page.getByRole("heading", { name: "Opening tree" }).waitFor();
const mastersCard = page.getByRole("region", { name: "Master games" });
await mastersCard.waitFor();
const down = await mastersCard.innerText();
note("masters-down", down);
check(/unavailable/i.test(down) && /Showing local counts only/.test(down), "explorer unreachable -> masters panel says so and falls back to local counts");
check(await page.getByRole("region", { name: "Local games" }).getByRole("button", { name: "e4", exact: true }).isVisible(), "local counts still shown");
await page.screenshot({ path: join(OUT, "pq_masters_unavailable.png") });

const fixture = readFileSync(join(ROOT, "backend", "tests", "fixtures", "lichess_masters_startpos.json"), "utf8");
const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const hits = [];
const replay = createServer((req, res) => {
  const u = new URL(req.url, "http://x");
  hits.push(`${u.pathname}?fen=${u.searchParams.get("fen")}`);
  if (u.pathname === "/masters" && u.searchParams.get("fen") === START) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(fixture);
  } else {
    res.writeHead(404);
    res.end("{}");
  }
});
await new Promise((ok) => replay.listen(REPLAY_PORT, "127.0.0.1", ok));
await page.waitForTimeout(2500); // let the backend's failure backoff lapse
await page.reload();
await mastersCard.waitFor();
await page.getByRole("region", { name: "Master games" }).getByText("fetched just now").waitFor({ timeout: 15000 });
const up = await mastersCard.innerText();
note("masters-up", up);
const fx = JSON.parse(fixture);
check(up.includes(`${(fx.white + fx.draws + fx.black).toLocaleString()} master games`), "sample size shown from the explorer payload");
check(/Source: Lichess Masters database/.test(up) && /open on lichess\.org/.test(up), "source attribution + lichess.org link shown");
check(!/Qh5/.test(up), "illegal move in the payload was dropped by python-chess validation");
check(hits.length === 1 && hits[0].includes("/masters?fen="), `backend fetched the explorer once: ${hits[0]}`);
await page.screenshot({ path: join(OUT, "pq_masters_fixture_replay.png") });
await page.reload();
await page.getByRole("region", { name: "Master games" }).getByText("cached", { exact: true }).waitFor();
check(hits.length === 1, "reload served from master_explorer_cache (no second upstream call)");
replay.close();

writeFileSync(join(OUT, "proof_position_questions.json"), JSON.stringify(log, null, 2));
await browser.close();
console.log(process.exitCode ? "PROOF FAILED" : "PROOF PASSED");
