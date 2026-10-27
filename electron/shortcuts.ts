import type { WebContents } from 'electron';

/**
 * Window keyboard shortcuts for the main process.
 *
 * The native menu is intentionally absent, so the shortcuts users expect from a
 * desktop text workspace are handled here. The handler is exported as a pure
 * registration function so it can be unit tested with a fake WebContents:
 * Playwright dispatches keys through CDP, which does not reach Electron's
 * `before-input-event`, so end-to-end tests cannot observe this behaviour.
 *
 * Editing shortcuts stay available in packaged builds; reload and developer
 * tools shortcuts are development only.
 */
export interface WindowShortcutOptions {
  packaged: boolean;
  quit: () => void;
}

export function installWindowShortcuts(contents: WebContents, options: WindowShortcutOptions): void {
  contents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    const key = input.key.toLowerCase();
    const command = input.control || input.meta;
    if (command && !input.alt) {
      const actions: Record<string, () => void> = {
        a: () => contents.selectAll(),
        c: () => contents.copy(),
        v: () => contents.paste(),
        x: () => contents.cut(),
        z: () => input.shift ? contents.redo() : contents.undo(),
        y: () => contents.redo(),
      };
      const action = actions[key];
      if (action) {
        event.preventDefault();
        action();
        return;
      }
    }
    if (input.meta && key === 'q') {
      event.preventDefault();
      options.quit();
      return;
    }
    if (!options.packaged) {
      if (key === 'f5' || (command && key === 'r')) {
        event.preventDefault();
        if (input.shift) contents.reloadIgnoringCache();
        else contents.reload();
      } else if (key === 'f12' || (command && input.shift && key === 'i')) {
        event.preventDefault();
        contents.toggleDevTools();
      }
    }
  });
}
