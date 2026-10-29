import assert from 'node:assert/strict';
import fs from 'node:fs';
import Module, { createRequire } from 'node:module';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

// Exercise the actual store with the checked-in TypeScript compiler. This test
// intentionally does not need a browser, Electron, or live backend credentials.
const require = createRequire(import.meta.url);
const ts = require('typescript');
const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const appRoot = path.join(projectRoot, 'frontend/src/app');
const storePath = path.join(appRoot, 'stores/workspaceStore.ts');
const originalTsLoader = Module._extensions['.ts'];
const originalLoad = Module._load;

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const tick = () => new Promise(resolve => setImmediate(resolve));
const conversation = (id, jobId = null) => ({
  conversation_id: id, title: id, updated_at: '2026-10-28T12:00:00Z',
  status: 'running', job_id: jobId, messages: jobId ? [{ id: 'message', role: 'user', content: 'fixture', kind: 'chat', job_id: jobId }] : [], result: null,
});
const job = (id, status = 'running', events = [], conversationId = 'a') => ({
  job_id: id, session_id: 'session', conversation_id: conversationId,
  action: 'research', idea: 'Test research', status, delivery_status: null,
  result: null, events, artifacts: [], error: null,
  created_at: '2026-10-28T12:00:00Z', updated_at: '2026-10-28T12:00:00Z',
});
const event = (seq, message) => ({ seq, kind: 'status', message });

function loadStore(overrides = {}) {
  for (const name of Object.keys(require.cache)) {
    if (name.startsWith(appRoot)) delete require.cache[name];
  }
  const api = {
    info: async () => ({ status: 'ready' }),
    config: async () => ({ default_top_k: 5, default_execution_mode: 'auto' }),
    conversations: async () => [conversation('a', 'job-a')],
    conversation: async id => conversation(id, `job-${id}`),
    job: async id => job(id),
    sendMessage: async () => job('new-job'),
    cancelJob: async id => job(id, 'cancelled'),
    ...overrides,
  };
  global.window = { paperAgent: api };
  Module._extensions['.ts'] = (module, filename) => {
    const js = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      fileName: filename,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    module._compile(js, filename);
  };
  Module._load = function (request, parent, isMain) {
    if (request === 'react') return { useSyncExternalStore: () => {} };
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    return { store: require(storePath).workspaceStore, api };
  } finally {
    Module._extensions['.ts'] = originalTsLoader;
    Module._load = originalLoad;
  }
}

test('terminal job cannot regress to running on an out-of-order poll', async () => {
  const completed = job('job-a', 'completed', [event(4, 'done')]);
  const running = job('job-a', 'running', [event(1, 'start')]);
  const { store } = loadStore({ job: async () => completed, conversations: async () => [] });
  await store.selectConversation('a');
  assert.equal(store.getSnapshot().activeJob.status, 'completed');
  const polled = await store.refreshJob('job-a');
  assert.equal(polled.status, 'completed');
  // A stale response is distinct from the store's authoritative merged state.
  global.window.paperAgent.job = async () => running;
  await store.refreshJob('job-a');
  const state = store.getSnapshot();
  assert.equal(state.activeJob.status, 'completed');
  assert.equal(state.activeJob.events.at(-1).seq, 4);
});

test('SSE updates survive polling, and duplicate sequence replays cannot replace them', async () => {
  const snapshot = job('job-a', 'running', [event(1, 'old-1'), event(2, 'old-2')]);
  const { store } = loadStore({ job: async () => snapshot });
  await store.selectConversation('a');
  store.onJobEvent('job-a', event(2, 'new-2'));
  store.onJobEvent('job-a', event(5, 'new-5'));
  await store.refreshJob('job-a');
  assert.deepEqual(store.getSnapshot().activeJob.events.map(item => item.seq), [1, 2, 5]);
  assert.equal(store.getSnapshot().activeJob.events.find(item => item.seq === 2).message, 'new-2');
});

test('latest conversation list wins when concurrent requests complete out of order', async () => {
  const first = deferred();
  const second = deferred();
  let calls = 0;
  const { store } = loadStore({ conversations: () => (++calls === 1 ? first.promise : second.promise) });
  const slow = store.refreshConversations();
  const fast = store.refreshConversations();
  second.resolve([conversation('newer')]);
  await fast;
  first.resolve([conversation('older')]);
  await slow;
  assert.deepEqual(store.getSnapshot().conversations.map(item => item.conversation_id), ['newer']);
});

test('a failed obsolete conversation switch never overwrites a new session', async () => {
  const old = deferred();
  const { store } = loadStore({ conversation: id => id === 'old' ? old.promise : Promise.resolve(conversation(id)) });
  const oldSelection = store.selectConversation('old');
  await store.selectConversation('new');
  old.reject(new Error('obsolete request failed'));
  await oldSelection;
  assert.equal(store.getSnapshot().conversationId, 'new');
  assert.equal(store.getSnapshot().error, null);
});

test('initialization must not auto-select an obsolete conversation after user chooses new', async () => {
  const initialList = deferred();
  const { store } = loadStore({ conversations: () => initialList.promise });
  const boot = store.initialize();
  await tick();
  store.newConversation();
  initialList.resolve([conversation('old')]);
  await boot;
  assert.equal(store.getSnapshot().conversationId, null);
});

test('failed send from a previous selection must not show an error in the new session', async () => {
  const sending = deferred();
  const { store } = loadStore({ sendMessage: () => sending.promise });
  store.setDraft('original');
  const pending = store.sendMessage('original').catch(() => {});
  store.newConversation();
  sending.reject(new Error('old session timeout'));
  await pending;
  assert.equal(store.getSnapshot().error, null);
  assert.equal(store.getSnapshot().drafts.__new_conversation__, 'original');
});

test('an obsolete list request failure does not show an error after newer success', async () => {
  const first = deferred();
  let calls = 0;
  const { store } = loadStore({ conversations: () => (++calls === 1 ? first.promise : Promise.resolve([conversation('newer')])) });
  const slow = store.refreshConversations();
  await store.refreshConversations();
  first.reject(new Error('old list failure'));
  await slow;
  assert.deepEqual(store.getSnapshot().conversations.map(item => item.conversation_id), ['newer']);
  assert.equal(store.getSnapshot().error, null);
});

test('initialization must not overwrite a newer list or auto-select its stale first item', async () => {
  const first = deferred();
  let calls = 0;
  const { store } = loadStore({ conversations: () => (++calls === 1 ? first.promise : Promise.resolve([conversation('fresh')])) });
  const boot = store.initialize();
  await tick();
  await store.refreshConversations();
  first.resolve([conversation('stale')]);
  await boot;
  assert.equal(store.getSnapshot().conversationId, null);
  assert.deepEqual(store.getSnapshot().conversations.map(item => item.conversation_id), ['fresh']);
});

test('a failed old-job poll cannot overwrite the current conversation error', async () => {
  const poll = deferred();
  const { store } = loadStore({ job: id => id === 'job-a' ? Promise.resolve(job(id)) : Promise.resolve(job(id)) });
  await store.selectConversation('a');
  global.window.paperAgent.job = () => poll.promise;
  const pending = store.refreshJob('job-a').catch(() => {});
  store.newConversation();
  poll.reject(new Error('old job unavailable'));
  await pending;
  assert.equal(store.getSnapshot().error, null);
});

function loadRecoveryHook({ activeJobId = 'job-a', refreshJob = async () => {}, errors = [] } = {}) {
  const filename = path.join(appRoot, 'hooks/useJobRecovery.ts');
  const source = fs.readFileSync(filename, 'utf8').replaceAll('import.meta.hot', 'undefined');
  const js = ts.transpileModule(source, {
    fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  let cleanup;
  let callback;
  let subscribedAfter;
  let unsubscribeCount = 0;
  const api = {
    subscribeJob: (_jobId, after, listener) => {
      subscribedAfter = after;
      callback = listener;
      return () => { unsubscribeCount += 1; };
    },
  };
  const store = {
    refreshJob,
    getSnapshot: () => ({ activeJob: activeJobId ? { job_id: activeJobId } : null }),
    onJobEvent: () => {},
    setError: text => errors.push(text),
  };
  global.window = { paperAgent: api, setInterval: () => 123, clearInterval: () => {} };
  const loaded = new Module(filename);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  Module._load = function (request, parent, isMain) {
    if (request === 'react') return { useEffect: effect => { cleanup = effect(); } };
    if (request === '../services/desktopApi') return { desktopApi: () => api };
    if (request === '../stores/workspaceStore') return { workspaceStore: store };
    if (request === '../utils/status') return { isJobActive: status => ['pending', 'queued', 'running'].includes(status) };
    return originalLoad.call(this, request, parent, isMain);
  };
  try { loaded._compile(js, filename); } finally { Module._load = originalLoad; }
  return {
    run: value => loaded.exports.useJobRecovery(value),
    emit: value => callback(value),
    cleanup: () => cleanup?.(),
    setActiveJob: id => { activeJobId = id; },
    subscribedAfter: () => subscribedAfter,
    unsubscribeCount: () => unsubscribeCount,
  };
}

test('recovery subscribes after the largest event sequence and never cancels the backend on cleanup', async () => {
  const recovery = loadRecoveryHook();
  recovery.run(job('job-a', 'running', [event(8, 'a'), event(2, 'b'), event(5, 'c')]));
  assert.equal(recovery.subscribedAfter(), 8);
  recovery.cleanup();
  assert.equal(recovery.unsubscribeCount(), 1);
});

test('recovery does not surface stale poll or SSE errors after switching jobs', async () => {
  const poll = deferred();
  const errors = [];
  const recovery = loadRecoveryHook({ refreshJob: () => poll.promise, errors });
  recovery.run(job('job-a'));
  recovery.setActiveJob('job-b');
  recovery.emit({ error: 'old SSE failure' });
  poll.reject(new Error('old poll failure'));
  await tick();
  assert.deepEqual(errors, []);
  recovery.cleanup();
});
