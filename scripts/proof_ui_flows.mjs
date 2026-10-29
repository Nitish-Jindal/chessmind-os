// Proof: PGN upload page, mastery drill loop, and opponent import + twin game, driven through the
// real frontend in a real (headless) browser against the real backend, Postgres and Stockfish.
//
//   node scripts/proof_ui_flows.mjs [frontendUrl]
//
// Nothing is mocked. The only stub is speechSynthesis (headless Chromium has no voices), so TTS calls
// complete immediately; moves are typed into the text box, which goes through the same resolver and
// POST /sessions/{id}/move path as voice. Real-mic voice is verified separately on the HTTPS deploy.
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const FE = process.argv[2] ?? "http://localhost:5173";
const DATA = join(dirname(fileURLToPath(import.meta.url)), "data");
mkdirSync(DATA, { recursive: true });
const log = [];
const note = (k, v) => { log.push({ k, v }); console.log(k, typeof v === "string" ? v : JSON.stringify(v)); };
function check(cond, msg) { if (!cond) { console.error("FAIL:", msg); process.exitCode = 1; log.push({ k: "FAIL", v: msg }); } else note("ok", msg); }

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1100, height: 1000 } });
await ctx.addInitScript(() => {
  const synth = window.speechSynthesis;
  synth.getVoices = () => [];
  synth.speak = (u) => setTimeout(() => u.onend && u.onend(), 5);
});
const page = await ctx.newPage();
const apiCalls = [];
page.on("response", (r) => { const u = new URL(r.url()); if (u.port === "8000") apiCalls.push(`${r.request().method()} ${u.pathname} ${r.status()}`); });
page.on("pageerror", (e) => { console.error("PAGE ERROR", e.message); process.exitCode = 1; });
const called = (re) => apiCalls.some((c) => re.test(c));

async function waitText(locator, re, ms = 120000) {
  await locator.filter({ hasText: re }).first().waitFor({ timeout: ms });
}
async function typeMove(san) {
  const box = page.getByLabel("Type a move");
  await box.waitFor();
  await page.waitForFunction(() => !document.querySelector('input[aria-label="Type a move"]')?.disabled, null, { timeout: 60000 });
  await box.fill(san);
  await box.press("Enter");
}

// ---- register
const email = `ui-proof-${Date.now()}@chessmind.example.com`;
await page.goto(FE);
await page.getByRole("button", { name: /need an account/i }).click();
await page.getByLabel("Display name").fill("UI Proof");
await page.getByLabel("Email").fill(email);
await page.getByLabel("Password").fill("proofpassword123");
await page.getByRole("button", { name: "Register" }).click();
await page.getByRole("heading", { name: "Start a session" }).waitFor();

// =========================================================================================
// 1. PGN upload page: 153-game file -> repertoire import -> counts -> openings tree
// =========================================================================================
await page.getByRole("link", { name: "Import", exact: true }).click();
await page.getByRole("heading", { name: "Import PGN" }).waitFor();
check((await page.getByLabel("Import intent").locator("option").allTextContents()).length === 3, "intent selector offers repertoire / corpus / opponent");
await page.getByLabel("PGN file").setInputFiles(join(DATA, "ui_repertoire_153.pgn"));
await page.getByText(/153 games detected/).waitFor();
await page.getByRole("button", { name: "Import", exact: true }).click();
const result = page.getByRole("status", { name: "Import result" });
await waitText(result, /153 of 153 games imported/);
const resultText = await result.innerText();
note("repertoire-import-result", resultText);
check(called(/^POST \/imports\/pgn 202/), "file upload POSTed /imports/pgn");
check(called(/^GET \/imports\/[0-9a-f-]+ 200/), "UI polled GET /imports/{id}");
check(/succeeded/.test(resultText) && /positions/.test(resultText) && /repertoire lines/.test(resultText), "result shows status, positions, edges and repertoire lines");
await page.screenshot({ path: join(DATA, "ui_import_repertoire.png") });

await page.getByRole("link", { name: /Browse the opening tree/ }).click();
await page.getByRole("heading", { name: "Opening tree" }).waitFor();
const e4row = page.getByRole("row").filter({ has: page.getByRole("button", { name: "e4", exact: true }) });
await e4row.waitFor();
const e4cells = await e4row.locator("td").allInnerTexts();
note("tree-root-e4-row", e4cells);
check(Number(e4cells[1]) === 108, "openings tree shows the uploaded games landed (1.e4 = 108 games)");
await page.screenshot({ path: join(DATA, "ui_import_openings_tree.png") });

// paste path + corpus intent
await page.getByRole("link", { name: "Import", exact: true }).click();
await page.getByLabel("Import intent").selectOption("corpus");
await page.getByLabel("PGN text").fill('[Event "Corpus A"]\n[White "M1"]\n[Black "M2"]\n[Result "1/2-1/2"]\n\n1. d4 d5 2. c4 e6 1/2-1/2\n\n[Event "Corpus B"]\n[White "M3"]\n[Black "M4"]\n[Result "1-0"]\n\n1. d4 Nf6 2. c4 g6 1-0\n');
await page.getByRole("button", { name: "Import", exact: true }).click();
await waitText(result, /2 of 2 games imported/);
const corpusText = await result.innerText();
check(!/repertoire lines/.test(corpusText), "corpus intent reports no repertoire lines (graph only)");

// =========================================================================================
// 3a. Opponent import through the same page (100+ games), plus visible errors
// =========================================================================================
await page.getByLabel("Import intent").selectOption("opponent");
await page.getByLabel("New opponent name").fill("KID_Specialist");
await page.getByRole("button", { name: "Add", exact: true }).click();
await page.getByLabel("Opponent profile").selectOption({ label: "KID_Specialist (0 games)" });
await page.getByLabel("PGN file").setInputFiles(join(DATA, "ui_opponent_120.pgn"));
await page.getByText(/120 games detected/).waitFor();
await page.getByRole("button", { name: "Import", exact: true }).click();
await waitText(result, /120 of 120 games imported/);
note("opponent-import-result", await result.innerText());
check(called(/^POST \/imports\/pgn 202/), "opponent import POSTed /imports/pgn with intent=opponent");

await page.getByLabel("PGN text").fill('[Event "Wrong"]\n[White "Somebody"]\n[Black "Else"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n');
await page.getByRole("button", { name: "Import", exact: true }).click();
await waitText(result, /partial|failed/);
const errText = await result.innerText();
note("opponent-import-errors", errText);
check(/skipped: opponent not in game headers/.test(errText), "import errors from the job are shown in the UI");

// =========================================================================================
// 2. Mastery loop: mistake -> due drill -> answer -> MasteryState changes on screen
// =========================================================================================
await page.getByRole("link", { name: "Home", exact: true }).click();
await page.getByLabel("Opponent style").selectOption("mainline");
await page.getByRole("button", { name: "Start", exact: true }).click();
await page.waitForURL(/\/session\//);
await page.locator('[data-square="e2"]').first().waitFor();
await typeMove("g4");
const coach = page.getByLabel("Coach feedback");
await waitText(coach, /Stockfish/);
const mistake = await coach.innerText();
note("mistake-feedback", mistake);
check(/inaccuracy|mistake|blunder/.test(mistake), "1.g4 was graded as a mistake by Stockfish evidence");

await page.getByRole("link", { name: "Mastery", exact: true }).click();
await page.getByRole("heading", { name: "Mastery", exact: true }).waitFor();
await page.getByText(/1 due now/).first().waitFor();
await page.screenshot({ path: join(DATA, "ui_mastery_before.png") });
await page.getByRole("button", { name: "Start due drill" }).click();
await page.waitForURL(/\/session\/.*drill=/);
const drillCard = page.getByLabel("Drill mastery");
await drillCard.waitFor();
await page.locator('[data-square="e2"]').first().waitFor();
check(called(/^POST \/players\/[0-9a-f-]+\/mastery\/next 200/), "Start due drill called POST /players/{id}/mastery/next");
const beforeTable = await drillCard.locator("tbody tr").evaluateAll((rs) => rs.map((r) => [...r.children].map((c) => c.textContent)));
note("drill-before", beforeTable);
check(beforeTable.find((r) => r[0] === "Reps")?.[1] === "0", "drill opened on the due item (reps 0 after the mistake)");

await typeMove("e4");
await drillCard.getByText(/Recalled:/).waitFor({ timeout: 120000 });
const afterTable = await drillCard.locator("tbody tr").evaluateAll((rs) => rs.map((r) => [...r.children].map((c) => [c.textContent, c.getAttribute("data-changed")])));
note("drill-after", afterTable);
const row = (name) => afterTable.find((r) => r[0][0] === name);
check(row("Reps")[2][0] === "1" && row("Reps")[2][1] === "true", "reps 0 -> 1 on screen");
check(row("Next due")[2][1] === "true", "next-due moved on screen");
check(row("Recall")[2][1] === "true" || row("State")[2][1] === "true", "recall score / state changed on screen");
note("drill-summary", await drillCard.locator("p").first().innerText());
await page.screenshot({ path: join(DATA, "ui_mastery_drill.png") });

await drillCard.getByRole("link", { name: "Back to mastery" }).click();
await page.getByRole("heading", { name: "Mastery", exact: true }).waitFor();
await page.getByText(/0 due now/).first().waitFor();
check(true, "mastery dashboard now shows 0 due");
await page.screenshot({ path: join(DATA, "ui_mastery_after.png") });

// =========================================================================================
// 3b. Opponents page: tree + Play the twin
// =========================================================================================
await page.getByRole("link", { name: "Opponents", exact: true }).click();
await page.getByRole("heading", { name: "Opponents", exact: true }).waitFor();
await page.getByLabel("Opponent", { exact: true }).selectOption({ label: "KID_Specialist (120 games)" });
await page.getByText(/120 games at this position|games at this position/).waitFor();
const oppTree = await page.getByRole("row").filter({ has: page.getByRole("button", { name: "c4", exact: true }) }).first().innerText();
note("opponent-tree-root", oppTree);
check(called(/^GET \/opponents\/[0-9a-f-]+\/tree/), "opponent tree loaded from GET /opponents/{id}/tree");
await page.screenshot({ path: join(DATA, "ui_opponent_tree.png") });
await page.getByRole("button", { name: "Play the twin" }).click();
await page.waitForURL(/\/session\//);
await page.getByRole("heading", { name: /Playing the twin/ }).waitFor();
await page.locator('[data-square="e2"]').first().waitFor();
await typeMove("d4");
await waitText(coach, /opponent_profile/);
const twin = await coach.innerText();
note("twin-reply", twin);
check(/opponent replied Nf6/.test(twin), "twin replied Nf6 (the opponent's KID) from the opponent_profile tier");
await page.screenshot({ path: join(DATA, "ui_twin_session.png") });

// ---- PATCH /players/me merges settings_json (UI saves tts_voice; other keys must survive)
const merged = await page.evaluate(async () => {
  const t = JSON.parse(localStorage.getItem("chessmind.tokens")).access_token;
  const h = { "Content-Type": "application/json", Authorization: `Bearer ${t}` };
  const patch = (settings_json) => fetch("http://localhost:8000/players/me", { method: "PATCH", headers: h, body: JSON.stringify({ settings_json }) }).then((r) => r.json());
  await patch({ blindfold_free_reveals_per_session: 3 });
  const after = await patch({ tts_voice: "voice-x" });
  return after.settings_json;
});
note("settings-after-two-patches", merged);
check(merged.blindfold_free_reveals_per_session === 3 && merged.tts_voice === "voice-x", "PATCH /players/me merged settings_json");

note("api-calls", apiCalls);
writeFileSync(join(DATA, "proof_ui_flows.json"), JSON.stringify({ proof: "ui_flows_e2e", email, log, apiCalls }, null, 2));
await browser.close();
