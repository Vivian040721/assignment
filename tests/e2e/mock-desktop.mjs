/**
 * Real Electron Main/Preload against deterministic Mock HTTP/Fake LLM fixtures.
 * This does not test real research networks, real LLMs, or answer quality.
 * Integrated UI checks run by default; set PAPER_AGENT_E2E_UI=0 for API-only checks.
 */
import { _electron as electron, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runMockUiChecks } from './mock-ui-checks.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'data/validation/v020', `mock-${Date.now()}`);
const profile = path.join(output, 'profile');
const descriptorPath = path.join(output, 'mock-endpoint.json');
await mkdir(profile, { recursive: true });
const pythonCandidates = [process.env.PAPER_AGENT_PYTHON,
  path.join(root, '.venv-v2', 'Scripts', 'python.exe'), path.join(root, '.venv-v2', 'bin', 'python')].filter(Boolean);
const python = pythonCandidates.find(candidate => existsSync(candidate));
assert.ok(python, 'The project Python is required only as the service-control shim launcher.');
const executablePath = process.env.PAPER_AGENT_ELECTRON_EXE || path.join(root, 'node_modules', 'electron', 'dist',
  process.platform === 'win32' ? 'electron.exe' : 'electron');
const env = { ...process.env,
  PAPER_AGENT_DESKTOP_DATA_DIR: profile,
  PAPER_AGENT_E2E_MOCK: '1',
  PAPER_AGENT_E2E_DESCRIPTOR: descriptorPath,
  PAPER_AGENT_E2E_ROOT: root,
  PAPER_AGENT_E2E_NODE: process.execPath,
  PAPER_AGENT_PYTHON: python,
  PAPER_AGENT_RENDERER_URL: '',
  PAPER_AGENT_MODE: 'demo',
  LLM_API_KEY: 'deterministic-fixture-not-a-key',
  DEEPSEEK_API_KEY: '',
  OPENALEX_API_KEY: '',
  PYTHONUTF8: '1',
  PYTHONPATH: [path.join(root, 'tests/e2e'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
};
delete env.ELECTRON_RUN_AS_NODE;
const report = { fixture: true, mock_http: true, fake_llm: true, live_api_calls: 0,
  ui_checks_requested: process.env.PAPER_AGENT_E2E_UI !== '0', checks: [], deferred: [], measurements: {} };
if (!report.ui_checks_requested) {
  report.deferred.push('Integrated UI brief/status/execution-log assertions');
  report.deferred.push('Integrated PDF viewer page-citation jump, paging, zoom and search');
}
const rendererErrors = [];
let endpoint;
let failure;
const started = process.hrtime.bigint();
const app = await electron.launch({ args: ['.', `--user-data-dir=${path.join(output, 'chromium')}`],
  executablePath: existsSync(executablePath) ? executablePath : undefined,
  cwd: root, env, timeout: 90_000, chromiumSandbox: true });
try {
  const page = await app.firstWindow({ timeout: 90_000 });
  page.on('pageerror', error => rendererErrors.push(error.message));
  await expect(page.getByRole('main', { name: '研究对话' })).toBeVisible({ timeout: 30_000 });
  report.measurements.electron_ready_ms = Number(process.hrtime.bigint() - started) / 1e6;
  endpoint = JSON.parse(await readFile(descriptorPath, 'utf8'));

  const bridge = await page.evaluate(() => ({
    node: typeof window.require,
    process: typeof window.process,
    bridge: Boolean(window.paperAgent),
    pdf: typeof window.paperAgent.readPdfArtifact,
    exposedKeys: Object.keys(window.paperAgent),
  }));
  assert.equal(bridge.node, 'undefined');
  assert.equal(bridge.process, 'undefined');
  assert.equal(bridge.bridge, true);
  assert.equal(bridge.pdf, 'function');
  assert.ok(!bridge.exposedKeys.some(key => /token|endpoint|path|file|readFile/i.test(key)));
  const preferences = await app.evaluate(({ BrowserWindow }) => {
    const value = BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences();
    return { sandbox: value.sandbox, contextIsolation: value.contextIsolation,
      nodeIntegration: value.nodeIntegration, webSecurity: value.webSecurity };
  });
  assert.deepEqual(preferences, { sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true });
  assert.equal((await page.evaluate(() => window.paperAgent.config())).default_execution_mode, 'auto');
  report.checks.push('real-electron-preload-sandbox-bridge-and-auto-config');

  const scenarioNames = ['success', 'partial', 'no_result', 'retry_recovered', 'retry_exhausted'];
  const jobs = await page.evaluate(async names => Object.fromEntries(await Promise.all(names.map(async name => {
    const job = await window.paperAgent.job(`job_${name}`);
    return [name, job];
  }))), scenarioNames);
  assert.equal(jobs.success.status, 'completed');
  assert.equal(jobs.success.delivery_status, 'result');
  assert.equal(jobs.success.result.brief.findings.length, 3);
  assert.ok(jobs.success.result.brief.findings.every(finding => finding.evidence.length > 0));
  assert.equal(jobs.success.result.brief.report_artifact_id, 'report_fixture');
  assert.equal(jobs.partial.status, 'partial_success');
  assert.equal(jobs.partial.delivery_status, 'preliminary_notes');
  assert.ok(jobs.partial.result.brief.limitations.length > 0);
  assert.equal(jobs.no_result.delivery_status, 'no_result');
  assert.equal(jobs.no_result.result.brief.findings.length, 0);
  assert.equal(jobs.no_result.result.report, null);
  assert.equal(jobs.no_result.result.brief.report_artifact_id, null);
  assert.ok(jobs.retry_recovered.events.some(event => event.message.includes('invalid_model_output')));
  assert.ok(jobs.retry_recovered.events.some(event => event.message.includes('retry recovered')));
  assert.equal(jobs.retry_recovered.delivery_status, 'result');
  assert.equal(jobs.retry_exhausted.delivery_status, 'preliminary_notes');
  assert.ok(jobs.retry_exhausted.result.errors.some(error => error.code === 'invalid_model_output'));
  assert.ok(jobs.retry_exhausted.result.brief.limitations[0].impact.length > 0);
  report.checks.push('mock-api-success-partial-no-result-retry-recovery-and-exhaustion');

  const pdf = await page.evaluate(async () => {
    const started = performance.now();
    const value = await window.paperAgent.readPdfArtifact('job_success', 'pdf_fixture');
    const elapsed = performance.now() - started;
    return { name: value.name, kind: value.kind, bytes: value.data.byteLength,
      uint8: value.data instanceof Uint8Array,
      header: new TextDecoder().decode(value.data.slice(0, 8)), elapsed };
  });
  assert.deepEqual({ name: pdf.name, kind: pdf.kind, uint8: pdf.uint8 }, {
    name: 'registered-paper.pdf', kind: 'pdf', uint8: true,
  });
  assert.ok(pdf.bytes > 500);
  assert.ok(pdf.header.startsWith('%PDF-'));
  report.measurements.mock_pdf_ipc_read_ms = pdf.elapsed;
  report.checks.push('controlled-registered-pdf-bytes-through-desktop-api');

  const rejectedReads = await page.evaluate(async () => {
    const attempts = [
      ['job_no_result', 'pdf_fixture'],
      ['job_success', '../outside.pdf'],
      ['job_success', 'C:\\Windows\\system.ini'],
      ['job_success', 'report_fixture'],
      ['job_success', 'misleading_pdf'],
    ];
    const results = [];
    for (const [jobId, artifactId] of attempts) {
      try {
        await window.paperAgent.readPdfArtifact(jobId, artifactId);
        results.push(false);
      } catch {
        results.push(true);
      }
    }
    return results;
  });
  assert.deepEqual(rejectedReads, [true, true, true, true, true]);
  const reportText = await page.evaluate(() => window.paperAgent.readArtifact('job_success', 'report_fixture'));
  assert.match(reportText.text, /Mock HTTP\/Fake LLM/);
  report.checks.push('cross-job-path-non-pdf-and-fake-pdf-reads-rejected');

  for (const executionMode of ['auto', 'parallel', 'sequential', 'concurrent']) {
    await page.evaluate(mode => window.paperAgent.sendMessage({ text: 'fixture:success', action: 'research',
      top_k: 3, execution_mode: mode, mode: 'demo' }), executionMode);
  }
  let mockState = await (await fetch(`${endpoint.url}/mock/state`, {
    headers: { Authorization: `Bearer ${endpoint.token}` },
  })).json();
  assert.deepEqual(mockState.submissions.slice(-4).map(item => item.execution_mode),
    ['auto', 'parallel', 'sequential', 'concurrent']);
  report.checks.push('all-execution-modes-cross-real-preload-with-legacy-alias');

  const reconnect = await page.evaluate(async () => {
    const submitted = await window.paperAgent.sendMessage({ text: 'fixture:reconnect', action: 'research',
      execution_mode: 'auto', mode: 'demo' });
    return new Promise((resolve, reject) => {
      const sequence = [];
      const errors = [];
      const timer = setTimeout(() => { off(); reject(new Error('Mock SSE reconnect timed out.')); }, 15_000);
      const off = window.paperAgent.subscribeJob(submitted.job_id, 0, value => {
        if (value.event) sequence.push(value.event.seq);
        if (value.error) errors.push(value.error);
        if (value.done) {
          clearTimeout(timer);
          off();
          resolve({ sequence, errors, jobId: submitted.job_id });
        }
      });
    });
  });
  assert.deepEqual(reconnect.sequence, [1, 2]);
  assert.equal(reconnect.errors.length, 1);
  assert.match(reconnect.errors[0], /重连/);
  mockState = await (await fetch(`${endpoint.url}/mock/state`, {
    headers: { Authorization: `Bearer ${endpoint.token}` },
  })).json();
  assert.equal(mockState.sse_connections.job_reconnect, 2);
  report.checks.push('real-preload-sse-disconnect-resume-after-last-seq-without-duplicates');

  const beforeReload = await page.evaluate(() => window.paperAgent.conversations());
  const submissionCount = mockState.submissions.length;
  await page.reload();
  await expect(page.getByRole('main', { name: '研究对话' })).toBeVisible({ timeout: 30_000 });
  const afterReload = await page.evaluate(() => window.paperAgent.conversations());
  assert.equal(afterReload.length, beforeReload.length);
  mockState = await (await fetch(`${endpoint.url}/mock/state`, {
    headers: { Authorization: `Bearer ${endpoint.token}` },
  })).json();
  assert.equal(mockState.submissions.length, submissionCount);
  assert.equal((await page.evaluate(id => window.paperAgent.job(id), reconnect.jobId)).delivery_status, 'result');
  report.checks.push('renderer-reload-restores-jobs-without-resubmitting');

  if (report.ui_checks_requested) {
    await runMockUiChecks(page, jobs);
    report.checks.push('integrated-mock-ui-status-execution-log-and-pdf-page-jump');
  }
  assert.deepEqual(rendererErrors, []);
} catch (error) {
  failure = error;
  report.failure = error.stack;
  try { await (await app.firstWindow()).screenshot({ path: path.join(output, 'failure.png') }); } catch { /* App may be unavailable. */ }
} finally {
  await app.close();
  try {
    endpoint ??= JSON.parse(await readFile(descriptorPath, 'utf8'));
    const headers = { Authorization: `Bearer ${endpoint.token}` };
    const alive = await fetch(`${endpoint.url}/api/service`, { headers });
    assert.equal(alive.status, 200);
    report.checks.push('mock-daemon-survives-renderer-and-window-close');
    const stopped = await fetch(`${endpoint.url}/api/service/shutdown`, { method: 'POST', headers });
    assert.equal(stopped.status, 200);
  } catch (error) {
    report.cleanup_error = error.message;
  }
  report.measurements.total_mock_e2e_ms = Number(process.hrtime.bigint() - started) / 1e6;
  await writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ output, checks: report.checks, deferred: report.deferred,
    measurements: report.measurements, failure: Boolean(failure),
    ...(report.cleanup_error ? { cleanup_error: report.cleanup_error } : {}) }, null, 2));
}
if (failure) throw failure;
