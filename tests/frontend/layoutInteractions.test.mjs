import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import Module from 'node:module';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const storeFile = path.join(root, 'frontend/src/app/layout/stores/layoutStore.ts');
const storage = new Map();
const fakeWindow = {
  localStorage: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
  },
  addEventListener: () => {},
  setTimeout: (callback, milliseconds) => setTimeout(callback, milliseconds),
  clearTimeout: timer => clearTimeout(timer),
};

function loadStore() {
  storage.clear();
  global.window = fakeWindow;
  const built = buildSync({ entryPoints: [storeFile], bundle: true, platform: 'node', format: 'cjs', write: false, logLevel: 'silent' });
  const loaded = new Module(storeFile);
  loaded.filename = storeFile;
  loaded.paths = Module._nodeModulePaths(path.dirname(storeFile));
  loaded._compile(built.outputFiles[0].text, storeFile);
  return loaded.exports.layoutStore;
}

test('non-finite layout resize values are ignored before they reach renderer state', () => {
  const store = loadStore();
  const initial = store.getSnapshot();
  for (const invalid of [NaN, Infinity, -Infinity]) {
    store.setSidebarWidth(invalid);
    store.setDockWidth(invalid);
    store.setDockHeight(invalid);
  }
  assert.equal(store.getSnapshot().sidebarWidth, initial.sidebarWidth);
  assert.equal(store.getSnapshot().dockWidth, initial.dockWidth);
  assert.equal(store.getSnapshot().dockHeight, initial.dockHeight);
});

test('finite resize values are still rounded and bounded correctly', () => {
  const store = loadStore();
  store.setSidebarWidth(285.7);
  store.setDockWidth(700);
  store.setDockHeight(90);
  assert.equal(store.getSnapshot().sidebarWidth, 286);
  assert.equal(store.getSnapshot().dockWidth, 520);
  assert.equal(store.getSnapshot().dockHeight, 200);
});

test('an invalid tab destination index falls back to the end instead of moving a tab to the start', () => {
  const store = loadStore();
  store.openPaper('a', 'Paper A', 'job');
  store.openPaper('b', 'Paper B', 'job');
  store.moveTab('panel:papers', 'main', NaN);
  assert.deepEqual(store.getSnapshot().groups[0].tabs.map(tab => tab.id), [
    'paper:job:a', 'paper:job:b', 'panel:papers',
  ]);
  store.moveTab('paper:job:a', 'main', Infinity);
  assert.deepEqual(store.getSnapshot().groups[0].tabs.map(tab => tab.id), [
    'paper:job:b', 'panel:papers', 'paper:job:a',
  ]);
});

test('a collapsed or compact sidebar does not silently apply its hidden search filter', () => {
  const source = readFileSync(path.join(root, 'frontend/src/app/layout/components/sidebar/Sidebar.tsx'), 'utf8');
  assert.match(source, /const shortMode = compact \|\| layout\.sidebarCollapsed;/);
  assert.match(source, /const conversations = shortMode\s*\? workspace\.conversations\s*:\s*workspace\.conversations\.filter/);
});

test('dock popup menus close on Escape and outside clicks, restoring keyboard focus', () => {
  const source = readFileSync(path.join(root, 'frontend/src/app/layout/components/docking/RightDock.tsx'), 'utf8');
  assert.match(source, /addPanelRef\.current\?\.contains\(event\.target as Node\)/);
  assert.match(source, /addButtonRef\.current\?\.focus\(\)/);
  assert.match(source, /menuRef\.current\?\.querySelector<HTMLButtonElement>\('\.dock-overflow-trigger'\)\?\.focus\(\)/);
  assert.equal((source.match(/document\.addEventListener\('keydown', onEscape\)/g) ?? []).length, 2);
  assert.equal((source.match(/document\.removeEventListener\('keydown', onEscape\)/g) ?? []).length, 2);
});
