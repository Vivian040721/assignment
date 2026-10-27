import assert from 'node:assert/strict';
import path from 'node:path';
import Module from 'node:module';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

/**
 * Main-process window shortcut behaviour.
 *
 * Playwright dispatches keys through CDP, which never reaches Electron's
 * `before-input-event`, so this handler is verified here with a fake
 * WebContents instead of in the desktop E2E suite.
 */
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

const { installWindowShortcuts } = loadTypeScript('electron/shortcuts.ts');

function createContents() {
  const calls = [];
  const record = name => () => calls.push(name);
  const contents = {
    calls,
    handlers: {},
    on(event, handler) { (contents.handlers[event] ??= []).push(handler); },
    selectAll: record('selectAll'),
    copy: record('copy'),
    paste: record('paste'),
    cut: record('cut'),
    undo: record('undo'),
    redo: record('redo'),
    reload: record('reload'),
    reloadIgnoringCache: record('reloadIgnoringCache'),
    toggleDevTools: record('toggleDevTools'),
  };
  return contents;
}

function install(packaged) {
  const contents = createContents();
  let quitCount = 0;
  installWindowShortcuts(contents, { packaged, quit: () => { quitCount += 1; } });
  assert.equal(contents.handlers['before-input-event'].length, 1);
  const handler = contents.handlers['before-input-event'][0];
  const press = (input, type = 'keyDown') => {
    let prevented = false;
    handler({ preventDefault: () => { prevented = true; } },
      { type, key: '', control: false, meta: false, alt: false, shift: false, ...input });
    return prevented;
  };
  return { contents, press, quitCount: () => quitCount };
}

test('editing shortcuts stay available in packaged builds', () => {
  const { contents, press } = install(true);
  assert.equal(press({ key: 'a', control: true }), true);
  assert.equal(press({ key: 'c', control: true }), true);
  assert.equal(press({ key: 'v', control: true }), true);
  assert.equal(press({ key: 'x', control: true }), true);
  assert.equal(press({ key: 'z', control: true }), true);
  assert.equal(press({ key: 'z', control: true, shift: true }), true);
  assert.equal(press({ key: 'y', meta: true }), true);
  assert.deepEqual(contents.calls,
    ['selectAll', 'copy', 'paste', 'cut', 'undo', 'redo', 'redo']);
});

test('meta+q quits the app and is preventDefaulted', () => {
  const { contents, press, quitCount } = install(true);
  assert.equal(press({ key: 'q', meta: true }), true);
  assert.equal(quitCount(), 1);
  assert.deepEqual(contents.calls, []);
  assert.equal(press({ key: 'q', control: true }), false);  // Ctrl+Q is not the macOS quit key
  assert.equal(quitCount(), 1);
});

test('reload and developer tools shortcuts are development only', () => {
  const dev = install(false);
  assert.equal(dev.press({ key: 'F5' }), true);
  assert.equal(dev.press({ key: 'r', control: true }), true);
  assert.equal(dev.press({ key: 'r', control: true, shift: true }), true);
  assert.equal(dev.press({ key: 'F12' }), true);
  assert.equal(dev.press({ key: 'i', control: true, shift: true }), true);
  assert.deepEqual(dev.contents.calls,
    ['reload', 'reload', 'reloadIgnoringCache', 'toggleDevTools', 'toggleDevTools']);

  const packaged = install(true);
  assert.equal(packaged.press({ key: 'F5' }), false);
  assert.equal(packaged.press({ key: 'r', control: true }), false);
  assert.equal(packaged.press({ key: 'F12' }), false);
  assert.deepEqual(packaged.contents.calls, []);
});

test('unrelated keys and key-up events are ignored', () => {
  const { contents, press } = install(false);
  assert.equal(press({ key: 'Escape' }), false);
  assert.equal(press({ key: 'Tab' }), false);
  assert.equal(press({ key: 'F5' }, 'keyUp'), false);
  assert.equal(press({ key: 'a' }), false);  // no command modifier
  assert.deepEqual(contents.calls, []);
});
