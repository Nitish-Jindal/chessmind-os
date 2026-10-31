// Proof: one opponent's real games → import → prep report → blindfold voice game vs their twin.
//
//   node scripts/proof_opponent_prep.mjs [frontendUrl] [apiUrl]
//
// Fixture: scripts/data/opponent_firouzja_150.pgn — the 150 most recent standard games of
// Alireza Firouzja (2021-09 → 2022-08) from the PgnMentor collection, mirrored at
// github.com/rozim/ChessData (PgnMentor/Firouzja.pgn). Real games, not composed lines.
//
// Real: Vite build, FastAPI, Postgres, Stockfish, python-chess, opening taxonomy. Stubbed (as
// in the other browser proofs): microphone and system voices. The report's counts are checked
// against chess.js replaying the same PGN file, independent of the python-chess code under test.
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const { Chess } = createRequire(join(ROOT, "frontend", "package.json"))("chess.js");

const FE = process.argv[2] ?? "http://localhost:5173";
const API = process.argv[3] ?? "http://localhost:8000";
const OUT = join(ROOT, "scripts", "data");
const FIXTURE = join(OUT, "opponent_firouzja_150.pgn");
mkdirSync(OUT, { recursive: true });
const log = [];
const note = (k, v) => { log.push({ k, v }); console.log(k, typeof v === "string" ? v : JSON.stringify(v)); };
function check(cond, msg) { if (!cond) { console.error("FAIL:", msg); process.exitCode = 1; note("FAIL", msg); } else note("ok", msg); }

// ---------------------------------------------------------------- independent ground truth (chess.js)
const PLIES = 12;
const truth = { white: [], black: [] };
for (const chunk of readFileSync(FIXTURE, "utf8").split(/\n\n(?=\[Event )/)) {
  if (!chunk.trim()) continue;
  const c = new Chess();
  c.loadPgn(chunk);
  const color = /\[White "[^"]*Firouzja/.test(chunk) ? "white" : "black";
  truth[color].push(c.history().slice(0, PLIES));
}
const countPrefix = (color, sans) => truth[color].filter((h) => sans.every((s, i) => h[i] === s)).length;
note("fixture games", { white: truth.white.length, black: truth.black.length });

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
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1100 } });
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

// ---------------------------------------------------------------- register + import
const email = `prep-proof-${Date.now()}@chessmind.example.com`;
await page.goto(FE);
await page.getByRole("button", { name: /need an account/i }).click();
await page.getByLabel("Display name").fill("Prep Proof");
await page.getByLabel("Email").fill(email);
await page.getByLabel("Password").fill("proofpassword123");
await page.getByRole("button", { name: "Register" }).click();
await page.getByRole("heading", { name: "Start a session" }).waitFor();
await page.getByRole("link", { name: "Opponents", exact: true }).click();
await page.getByRole("heading", { name: "Opponents", exact: true }).waitFor();
await page.getByLabel("New opponent name").fill("Firouzja");
await page.getByRole("button", { name: "Add", exact: true }).click();
await page.getByRole("heading", { name: "Import games for Firouzja" }).waitFor();
await page.getByLabel("PGN file").setInputFiles(FIXTURE);
await page.getByText(/150 games detected/).waitFor();
const t0 = Date.now();
await page.getByRole("button", { name: "Import", exact: true }).click();
const result = page.getByRole("status", { name: "Import result" });
await page.waitForFunction(() => /150 of 150 games imported/.test(document.querySelector('[aria-label="Import result"]')?.textContent ?? ""), null, { timeout: 600000 });
note("import result", await result.innerText());
note("import seconds", ((Date.now() - t0) / 1000).toFixed(1));
check(true, "150 of 150 real Firouzja games imported through the Opponents page");
await page.getByLabel("Opponent", { exact: true }).selectOption({ label: "Firouzja (150 games)" });
await page.screenshot({ path: join(OUT, "prep_import.png") });

// ---------------------------------------------------------------- prep report
const prepResp = page.waitForResponse((r) => /\/opponents\/[0-9a-f-]+\/prep/.test(r.url()), { timeout: 300000 });
await page.getByRole("button", { name: "Build prep report" }).click();
const rep = await (await prepResp).json();
await page.getByRole("region", { name: "When Firouzja has White" }).waitFor();
for (const color of ["white", "black"]) {
  const side = rep[`opponent_as_${color}`];
  check(side.games === truth[color].length, `${side.games} games with Firouzja as ${color} (chess.js: ${truth[color].length})`);
  const firsts = {};
  for (const h of truth[color]) firsts[h[0]] = (firsts[h[0]] ?? 0) + 1;
  check(side.first_moves.every((m) => m.games === firsts[m.san]), `first-move counts as ${color} match chess.js: ${side.first_moves.map((m) => `${m.san} ${m.games}`).join(", ")}`);
  for (const line of side.frequent_lines) {
    const n = countPrefix(color, line.san);
    check(line.games === n, `line ${line.san.join(" ")} (${line.opening?.name ?? "unnamed"}): ${line.games} games = chess.js ${n}`);
    const sr = line.suggested_response;
    if (sr?.best) {
      const c = new Chess(sr.fen);
      const mv = c.move(sr.best.san);
      check(!!mv && c.turn() !== (side.you_play === "white" ? "w" : "b"), `  suggestion ${sr.best.san} is legal for you (${side.you_play}) after ${sr.after_san.slice(line.san.length).join(" ") || "the line"}; ${sr.best.cp_for_you} cp for you, ${sr.engine.version} d${sr.engine.depth}`);
    }
  }
  for (const b of side.likely_branches) {
    check(b.options.every((o) => o.games === countPrefix(color, [...b.after_san, o.san])), `branch after "${b.after_san.join(" ") || "start"}" (${b.to_move} to choose): ${b.options.map((o) => `${o.san} ${o.games}`).join(", ")}`);
  }
}
note("prep summary", {
  white: rep.opponent_as_white.frequent_lines.map((l) => [l.san.join(" "), l.games, l.opening?.name, l.suggested_response?.best?.san]),
  black: rep.opponent_as_black.frequent_lines.map((l) => [l.san.join(" "), l.games, l.opening?.name, l.suggested_response?.best?.san]),
});
const shown = await page.getByRole("region", { name: "When Firouzja has White" }).innerText();
const topW = rep.opponent_as_white.frequent_lines[0];
check(shown.includes(`${topW.games} (`) && shown.includes(topW.suggested_response.best.san), "UI shows the top White line's count and its suggested response");
await page.getByLabel("Prep report").screenshot({ path: join(OUT, "prep_report.png") });

// ---------------------------------------------------------------- blindfold game vs the twin
await page.getByLabel("Your color against the twin").selectOption("black");
await page.getByLabel("Blindfold against the twin").check();
await page.getByRole("button", { name: "Play the twin" }).click();
await page.waitForURL(/\/session\//);
await page.getByRole("heading", { name: /Blindfold vs the twin/ }).waitFor();
check((await page.getByRole("img", { name: /board hidden/i }).count()) === 1, "blindfold vs twin: board hidden");
const sid = page.url().split("/session/")[1];
const st = await page.evaluate(async ([base, id]) => {
  const t = JSON.parse(localStorage.getItem("chessmind.tokens"));
  return (await fetch(`${base}/sessions/${id}`, { headers: { Authorization: `Bearer ${t.access_token}` } })).json();
}, [API, sid]);
const om = st.state_json.last_opponent_move;
note("twin's first move", om);
check(om.source_tier === "opponent_profile" && rep.opponent_as_white.first_moves.some((m) => m.san === om.san), `twin opened ${om.san} from Firouzja's own White games (opponent_profile tier)`);
await page.waitForFunction((s) => window.__spoken.some((t) => t.startsWith("Opponent plays")), null, { timeout: 15000 });
// Reply with the move Firouzja most often faced after the twin's opening move (chess.js tally),
// so the twin's answer can come from his games. A move he never faced would (correctly) fall
// back to the engine tier.
const faced = {};
for (const h of truth.white) if (h[0] === om.san && h[1]) faced[h[1]] = (faced[h[1]] ?? 0) + 1;
const reply = Object.entries(faced).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0];
note("reply chosen from his games", { after: om.san, faced });
const moveResp = page.waitForResponse((r) => r.url().endsWith("/move"), { timeout: 60000 });
await speak(reply);
const mr = await (await moveResp).json();
note("after voice reply", { played: mr.played_san, twin: mr.opponent_move });
check(mr.played_san === reply && mr.opponent_move?.source_tier === "opponent_profile", `spoken ${reply} accepted; twin answered ${mr.opponent_move?.san} from their games`);
await page.waitForTimeout(400);
note("spoken", await page.evaluate(() => window.__spoken));
await page.screenshot({ path: join(OUT, "prep_blindfold_twin.png"), fullPage: true });

writeFileSync(join(OUT, "proof_opponent_prep.json"), JSON.stringify(log, null, 2));
await browser.close();
console.log(process.exitCode ? "PROOF FAILED" : "PROOF PASSED", `(${log.filter((l) => l.k === "ok").length} checks)`);
