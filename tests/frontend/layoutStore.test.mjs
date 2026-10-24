import assert from 'node:assert/strict';
import path from 'node:path';
import Module from 'node:module';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const filename = path.join(root, 'frontend/src/app/layout/stores/layoutStore.ts');
const storage = new Map();
const fakeWindow = {
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => { storage.set(key, String(value)); },
  },
  addEventListener: () => {},
  setTimeout: (callback, milliseconds) => setTimeout(callback, milliseconds),
  clearTimeout: (timer) => clearTimeout(timer),
};

function loadLayoutStore() {
  const built = buildSync({
    entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs',
    write: false, logLevel: 'silent',
  });
  global.window = fakeWindow;
  const loaded = new Module(filename);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(built.outputFiles[0].text, filename);
  return loaded.exports.layoutStore;
}

test('layout store defaults survive malformed and versioned localStorage', () => {
  storage.clear();
  storage.set('paperAgent.layout.v1', '{not-json');
  let store = loadLayoutStore();
  assert.equal(store.getSnapshot().sidebarCollapsed, false);
  assert.equal(store.getSnapshot().sidebarWidth, 264);
  assert.deepEqual(store.getSnapshot().groups[0].tabs.map(tab => tab.id), ['panel:papers']);
  storage.set('paperAgent.layout.v1', JSON.stringify({ version: 99, layout: { sidebarCollapsed: true } }));
  store = loadLayoutStore();
  assert.equal(store.getSnapshot().sidebarCollapsed, false);
});

test('sidebar toggles are synchronous, bounded and persisted once', async () => {
  storage.clear();
  const store = loadLayoutStore();
  for (let index = 0; index < 10; index += 1) store.toggleSidebar();
  assert.equal(store.getSnapshot().sidebarCollapsed, false);
  store.toggleSidebar();
  assert.equal(store.getSnapshot().sidebarCollapsed, true);
  store.setSidebarWidth(120);
  assert.equal(store.getSnapshot().sidebarWidth, 220);
  store.setSidebarWidth(900);
  assert.equal(store.getSnapshot().sidebarWidth, 380);
  store.setDockWidth(1);
  assert.equal(store.getSnapshot().dockWidth, 310);
  await new Promise((resolve) => setTimeout(resolve, 260));
  const saved = JSON.parse(storage.get('paperAgent.layout.v1'));
  assert.equal(saved.version, 2);
  assert.equal(saved.layout.sidebarCollapsed, true);
  assert.equal(saved.layout.sidebarWidth, 380);
  assert.equal(saved.layout.dockWidth, 310);
});

test('v1 placeholder tabs migrate away while paper tabs and job identity remain', () => {
  storage.clear();
  storage.set('paperAgent.layout.v1', JSON.stringify({ version: 1, layout: {
    sidebarCollapsed: false, groups: [{ id: 'main', activeTabId: 'panel:activity', tabs: [
      { id: 'panel:papers', kind: 'papers', title: '论文' },
      { id: 'panel:report', kind: 'report', title: '研究报告' },
      { id: 'panel:activity', kind: 'activity', title: '活动记录' },
      { id: 'paper:old', kind: 'paper', title: '旧论文', paperId: 'old' },
    ] }],
  } }));
  const store = loadLayoutStore();
  const tabs = store.getSnapshot().groups[0].tabs;
  assert.deepEqual(tabs.map(tab => tab.id), ['panel:papers', 'paper:old']);
  assert.equal(store.getSnapshot().groups[0].activeTabId, 'panel:papers');
});

test('paper tabs retain their owning job identity', () => {
  storage.clear();
  const store = loadLayoutStore();
  store.openPaper('p1', 'Same paper', 'job-a');
  store.openPaper('p1', 'Same paper', 'job-b');
  const tabs = store.getSnapshot().groups[0].tabs.filter(tab => tab.kind === 'paper');
  assert.equal(tabs.length, 2);
  assert.notEqual(tabs[0].id, tabs[1].id);
  assert.equal(tabs[0].jobId, 'job-a');
  assert.equal(tabs[1].jobId, 'job-b');
});
