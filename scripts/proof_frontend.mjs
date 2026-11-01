// Proof: drive the real frontend (Vite dev server) against the real backend with a real browser.
//
//   node scripts/proof_frontend.mjs [frontendUrl] [apiUrl]
//
// The backend, Stockfish, Postgres and the app's own resolver/captions/TTS code are all real.
// The one thing a headless browser cannot supply is a microphone and system voices, so an init
// script installs automatic SpeechRecognition input and a fake voice list, and records both TTS
// output and any overlap with an active microphone.
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const FE = process.argv[2] ?? "http://localhost:5173";
const OUT = join(dirname(fileURLToPath(import.meta.url)), "data");
mkdirSync(OUT, { recursive: true });
const log = [];
const note = (k, v) => { log.push({ k, v }); console.log(k, typeof v === "string" ? v : JSON.stringify(v)); };
function check(cond, msg) { if (!cond) { console.error("FAIL:", msg); process.exitCode = 1; } else note("ok", msg); }

const initScript = () => {
  window.__spoken = [];
  window.__activeRec = null;
  window.__recognizerStarts = 0;
  window.__micTtsOverlaps = 0;
  window.__resultIndex = 0;
  const voices = [
    { voiceURI: "fake-en-gb", name: "Fake British", lang: "en-GB", default: false, localService: true },
    { voiceURI: "fake-en-us", name: "Fake American", lang: "en-US", default: true, localService: true },
  ];
  const synth = window.speechSynthesis;
  synth.getVoices = () => voices;
  synth.speak = (u) => {
    if (window.__activeRec?.listening) window.__micTtsOverlaps++;
    window.__spoken.push(u.text);
    setTimeout(() => u.onend && u.onend(), 5);
  };
  setTimeout(() => synth.dispatchEvent(new Event("voiceschanged")), 50);
  class FakeRec {
    constructor() { this.listening = false; this.continuous = false; }
    start(continuous) {
      this.listening = true;
      this.continuous = continuous;
      window.__activeRec = this;
      window.__recognizerStarts++;
    }
    stop() {
      if (!this.listening) return;
      this.listening = false;
      if (window.__activeRec === this) window.__activeRec = null;
      setTimeout(() => this.onend?.(), 5);
    }
    abort() { this.stop(); }
  }
  window.__deliverUtterance = (input) => {
    const rec = window.__activeRec;
    if (!rec?.listening) return false;
    const list = Array.isArray(input) ? input : [input];
    const index = window.__resultIndex++;
    const words = list[0].split(" ");
    rec.onresult?.({ resultIndex: index, results: { length: index + 1,
      [index]: { isFinal: false, length: 1, 0: { transcript: words.slice(0, 1).join(" "), confidence: 0.5 } },
    } });
    const res = { isFinal: true, length: list.length };
    list.forEach((t, i) => { res[i] = { transcript: t, confidence: 0.9 - i * 0.1 }; });
    rec.onresult?.({ resultIndex: index, results: { length: index + 1, [index]: res } });
    return true;
  };
  window.SpeechRecognition = FakeRec; // newer Chromium exposes the unprefixed name, which the app prefers
  window.webkitSpeechRecognition = FakeRec;
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } });
await ctx.addInitScript(initScript);
const page = await ctx.newPage();
const apiCalls = [];
page.on("response", (r) => { const u = new URL(r.url()); if (u.port === "8000") apiCalls.push(`${r.request().method()} ${u.pathname} ${r.status()}`); });
page.on("pageerror", (e) => { console.error("PAGE ERROR", e.message); process.exitCode = 1; });

async function speak(utterance) {
  await page.waitForFunction(() => window.__activeRec?.listening === true, null, { timeout: 30000 });
  const state = await page.evaluate((u) => ({ delivered: window.__deliverUtterance(u), listeningAfterResult: window.__activeRec?.listening === true }), utterance);
  if (!state.delivered) throw new Error("automatic microphone was not listening for the utterance");
  return state.listeningAfterResult;
}
const captions = () => page.locator('[aria-label="Live captions"]').innerText();
async function waitCaption(re, ms = 60000) {
  try {
    await page.waitForFunction(([src]) => new RegExp(src, "i").test(document.querySelector('[aria-label="Live captions"]')?.textContent ?? ""), [re.source], { timeout: ms });
  } catch (e) {
    console.error(`timed out waiting for caption ${re}\n--- captions ---\n${await captions()}\n--- mic status ---\n${await page.getByLabel("Microphone status").innerText()}`);
    await page.screenshot({ path: join(OUT, "frontend_failure.png") });
    throw e;
  }
}
const spoken = () => page.evaluate(() => window.__spoken);

// ---- register & land on Home
const email = `fe-proof-${Date.now()}@chessmind.example.com`;
await page.goto(FE);
await page.getByRole("button", { name: /need an account/i }).click();
await page.getByLabel("Display name").fill("Frontend Proof");
await page.getByLabel("Email").fill(email);
await page.getByLabel("Password").fill("proofpassword123");
await page.getByRole("button", { name: "Register" }).click();
await page.getByRole("heading", { name: "Start a session" }).waitFor();

// ---- TTS voice dropdown + persistence in settings_json.tts_voice
const sel = page.getByLabel("Text-to-speech voice");
check((await sel.locator("option").count()) === 3, "voice dropdown populated from getVoices() on voiceschanged (2 voices + default)");
const patched = page.waitForResponse((r) => r.url().endsWith("/players/me") && r.request().method() === "PATCH");
await sel.selectOption("fake-en-gb");
check((await patched).ok(), "choosing a voice PATCHes /players/me");
await page.reload();
await page.getByLabel("Text-to-speech voice").waitFor();
await page.waitForFunction(() => document.querySelector('select[aria-label="Text-to-speech voice"]')?.value === "fake-en-gb");
check(true, "voice choice restored from settings_json.tts_voice after reload");

// ---- Session 1: full board, mainline opponent
await page.getByLabel("Opponent style").selectOption("mainline");
await page.getByRole("button", { name: "Start", exact: true }).click();
await page.waitForURL(/\/session\//);
await page.locator('[data-square="e2"]').first().waitFor();
await page.waitForFunction(() => window.__activeRec?.listening === true, null, { timeout: 30000 });
check(/Listening for your move/.test(await page.getByLabel("Microphone status").innerText()), "microphone automatically activates for White's turn");
check(await page.evaluate(() => window.__activeRec?.continuous === false), "normal mode starts a turn-scoped recognizer");

// first move by dragging on the board (still POSTs /move)
// (HTML5 drag needs a few pauses between mouse events for react-dnd to register the hover)
const from = await page.locator('[data-square="d2"]').first().boundingBox();
const to = await page.locator('[data-square="d4"]').first().boundingBox();
const mid = (b) => [b.x + b.width / 2, b.y + b.height / 2];
await page.mouse.move(...mid(from));
await page.mouse.down();
await page.mouse.move(mid(from)[0], from.y - 10, { steps: 5 });
await page.waitForTimeout(100);
await page.mouse.move(...mid(to), { steps: 10 });
await page.waitForTimeout(100);
await page.mouse.move(mid(to)[0] + 1, mid(to)[1] + 1);
await page.waitForTimeout(100);
await page.mouse.up();
await waitCaption(/Opponent plays/);
note("after-drag captions", await captions());
check(apiCalls.some((c) => c.startsWith("POST") && c.includes("/move") && c.endsWith("200")), "drag move went through POST /sessions/{id}/move");

// voice move: "knight f3"
const micAfterMove = await speak("knight f3");
check(!micAfterMove, "normal-mode recognition stops immediately after the spoken move is submitted");
await waitCaption(/you:\s*knight f3/);
await page.waitForFunction(() => (document.querySelector('[aria-label="Live captions"]')?.textContent.match(/Opponent plays/g) ?? []).length >= 2, null, { timeout: 60000 });
check((await spoken()).some((t) => /^Opponent plays/.test(t)), "TTS announced the opponent reply");
await page.getByLabel("Coach feedback").waitFor();
const coach = await page.getByLabel("Coach feedback").innerText();
note("coach-feedback", coach);
check(/Stockfish/i.test(coach), "coach card shows Stockfish engine evidence");
check((await spoken()).some((t) => t.length > 20 && !/^Opponent plays|Session started/.test(t)), "TTS announced the coach feedback");
await page.waitForFunction(() => window.__activeRec?.listening === true, null, { timeout: 30000 });
check(await page.evaluate(() => window.__micTtsOverlaps === 0), "normal-mode opponent and coach TTS never overlap recognition");
check(/Listening for your move/.test(await page.getByLabel("Microphone status").innerText()), "microphone resumes after the full opponent and coaching sequence");
await page.screenshot({ path: join(OUT, "frontend_session_full.png") });

// ambiguous: both knights can go to d2 -> must ask, then confirm by voice
await speak("knight d2");
await waitCaption(/More than one move fits/);
check(await page.getByRole("group", { name: "Confirm move" }).isVisible(), "ambiguous utterance shows confirmation choices");
check((await spoken()).some((t) => /Did you mean .*knight .*d2.*knight .*d2/.test(t)), "TTS asked 'Did you mean A or B?'");
const movesBefore = apiCalls.filter((c) => c.startsWith("POST") && c.includes("/move")).length;
await speak("second");
await page.waitForFunction((n) => true, 0);
await page.waitForFunction(() => (document.querySelector('[aria-label="Live captions"]')?.textContent.match(/Opponent plays/g) ?? []).length >= 3 || /game is over/i.test(document.body.textContent), null, { timeout: 60000 });
check(apiCalls.filter((c) => c.startsWith("POST") && c.includes("/move")).length === movesBefore + 1, "confirmation resolved to exactly one POST /move");

// text fallback
const typed = page.getByLabel("Type a move");
await typed.fill("e3");
await typed.press("Enter");
await page.waitForFunction(() => (document.querySelector('[aria-label="Live captions"]')?.textContent.match(/you:\s*e3/g) ?? []).length >= 1);
check(true, "text-input fallback accepted a move");
await page.waitForTimeout(1500);
note("session1-captions", await captions());

// ---- Session 2: blindfold
await page.getByRole("link", { name: "Home" }).click();
await page.getByLabel("Visualization").selectOption("blindfold");
await page.getByLabel("Opponent style").selectOption("mainline");
await page.getByRole("button", { name: "Start", exact: true }).click();
await page.waitForURL(/\/session\//);
await page.getByText("Board hidden", { exact: true }).waitFor();
check((await page.locator("[data-square]").count()) === 0, "blindfold: no board squares in the DOM");

await speak(["e four"]);
await waitCaption(/Opponent plays/);
check((await page.locator("[data-square]").count()) === 0, "blindfold: board still hidden after the opponent replied");
const sp = await spoken();
check(sp.some((t) => /^Opponent plays/.test(t)), "blindfold: opponent reply announced by voice");
note("blindfold-captions", await captions());
await page.screenshot({ path: join(OUT, "frontend_blindfold_hidden.png") });

const rev = page.waitForResponse((r) => /\/sessions\/[^/]+\/reveal$/.test(r.url()) && r.request().method() === "POST");
await page.getByRole("button", { name: "Reveal board" }).click();
const revBody = await (await rev).json();
note("reveal-response", revBody);
await page.locator('[data-square="e4"]').first().waitFor();
check(revBody.was_free === true && revBody.reveals_used === 1, "reveal hit POST /sessions/{id}/reveal (free reveal #1)");
check(await page.getByText(/Reveals 1\/2 free/).isVisible(), "reveal counter updated in the UI");
const pieces = await page.locator('[data-piece]').count();
check(pieces >= 30, `board shows the position after reveal (${pieces} pieces)`);
await page.screenshot({ path: join(OUT, "frontend_blindfold_revealed.png") });

// voice command "reveal" also works (second free reveal)
await page.getByRole("button", { name: "Hide board" }).click();
await speak("reveal the board");
await page.locator('[data-square="e4"]').first().waitFor();
check(await page.getByText(/Reveals 2\/2 free/).isVisible(), "voice 'reveal' used the second free reveal");

// other pages render real data
for (const [name, heading] of [["Openings", "Opening tree"], ["Mastery", "Mastery"], ["Opponents", "Opponents"]]) {
  await page.getByRole("link", { name }).click();
  await page.getByRole("heading", { name: heading, exact: true }).waitFor();
}
await page.getByRole("link", { name: "Openings" }).click();
await page.getByRole("button", { name: "e4", exact: true }).first().waitFor();
await page.screenshot({ path: join(OUT, "frontend_openings.png") });
check(true, "openings page rendered tree edges from /openings/tree");

note("api-calls", apiCalls);
writeFileSync(join(OUT, "proof_frontend.json"), JSON.stringify({ proof: "frontend_e2e", email, log, apiCalls }, null, 2));
await browser.close();
