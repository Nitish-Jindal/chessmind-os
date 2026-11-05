// Real browser / FastAPI / PostgreSQL / Stockfish proof. No chess or API stubs.
// NODE_PATH=<playwright install>/node_modules node scripts/proof_engine_evaluation.mjs
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const { chromium } = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1100, height: 1000 } });
const evaluations = [];
const moves = [];
const errors = [];
let evaluationRequests = 0;
page.on('pageerror', e => errors.push(e.message));
page.on('request', r => { if (/\/sessions\/[^/]+\/evaluation$/.test(r.url())) evaluationRequests++; });
page.on('response', async r => {
  if (/\/sessions\/[^/]+\/evaluation$/.test(r.url()) && r.ok()) {
    const value = await r.json();
    if (value.analysis_available) {
      evaluations.push(value);
      await page.evaluate(fen => { window.__proofEvaluationFen = fen; }, value.current_fen);
    }
  }
  if (/\/sessions\/[^/]+\/move$/.test(r.url()) && r.ok()) moves.push(await r.json());
});
try {
  await page.goto(process.argv[2] ?? 'http://127.0.0.1:5173');
  await page.getByRole('button', { name: /need an account/i }).click();
  await page.getByLabel('Display name').fill('Engine isolation proof');
  await page.getByLabel('Email').fill(`engine-proof-${Date.now()}@chessmind.example.com`);
  await page.getByLabel('Password').fill('proofpassword123');
  await page.getByRole('button', { name: 'Register', exact: true }).click();
  await page.getByRole('heading', { name: 'Start a session' }).waitFor();
  await page.getByLabel("Opponent's opening").fill('Italian Game');
  await page.getByLabel('Opponent style').selectOption('mainline');
  await page.getByRole('button', { name: 'Start', exact: true }).click();
  await page.waitForURL(/\/session\//);
  const bar = page.getByRole('img', { name: /Evaluation bar/ });
  await bar.waitFor({ timeout: 20000 });
  mkdirSync('scripts/data/engine-isolation', { recursive: true });
  await page.screenshot({ path: 'scripts/data/engine-isolation/before-move.png' });
  const beforeCount = evaluationRequests;
  // Observe more than two pending-poll intervals once a valid result exists.
  await page.waitForTimeout(4500);
  assert.equal(evaluationRequests, beforeCount, 'unchanged settled FEN must not poll');
  const moveResponse = page.waitForResponse(r => /\/move$/.test(r.url()) && r.request().method() === 'POST');
  await page.getByLabel('Type a move').fill('e4');
  const started = performance.now();
  await page.getByLabel('Type a move').press('Enter');
  const response = await moveResponse;
  assert.equal(response.status(), 200);
  const latencyMs = performance.now() - started;
  const move = await response.json();
  await bar.waitFor({ timeout: 20000 });
  await page.waitForFunction(fen => window.__proofEvaluationFen === fen, move.current_fen);
  await bar.waitFor({ timeout: 20000 });
  const afterCount = evaluationRequests;
  await page.waitForTimeout(4500);
  assert.equal(evaluationRequests, afterCount);
  assert.ok(evaluations.some(e => e.current_fen === move.current_fen));
  assert.notEqual(evaluations[0].current_fen, move.current_fen);
  assert.equal(errors.length, 0, errors.join('\n'));
  await page.screenshot({ path: 'scripts/data/engine-isolation/after-move.png' });
  const evidence = { latencyMs, evaluationRequests, beforeCount, afterCount, evaluations, moves, errors,
    bar: await page.getByLabel('Position evaluation', { exact: true }).innerText() };
  writeFileSync('scripts/data/engine-isolation/browser.json', JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence, null, 2));
} finally {
  await browser.close();
}
