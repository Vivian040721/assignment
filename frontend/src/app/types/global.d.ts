import type { DesktopAPI } from './contracts';

declare global {
  interface Window {
    paperAgent?: DesktopAPI;
  }
}

export {};
