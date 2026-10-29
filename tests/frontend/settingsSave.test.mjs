import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import Module from 'node:module';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const settingsFile = path.join(root, 'frontend/src/app/utils/settingsSave.ts');
const component = readFileSync(path.join(root, 'frontend/src/app/layout/components/settings/SettingsPanel.tsx'), 'utf8');

const built = buildSync({ entryPoints: [settingsFile], bundle: true, platform: 'node', format: 'cjs', write: false, logLevel: 'silent' });
const loaded = new Module(settingsFile);
loaded.filename = settingsFile;
loaded.paths = Module._nodeModulePaths(path.dirname(settingsFile));
loaded._compile(built.outputFiles[0].text, settingsFile);
const { settingsUpdateFor, deepSeekDraftError } = loaded.exports;

const draft = {
  baseUrl: ' https://api.example.com ', model: ' model-v1 ', proxy: ' http://localhost:7890 ',
  topK: 5, concurrency: 3, llmTimeout: 180, httpTimeout: 30, thinking: true,
  apiKey: ' deepseek-private ', openAlexKey: ' openalex-private ',
  clearApiKey: false, clearOpenAlexKey: false,
};

test('saving DeepSeek submits only its own credentials and settings, not the OpenAlex draft', () => {
  assert.deepEqual(settingsUpdateFor('deepseek', draft), {
    base_url: 'https://api.example.com', model: 'model-v1', http_proxy: 'http://localhost:7890',
    default_top_k: 5, concurrency: 3, llm_timeout: 180, http_timeout: 30,
    thinking_enabled: true, api_key: 'deepseek-private',
  });
});

test('saving OpenAlex never changes DeepSeek credentials or settings', () => {
  assert.deepEqual(settingsUpdateFor('openalex', draft), { openalex_api_key: 'openalex-private' });
});

test('empty secret fields preserve existing credentials on either form', () => {
  const empty = { ...draft, apiKey: '  ', openAlexKey: '  ' };
  const deepseek = settingsUpdateFor('deepseek', empty);
  assert.equal(Object.hasOwn(deepseek, 'api_key'), false);
  assert.equal(Object.hasOwn(deepseek, 'clear_api_key'), false);
  assert.deepEqual(settingsUpdateFor('openalex', empty), {});
});

test('explicit deletion wins over typed secrets and never deletes the other service key', () => {
  const bothClear = { ...draft, clearApiKey: true, clearOpenAlexKey: true };
  const deepseek = settingsUpdateFor('deepseek', bothClear);
  assert.equal(deepseek.clear_api_key, true);
  assert.equal(Object.hasOwn(deepseek, 'api_key'), false);
  assert.equal(Object.hasOwn(deepseek, 'clear_openalex_api_key'), false);
  assert.deepEqual(settingsUpdateFor('openalex', bothClear), { clear_openalex_api_key: true });
});

test('DeepSeek rejects invalid numeric settings before saving', () => {
  for (const [key, bad] of [
    ['topK', 0], ['topK', 16], ['concurrency', 0], ['concurrency', 9],
    ['llmTimeout', 4], ['llmTimeout', 601], ['httpTimeout', 4], ['httpTimeout', 301],
    ['topK', Number.NaN], ['concurrency', Infinity], ['httpTimeout', 22.5],
  ]) {
    assert.ok(deepSeekDraftError({ ...draft, [key]: bad }), `${key}=${bad} should fail`);
  }
  assert.equal(deepSeekDraftError(draft), null);
  assert.equal(deepSeekDraftError({ ...draft, baseUrl: ' ' }), '请填写 API Base URL 和模型名称。');
});

test('settings forms save independently, retain unrelated unsaved edits, and show scoped errors', () => {
  assert.match(component, /onSubmit=\{event => void save\(event, 'deepseek'\)\}/);
  assert.match(component, /onSubmit=\{event => void save\(event, 'openalex'\)\}/);
  assert.match(component, /ownSave !== 'openalex'/);
  assert.match(component, /ownSave !== 'deepseek'/);
  assert.match(component, /error\?\.section === 'deepseek'/);
  assert.match(component, /error\?\.section === 'openalex'/);
  assert.match(component, /if \(saveInFlight\.current\) return/);
});
