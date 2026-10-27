import assert from 'node:assert/strict';
import path from 'node:path';
import Module from 'node:module';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

function loadTypeScript(relativePath) {
  const filename = path.join(root, relativePath);
  const built = buildSync({
    entryPoints: [filename],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    write: false,
    logLevel: 'silent',
  });
  const loaded = new Module(filename);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(built.outputFiles[0].text, filename);
  return loaded.exports;
}

const validators = loadTypeScript('electron/validators.ts');
const { SseParser } = loadTypeScript('electron/sse.ts');

test('legacy read-only conversations survive the allowlisted IPC boundary', () => {
  const legacy = { conversation_id: 'legacy_1', title: 'Legacy record', updated_at: '2026-10-24',
    status: 'partial', job_id: 'job_1', read_only: true, messages: [], result: null, result_job_id: 'job_1' };
  assert.equal(validators.validateConversationSummaries([legacy])[0].read_only, true);
  assert.equal(validators.validateConversation(legacy).read_only, true);
  assert.throws(() => validators.validateConversationSummaries([{ ...legacy, read_only: 'true' }]));
});

test('config validation keeps only the shared allowlist and checks known fields', () => {
  assert.deepEqual(validators.validateConfigUpdate({ model: 'deepseek-flash', unknown: 'ignored' }), {
    model: 'deepseek-flash',
  });
  assert.throws(() => validators.validateConfigUpdate({ api_key: 42 }), /格式不正确/);
  assert.throws(() => validators.validateConfigUpdate({ concurrency: 100 }), /范围/);
});

test('service endpoint accepts only authenticated loopback descriptors for API v3', () => {
  const endpoint = {
    url: 'http://127.0.0.1:54321', mcp_url: 'http://127.0.0.1:54321/mcp',
    pid: 12, api_version: 3, data_root: 'C:\\Users\\example\\PaperAgent', token: 't'.repeat(48),
  };
  assert.equal(validators.validatePrivateEndpoint(endpoint, 3).token, endpoint.token);
  assert.throws(() => validators.validatePrivateEndpoint({ ...endpoint, url: 'https://127.0.0.1:54321' }, 3));
  assert.throws(() => validators.validatePrivateEndpoint({ ...endpoint, url: 'http://localhost:54321' }, 3));
  assert.throws(() => validators.validatePrivateEndpoint({ ...endpoint, api_version: 2 }, 3));
});

test('external links require HTTP(S) and reject embedded credentials', () => {
  assert.equal(validators.validateExternalUrl('https://example.org/a?q=1'), 'https://example.org/a?q=1');
  assert.throws(() => validators.validateExternalUrl('javascript:alert(1)'));
  assert.throws(() => validators.validateExternalUrl('https://user:pass@example.org/'));
});

test('message fields and identifiers are bounded and typed', () => {
  assert.deepEqual(validators.validateSendMessage({
    text: 'question', action: 'chat', unexpected: 'discarded',
  }), { text: 'question', action: 'chat' });
  assert.throws(() => validators.validateSendMessage({ text: 'x', action: 'shell' }));
  assert.throws(() => validators.validateId('../config.json'));
  assert.equal(validators.validateAfter(0), 0);
  assert.throws(() => validators.validateAfter(-1));
});

test('v0.2.2 input limits match the backend contract', () => {
  assert.equal(validators.validateSendMessage({ text: 'x'.repeat(12000), action: 'research' }).text.length, 12000);
  assert.throws(() => validators.validateSendMessage({ text: 'x'.repeat(12001), action: 'research' }));
  assert.equal(validators.validateConfigUpdate({ default_top_k: 15, concurrency: 8 }).default_top_k, 15);
  assert.throws(() => validators.validateConfigUpdate({ default_top_k: 16 }));
  assert.throws(() => validators.validateConfigUpdate({ concurrency: 9 }));
});

test('SSE errors preserve the backend message and are bounded', () => {
  const long = '服务端错误'.repeat(100);
  assert.equal(validators.validateJobEvent({ job_id: 'job_1', error: long }, 'job_1').error.length, 300);
  assert.equal(validators.validateJobEvent({ job_id: 'job_1', error: '配置 409 可见' }, 'job_1').error, '配置 409 可见');
});

test('SSE parser handles chunk boundaries, comments, and multi-line data', () => {
  const parser = new SseParser();
  assert.deepEqual(parser.push(': heartbeat\r\n\neve'), []);
  assert.deepEqual(parser.push('nt: update\ndata: {"seq":1,\ndata: "kind":"status"}\n\n'), [{
    event: 'update', data: '{"seq":1,\n"kind":"status"}',
  }]);
});

test('job payloads enforce identifier, array and size bounds', () => {
  const base = { job_id: 'job_1', session_id: 'sess_1', idea: 'idea', status: 'running',
    result: null, events: [], artifacts: [], created_at: '2026-10-27', updated_at: '2026-10-27' };
  assert.equal(validators.validateJob(base).job_id, 'job_1');
  assert.throws(() => validators.validateJob({ ...base, job_id: 'job 1' }));
  assert.throws(() => validators.validateJob({ ...base, idea: 'x'.repeat(10001) }));
  assert.throws(() => validators.validateJob({ ...base, events: new Array(100001).fill({ seq: 1 }) }));
  assert.throws(() => validators.validateJob({
    ...base, artifacts: [{ artifact_id: 'bad id', name: 'a.md', kind: 'markdown' }],
  }));
  assert.throws(() => validators.validateJob({
    ...base, artifacts: [{ artifact_id: 'a_1', name: 'x'.repeat(501), kind: 'markdown' }],
  }));
});

test('artifact text and location validators bound content and require absolute paths', () => {
  assert.equal(validators.validateArtifactText({ text: 'x', name: 'a.md', kind: 'markdown', truncated: false }).text, 'x');
  assert.throws(() => validators.validateArtifactText({ text: 'x', name: 'a.md', kind: 'markdown', truncated: 'no' }));
  assert.throws(() => validators.validateArtifactText({ text: 'x'.repeat(2 * 1024 * 1024 + 1), name: 'a.md', kind: 'markdown', truncated: false }));
  const location = { path: 'C:\\data\\a.md', name: 'a.md', kind: 'markdown' };
  assert.equal(validators.validateArtifactLocation(location).path, location.path);
  assert.throws(() => validators.validateArtifactLocation({ ...location, path: 'data/a.md' }));
  assert.throws(() => validators.validateArtifactLocation({ ...location, path: 'x'.repeat(4097) }));
});

test('public config and service info reject missing or mistyped fields', () => {
  const config = { provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash',
    api_key_configured: false, openalex_api_key_configured: false, thinking_enabled: false,
    http_proxy: '', default_top_k: 5, default_execution_mode: 'concurrent', concurrency: 4,
    llm_timeout: 60, http_timeout: 30 };
  assert.equal(validators.validatePublicConfig(config).default_top_k, 5);
  const { default_top_k: _ignored, ...missingTopK } = config;
  assert.throws(() => validators.validatePublicConfig(missingTopK));
  assert.throws(() => validators.validatePublicConfig({ ...config, default_execution_mode: 'turbo' }));
  const info = { version: '0.2.2', api_version: 3, pid: 12, active_jobs: 0,
    data_root: 'C:\\data', config_path: 'C:\\data\\config.json' };
  assert.equal(validators.validateServiceInfo(info).version, '0.2.2');
  assert.throws(() => validators.validateServiceInfo({ ...info, pid: 0 }));
  assert.throws(() => validators.validateServiceInfo({ ...info, active_jobs: -1 }));
  assert.throws(() => validators.validateServiceInfo({ ...info, data_root: 42 }));
});

test('conversation messages stay bounded through the IPC boundary', () => {
  const conversation = { conversation_id: 'c_1', title: 't', updated_at: '2026-10-27', status: 'completed',
    messages: [{ id: 'm_1', role: 'user', content: 'hi', kind: 'chat' }], result: null };
  assert.equal(validators.validateConversation(conversation).messages.length, 1);
  assert.throws(() => validators.validateConversation({ ...conversation, messages: 'none' }));
  assert.throws(() => validators.validateConversation({
    ...conversation, messages: [{ ...conversation.messages[0], content: 'x'.repeat(2_000_001) }],
  }));
  assert.throws(() => validators.validateConversation({
    ...conversation, messages: [{ ...conversation.messages[0], job_id: 'bad id' }],
  }));
});
