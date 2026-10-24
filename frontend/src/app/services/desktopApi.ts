import type { DesktopAPI } from '../types/contracts';

export function desktopApi(): DesktopAPI {
  if (!window.paperAgent) {
    throw new Error('桌面服务桥接不可用。请通过 Electron 启动 Paper Agent。');
  }
  return window.paperAgent;
}
