import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

/**
 * Release artifact audit for the desktop role.
 *
 * The packaged app must never carry credentials or personal research data:
 * the bundle is built locally and shipped to machines that may be shared, and
 * `docs/运行与验收.md` treats "do not commit keys, config.json or user
 * research material" as a hard rule. This makes that rule verifiable after
 * `scripts/build-backend.ps1` and `electron-builder` have run.
 *
 * The check runs only when the artifacts exist, so a clean checkout without a
 * built release still passes.
 */
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

const SECRET_PATTERNS = [
  [/sk-[A-Za-z0-9]{20,}/, 'an OpenAI style API key'],
  [/gh[o|p|s|u]_[A-Za-z0-9]{20,}/, 'a GitHub token'],
  [/AKIA[0-9A-Z]{16}/, 'an AWS access key'],
  [/-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----/, 'a private key block'],
];
const SECRET_ENV = /^\s*(?:PAPER_AGENT_API_KEY|DEEPSEEK_API_KEY|OPENAI_API_KEY|SEMANTIC_SCHOLAR_API_KEY|OPENALEX_API_KEY|ANTHROPIC_API_KEY)\s*=\s*\S+/m;
const LITERAL_ASSIGNMENT = /\b(?:api_key|secret|token)\s*=\s*["']?[A-Za-z0-9_-]{24,}/;
const DATA_FILES = new Set(['config.json', '.env', 'service.lock', 'service.log']);
const VENDORED = ['_internal', 'dist-info', 'licenses'];

function walk(directory) {
  const output = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) output.push(...walk(full));
    else if (entry.isFile()) output.push(full);
  }
  return output;
}

function auditTree(directory) {
  const problems = [];
  for (const file of walk(directory)) {
    const relative = path.relative(directory, file).replace(/\\/g, '/');
    const lowered = relative.toLowerCase();
    if (VENDORED.some(part => lowered.includes(part))) continue;
    if (DATA_FILES.has(path.basename(file).toLowerCase())) problems.push(`ships runtime data file ${relative}`);
    if (['.pdf', '.md'].includes(path.extname(file).toLowerCase())) problems.push(`ships document ${relative}`);
    if (statSync(file).size > 4 * 1024 * 1024) continue;
    const text = readFileSync(file, 'utf8');
    if (SECRET_ENV.test(text)) problems.push(`ships ${relative} with a populated secret environment variable`);
  }
  return problems;
}

function auditAsar(file) {
  const text = readFileSync(file, 'utf8');
  const problems = [];
  for (const [pattern, label] of SECRET_PATTERNS) {
    if (pattern.test(text)) problems.push(`app.asar contains ${label}`);
  }
  if (SECRET_ENV.test(text)) problems.push('app.asar contains a populated secret environment variable');
  if (LITERAL_ASSIGNMENT.test(text)) problems.push('app.asar contains a literal credential assignment');
  return problems;
}

test('packaged artifacts carry no credentials and no personal research data', (context) => {
  const targets = [
    path.join(root, 'data/release/backend/PaperAgent-service'),
    path.join(root, 'data/release/desktop-v022/win-unpacked/resources/backend'),
  ].filter(existsSync);
  const asar = path.join(root, 'data/release/desktop-v022/win-unpacked/resources/app.asar');
  if (targets.length === 0 && !existsSync(asar)) {
    context.skip('no packaged artifacts built yet (run scripts/build-backend.ps1 and electron-builder first)');
    return;
  }
  const problems = [];
  for (const target of targets) problems.push(...auditTree(target).map(item => `${path.basename(target)}: ${item}`));
  if (existsSync(asar)) problems.push(...auditAsar(asar));
  assert.deepEqual(problems, [], `packaged artifact problems:\n - ${problems.join('\n - ')}`);
});
