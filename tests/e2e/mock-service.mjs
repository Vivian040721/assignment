/**
 * Deterministic local HTTP/Fake-LLM fixture for Electron integration tests.
 * No research provider or LLM is contacted. This is not a live-quality test.
 */
import { randomBytes } from 'node:crypto';
import http from 'node:http';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const args = process.argv.slice(2);
function argument(name) {
  const index = args.indexOf(name);
  if (index < 0 || !args[index + 1]) throw new Error(`missing ${name}`);
  return args[index + 1];
}
const descriptorPath = path.resolve(argument('--descriptor'));
const dataRoot = path.resolve(argument('--data-root'));
const artifactRoot = path.join(dataRoot, 'registered-artifacts');
await mkdir(artifactRoot, { recursive: true });

function makePdf(labels) {
  const objects = [];
  const pageReferences = [];
  const fontObject = 3;
  labels.forEach((label, index) => {
    const pageObject = 4 + index * 2;
    const contentObject = pageObject + 1;
    pageReferences.push(`${pageObject} 0 R`);
    const stream = `BT /F1 18 Tf 72 720 Td (${label}) Tj ET`;
    objects[pageObject] = `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 ${fontObject} 0 R >> >> /Contents ${contentObject} 0 R >>`;
    objects[contentObject] = `<< /Length ${Buffer.byteLength(stream, 'ascii')} >>\nstream\n${stream}\nendstream`;
  });
  objects[1] = '<< /Type /Catalog /Pages 2 0 R >>';
  objects[2] = `<< /Type /Pages /Kids [${pageReferences.join(' ')}] /Count ${labels.length} >>`;
  objects[fontObject] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>';
  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  for (let index = 1; index < objects.length; index += 1) {
    offsets[index] = Buffer.byteLength(pdf, 'ascii');
    pdf += `${index} 0 obj\n${objects[index]}\nendobj\n`;
  }
  const xrefOffset = Buffer.byteLength(pdf, 'ascii');
  pdf += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for (let index = 1; index < objects.length; index += 1) {
    pdf += `${String(offsets[index]).padStart(10, '0')} 00000 n \n`;
  }
  pdf += `trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return Buffer.from(pdf, 'ascii');
}

const pdfPath = path.join(artifactRoot, 'registered-paper.pdf');
const reportPath = path.join(artifactRoot, 'registered-report.md');
const misleadingPath = path.join(artifactRoot, 'not-a-pdf.pdf');
await Promise.all([
  writeFile(pdfPath, makePdf(['FIXTURE PAGE ONE', 'FIXTURE PAGE TWO', 'FIXTURE PAGE THREE'])),
  writeFile(reportPath, '# Fixture report\n\nSynthetic Mock HTTP/Fake LLM content only.\n'),
  writeFile(misleadingPath, 'not a PDF'),
]);

const pdfArtifact = { artifact_id: 'pdf_fixture', name: 'registered-paper.pdf', kind: 'pdf', paper_id: 'paper_1' };
const reportArtifact = { artifact_id: 'report_fixture', name: 'registered-report.md', kind: 'report' };
const misleadingArtifact = { artifact_id: 'misleading_pdf', name: 'not-a-pdf.pdf', kind: 'pdf', paper_id: 'paper_1' };
const registeredPaths = new Map([
  [pdfArtifact.artifact_id, pdfPath],
  [reportArtifact.artifact_id, reportPath],
  [misleadingArtifact.artifact_id, misleadingPath],
]);
const evidence = { paper_id: 'paper_1', pdf_artifact_id: pdfArtifact.artifact_id, page_number: 2,
  quote: 'FIXTURE PAGE TWO', evidence_type: 'author_claim' };
const paper = { paper_id: 'paper_1', title: 'Deterministic fixture paper', authors: ['Fixture Author'],
  year: 2026, abstract: 'Synthetic fixture only.', doi: null, arxiv_id: null, arxiv_version: null,
  paper_url: null, pdf_url: null, pdf_urls: [], sources: ['mock'], source_records: [],
  citation_count: null, initial_score: 1, final_score: 1, rank: 1,
  selection_reason: 'Fixture selected deterministically.', ranking_reason: 'No live ranking.',
  status: 'converted', pdf_artifact_id: pdfArtifact.artifact_id, markdown_artifact_id: null,
  acquisition_status: 'downloaded', conversion_status: 'converted', reading_status: 'fulltext',
  page_map: [{ page_number: 1, start_char: 0, end_char: 10 }, { page_number: 2, start_char: 10, end_char: 20 }],
  fulltext_quality: { complete: true, synthetic: true }, metadata: { synthetic: true }, errors: [] };
const unavailablePaper = { ...paper, paper_id: 'paper_unavailable', title: 'Unavailable fixture paper',
  status: 'download_failed', pdf_artifact_id: null, acquisition_status: 'unavailable',
  conversion_status: 'failed', reading_status: 'metadata_only', page_map: [],
  fulltext_quality: { complete: false, synthetic: true },
  error: 'Fixture full text unavailable.' };
const summary = { paper_id: 'paper_1', title: paper.title, summary_scope: 'fulltext',
  markdown: 'Synthetic fixture summary.', evidence: [evidence], warnings: [], errors: [],
  summary_artifact_id: null };
const brief = (findingCount, limitations = []) => ({
  findings: Array.from({ length: findingCount }, (_, index) => ({
    statement: `Fixture finding ${index + 1}`, evidence: [evidence],
  })),
  key_papers: [{ paper_id: 'paper_1', selection_reason: 'Fixture only.', evidence_level: 'fulltext' }],
  next_step: { action: 'Inspect fixture page two.', rationale: 'Deterministic E2E check.' },
  limitations,
  report_artifact_id: reportArtifact.artifact_id,
});
const result = (scenario, deliveryStatus, researchBrief, errors = []) => ({
  run_id: `run_${scenario}`, idea: `fixture:${scenario}`, query: `fixture:${scenario}`, mode: 'demo',
  execution_mode: 'sequential', requested_execution_mode: 'auto', top_k: 3,
  status: deliveryStatus === 'no_result' ? 'failed' : deliveryStatus === 'result' ? 'completed' : 'partial_success',
  delivery_status: deliveryStatus, brief: researchBrief,
  papers: deliveryStatus === 'no_result' ? [unavailablePaper] : deliveryStatus === 'preliminary_notes' ? [paper, unavailablePaper] : [paper],
  selected_papers: deliveryStatus === 'no_result' ? [] : [paper],
  summaries: deliveryStatus === 'no_result' ? [] : [summary],
  report: deliveryStatus === 'no_result' ? null : { markdown: '# Fixture report', report_artifact_id: reportArtifact.artifact_id },
  source_counts: { mock: 1 }, metrics: { synthetic: true, fake_llm: true }, errors, timings: [], tool_calls: [],
});
function events(messages) {
  return messages.map((message, index) => ({ seq: index + 1, timestamp: `2026-10-27T00:00:0${index}Z`,
    kind: message.includes('invalid_model_output') ? 'error' : 'status', message, stage: 'fixture' }));
}
function makeJob(scenario, deliveryStatus, researchBrief, eventMessages, errors = []) {
  const jobId = `job_${scenario}`;
  return {
    job_id: jobId, session_id: `session_${scenario}`, conversation_id: `conversation_${scenario}`,
    action: 'research', idea: `fixture:${scenario}`,
    status: deliveryStatus === 'no_result' ? 'failed' : deliveryStatus === 'result' ? 'completed' : 'partial_success',
    delivery_status: deliveryStatus, result: result(scenario, deliveryStatus, researchBrief, errors),
    events: events(eventMessages), artifacts: [], error: errors[0]?.message ?? null,
    created_at: '2026-10-27T00:00:00Z', updated_at: '2026-10-27T00:00:05Z',
  };
}

const jobs = new Map();
jobs.set('success', makeJob('success', 'result', brief(3),
  ['Fixture started', 'Fixture completed with three supported findings']));
jobs.set('partial', makeJob('partial', 'preliminary_notes', brief(1, [{
  description: 'One fixture paper was unavailable.', impact: 'Coverage remains partial.', paper_ids: ['paper_unavailable'],
}]), ['Fixture started', 'invalid_model_output: temporary fixture failure', 'Fixture retained reliable partial material'],
[{ code: 'fulltext_unavailable', stage: 'fulltext', message: 'One fixture paper was unavailable.', retryable: false }]));
jobs.set('no_result', makeJob('no_result', 'no_result', {
  findings: [], key_papers: [], next_step: { action: 'Provide readable source PDFs.', rationale: 'No reliable fixture evidence.' },
  limitations: [{ description: 'Full text and report are unavailable.', impact: 'No answer can be delivered.' }],
  report_artifact_id: null,
}, ['Fixture started', 'Full text unavailable', 'Report unavailable'],
[{ code: 'no_readable_evidence', stage: 'report', message: 'No readable evidence.', retryable: false }]));
jobs.set('retry_recovered', makeJob('retry_recovered', 'result', brief(2),
  ['Fixture started', 'invalid_model_output: attempt one', 'Fake LLM retry recovered', 'Fixture completed']));
jobs.set('retry_exhausted', makeJob('retry_exhausted', 'preliminary_notes', brief(1, [{
  description: 'Fake LLM retries exhausted.', impact: 'The affected paper is not covered.', paper_ids: ['paper_1'],
}]), ['Fixture started', 'invalid_model_output: repeated fixture failure', 'invalid_model_output: repeated fixture failure', 'Fixture retries exhausted'],
[
  { code: 'invalid_model_output', stage: 'analysis', message: 'Fake LLM retries exhausted.', retryable: true },
  { code: 'invalid_model_output', stage: 'analysis', message: 'Fake LLM retries exhausted.', retryable: true },
]));
jobs.set('reconnect', makeJob('reconnect', 'result', brief(1),
  ['First event before disconnect', 'Second event after reconnect']));
for (const scenario of ['success', 'partial', 'no_result', 'retry_recovered', 'retry_exhausted', 'reconnect']) {
  const job = jobs.get(scenario);
  job.artifacts = scenario === 'no_result' ? [] : [pdfArtifact, reportArtifact, misleadingArtifact];
}
const visibleScenarios = ['success', 'partial', 'no_result', 'retry_recovered', 'retry_exhausted'];
const submissions = [];
const sseConnections = new Map();
const token = randomBytes(48).toString('base64url');
const publicConfig = { provider: 'fixture', base_url: 'http://127.0.0.1:9', model: 'fake-llm',
  api_key_configured: false, openalex_api_key_configured: false, thinking_enabled: false,
  http_proxy: '', default_top_k: 5, default_execution_mode: 'auto', concurrency: 2,
  llm_timeout: 60, http_timeout: 30 };

function publicJob(job) {
  return structuredClone(job);
}
function conversationFor(job) {
  return { conversation_id: job.conversation_id, title: `Fixture ${job.idea.slice(8)}`,
    updated_at: job.updated_at, status: job.status, job_id: job.job_id, read_only: false,
    messages: [{ id: `message_${job.job_id}`, role: 'user', content: job.idea, kind: 'research', job_id: job.job_id }],
    result: job.result, result_job_id: job.job_id };
}
function json(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
  response.end(body);
}
async function body(request) {
  let text = '';
  for await (const chunk of request) {
    text += chunk;
    if (text.length > 100_000) throw new Error('request too large');
  }
  return text ? JSON.parse(text) : {};
}
function resolveArtifact(job, artifactId) {
  if (!job || !job.artifacts.some(item => item.artifact_id === artifactId) || !registeredPaths.has(artifactId)) return null;
  const item = job.artifacts.find(artifact => artifact.artifact_id === artifactId);
  return { item, path: registeredPaths.get(artifactId) };
}
function streamEvents(request, response, job, after) {
  const count = (sseConnections.get(job.job_id) ?? 0) + 1;
  sseConnections.set(job.job_id, count);
  response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
  response.flushHeaders?.();
  const pending = job.events.filter(event => event.seq > after);
  const writeEvent = event => response.write(`id: ${event.seq}\nevent: update\ndata: ${JSON.stringify(event)}\n\n`);
  if (job.job_id === 'job_reconnect' && count === 1) {
    if (pending[0]) writeEvent(pending[0]);
    setImmediate(() => response.socket.destroy());
    return;
  }
  for (const event of pending) writeEvent(event);
  response.write(`event: done\ndata: ${JSON.stringify({ job_id: job.job_id, status: job.status })}\n\n`);
  response.end();
}

const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (request.headers.authorization !== `Bearer ${token}`) return json(response, 401, { error: 'Unauthorized fixture request.' });
    if (url.pathname === '/mock/state' && request.method === 'GET') {
      return json(response, 200, { submissions, sse_connections: Object.fromEntries(sseConnections) });
    }
    if (url.pathname === '/api/service' && request.method === 'GET') {
      return json(response, 200, { version: '0.3.0', api_version: 3, pid: process.pid,
        active_jobs: 0, data_root: dataRoot, config_path: path.join(dataRoot, 'config.json') });
    }
    if (url.pathname === '/api/service/shutdown' && request.method === 'POST') {
      json(response, 200, { status: 'stopping' });
      setImmediate(() => server.close());
      return;
    }
    if (url.pathname === '/api/config' && request.method === 'GET') return json(response, 200, publicConfig);
    if (url.pathname === '/api/config' && request.method === 'PUT') {
      const values = await body(request);
      for (const key of ['base_url', 'model', 'thinking_enabled', 'http_proxy', 'default_top_k', 'concurrency', 'llm_timeout', 'http_timeout']) {
        if (values[key] !== undefined) publicConfig[key] = values[key];
      }
      if (values.api_key) publicConfig.api_key_configured = true;
      if (values.clear_api_key) publicConfig.api_key_configured = false;
      return json(response, 200, publicConfig);
    }
    if (url.pathname === '/api/conversations' && request.method === 'GET') {
      const summaries = [...jobs.values()].filter(job => visibleScenarios.some(name => job.job_id === `job_${name}`) || submissions.some(item => item.job_id === job.job_id))
        .map(job => conversationFor(job));
      return json(response, 200, summaries.map(({ messages, result, result_job_id, ...summary }) => summary));
    }
    if (url.pathname === '/api/conversations/messages' && request.method === 'POST') {
      const values = await body(request);
      if (!['auto', 'parallel', 'sequential', 'concurrent', undefined].includes(values.execution_mode)) {
        return json(response, 422, { error: 'Invalid fixture execution mode.' });
      }
      const scenario = typeof values.text === 'string' && values.text.startsWith('fixture:') ? values.text.slice(8) : 'success';
      const job = jobs.get(scenario);
      if (!job) return json(response, 404, { error: 'Unknown fixture scenario.' });
      submissions.push({ job_id: job.job_id, execution_mode: values.execution_mode ?? null, top_k: values.top_k ?? null });
      return json(response, 202, publicJob(job));
    }
    let match = url.pathname.match(/^\/api\/conversations\/([A-Za-z0-9_-]+)$/);
    if (match && request.method === 'GET') {
      const job = [...jobs.values()].find(item => item.conversation_id === match[1]);
      return job ? json(response, 200, conversationFor(job)) : json(response, 404, { error: 'Unknown fixture conversation.' });
    }
    match = url.pathname.match(/^\/api\/jobs\/([A-Za-z0-9_-]+)$/);
    if (match && request.method === 'GET') {
      const job = jobs.get(match[1].slice(4));
      return job ? json(response, 200, publicJob(job)) : json(response, 404, { error: 'Unknown fixture job.' });
    }
    match = url.pathname.match(/^\/api\/jobs\/([A-Za-z0-9_-]+)\/events$/);
    if (match && request.method === 'GET') {
      const job = jobs.get(match[1].slice(4));
      if (!job) return json(response, 404, { error: 'Unknown fixture job.' });
      const after = Number(url.searchParams.get('after') ?? 0);
      return streamEvents(request, response, job, Number.isInteger(after) ? after : 0);
    }
    match = url.pathname.match(/^\/api\/jobs\/([A-Za-z0-9_-]+)\/artifacts\/([A-Za-z0-9_-]+)\/(location|text)$/);
    if (match && request.method === 'GET') {
      const job = jobs.get(match[1].slice(4));
      const resolved = resolveArtifact(job, match[2]);
      if (!resolved) return json(response, 404, { error: 'Artifact is not registered for this job.' });
      if (match[3] === 'location') {
        return json(response, 200, { path: resolved.path, name: resolved.item.name, kind: resolved.item.kind });
      }
      if (resolved.item.kind === 'pdf') return json(response, 415, { error: 'PDF is not text.' });
      return json(response, 200, { text: '# Fixture report\n\nSynthetic Mock HTTP/Fake LLM content only.\n',
        name: resolved.item.name, kind: resolved.item.kind, truncated: false });
    }
    return json(response, 404, { error: 'Unknown fixture route.' });
  } catch {
    return json(response, 500, { error: 'Mock fixture failure.' });
  }
});
server.listen(0, '127.0.0.1', async () => {
  const address = server.address();
  const url = `http://127.0.0.1:${address.port}`;
  await writeFile(descriptorPath, JSON.stringify({ url, mcp_url: `${url}/mcp`, pid: process.pid,
    api_version: 3, data_root: dataRoot, token }), { mode: 0o600 });
});
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => server.close(() => process.exit(0)));
