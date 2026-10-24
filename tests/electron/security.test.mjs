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
