export const MIN_DOCK_TAB_WIDTH = 104;
export const MAX_DOCK_TAB_WIDTH = 184;
const GAP = 2;

export function measureDockTabs(available: number, count: number): { width: number; overflow: boolean } {
  if (count <= 0 || available <= 0) return { width: MAX_DOCK_TAB_WIDTH, overflow: false };
  const fit = Math.floor((available - GAP * Math.max(0, count - 1)) / count);
  return {
    width: Math.max(MIN_DOCK_TAB_WIDTH, Math.min(MAX_DOCK_TAB_WIDTH, fit)),
    overflow: fit < MIN_DOCK_TAB_WIDTH,
  };
}
