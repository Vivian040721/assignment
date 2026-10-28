import { useSyncExternalStore } from 'react';
import type { DockGroup, DockGroupId, LayoutTab, PanelKind } from '../../types/ui';

const STORAGE_KEY = 'paperAgent.layout.v1';
const STORAGE_VERSION = 2;

export interface LayoutState {
  sidebarCollapsed: boolean;
  sidebarWidth: number;
  dockOpen: boolean;
  dockWidth: number;
  dockHeight: number;
  activeGroupId: DockGroupId;
  groups: DockGroup[];
  focusReading: boolean;
}

const DEFAULT_GROUPS: DockGroup[] = [{
  id: 'main',
  tabs: [{ id: 'panel:papers', kind: 'papers', title: '论文' }],
  activeTabId: 'panel:papers',
}];

function initialState(): LayoutState {
  const defaults: LayoutState = {
    sidebarCollapsed: false,
    sidebarWidth: 264,
    dockOpen: true,
    dockWidth: 386,
    dockHeight: 286,
    activeGroupId: 'main',
    groups: DEFAULT_GROUPS,
    focusReading: false,
  };
  if (typeof window === 'undefined') return defaults;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaults;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || !isRecord(parsed.layout)) return defaults;
    if (parsed.version === 1) return normalizeLayout(parsed.layout, defaults, 1);
    if (parsed.version !== STORAGE_VERSION) return defaults;
    return normalizeLayout(parsed.layout, defaults);
  } catch {
    return defaults;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isPanelKind(value: unknown): value is PanelKind {
  return value === 'papers' || value === 'report' || value === 'activity' || value === 'metrics' || value === 'settings' || value === 'paper';
}

function normalizeLayout(input: Record<string, unknown>, defaults: LayoutState, version = STORAGE_VERSION): LayoutState {
  const groupsValue = input.groups;
  if (!Array.isArray(groupsValue) || groupsValue.length < 1 || groupsValue.length > 2) return defaults;
  const usedIds = new Set<string>();
  const groups: DockGroup[] = [];
  for (let index = 0; index < groupsValue.length; index += 1) {
    const value = groupsValue[index];
    if (!isRecord(value)) return defaults;
    const id = value.id;
    if (id !== 'main' && id !== 'secondary') return defaults;
    if ((index === 0 && id !== 'main') || (index === 1 && id !== 'secondary')) return defaults;
    if (!Array.isArray(value.tabs) || value.tabs.length > 80) return defaults;
    const tabs: LayoutTab[] = [];
    for (const rawTab of value.tabs) {
      if (!isRecord(rawTab) || typeof rawTab.id !== 'string' || !isPanelKind(rawTab.kind) || typeof rawTab.title !== 'string') return defaults;
      if (usedIds.has(rawTab.id)) return defaults;
      if (rawTab.kind === 'paper' && (typeof rawTab.paperId !== 'string' || !rawTab.paperId)) return defaults;
      if (version === 1 && (rawTab.id === 'panel:report' || rawTab.id === 'panel:activity')) continue;
      usedIds.add(rawTab.id);
      tabs.push({
        id: rawTab.id, kind: rawTab.kind, title: rawTab.title,
        ...(typeof rawTab.paperId === 'string' ? { paperId: rawTab.paperId } : {}),
        ...(typeof rawTab.jobId === 'string' && rawTab.jobId ? { jobId: rawTab.jobId } : {}),
      });
    }
    const activeTabId = typeof value.activeTabId === 'string' && tabs.some(tab => tab.id === value.activeTabId)
      ? value.activeTabId
      : tabs[0]?.id ?? null;
    groups.push({ id, tabs, activeTabId });
  }
  const activeGroupId = input.activeGroupId === 'secondary' && groups.length === 2 ? 'secondary' : 'main';
  return {
    sidebarCollapsed: input.sidebarCollapsed === true,
    sidebarWidth: clampNumber(input.sidebarWidth, 264, 220, 380),
    dockOpen: input.dockOpen !== false,
    dockWidth: clampNumber(input.dockWidth, 386, 310, 520),
    dockHeight: clampNumber(input.dockHeight, 286, 200, 560),
    activeGroupId,
    groups,
    focusReading: false,
  };
}

function clampNumber(value: unknown, fallback: number, minimum: number, maximum: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(minimum, Math.min(maximum, value)) : fallback;
}

let state = initialState();
const listeners = new Set<() => void>();
let persistTimer: number | undefined;

function persistLayout(): void {
  try {
    const layout = {
      sidebarCollapsed: state.sidebarCollapsed,
      sidebarWidth: state.sidebarWidth,
      dockOpen: state.dockOpen,
      dockWidth: state.dockWidth,
      dockHeight: state.dockHeight,
      activeGroupId: state.activeGroupId,
      groups: state.groups,
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: STORAGE_VERSION, layout }));
  } catch {
    // Layout remains usable for the current window if storage is unavailable.
  }
}

if (typeof window !== 'undefined') window.addEventListener('pagehide', persistLayout);

function publish(next: LayoutState, persist = true): void {
  state = next;
  for (const listener of listeners) listener();
  if (persist && typeof window !== 'undefined') {
    if (persistTimer !== undefined) window.clearTimeout(persistTimer);
    persistTimer = window.setTimeout(() => {
      persistTimer = undefined;
      persistLayout();
    }, 180);
  }
}

function patch(update: Partial<LayoutState>): void {
  publish({ ...state, ...update });
}

function groupWithId(id: DockGroupId): DockGroup | undefined {
  return state.groups.find(group => group.id === id);
}

function withGroups(groups: DockGroup[], activeGroupId = state.activeGroupId): void {
  publish({ ...state, groups, activeGroupId, dockOpen: true });
}

const panelTabs: Record<Exclude<PanelKind, 'paper'>, LayoutTab> = {
  papers: { id: 'panel:papers', kind: 'papers', title: '论文' },
  report: { id: 'panel:report', kind: 'report', title: '研究报告' },
  activity: { id: 'panel:activity', kind: 'activity', title: '执行记录' },
  metrics: { id: 'panel:metrics', kind: 'metrics', title: '性能数据' },
  settings: { id: 'panel:settings', kind: 'settings', title: '设置' },
};

export const layoutStore = {
  getSnapshot: (): LayoutState => state,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  toggleSidebar(): void {
    patch({ sidebarCollapsed: !state.sidebarCollapsed });
  },
  setSidebarCollapsed(value: boolean): void {
    if (state.sidebarCollapsed !== value) patch({ sidebarCollapsed: value });
  },
  setSidebarWidth(value: number): void {
    const sidebarWidth = Math.max(220, Math.min(380, Math.round(value)));
    if (sidebarWidth !== state.sidebarWidth) patch({ sidebarWidth });
  },
  toggleDock(): void {
    patch({ dockOpen: !state.dockOpen });
  },
  openDock(): void {
    if (!state.dockOpen) patch({ dockOpen: true });
  },
  setFocusReading(value: boolean): void {
    if (state.focusReading === value) return;
    publish({ ...state, focusReading: value, dockOpen: value ? true : state.dockOpen }, false);
  },
  toggleFocusReading(): void {
    this.setFocusReading(!state.focusReading);
  },
  setDockWidth(value: number): void {
    const dockWidth = Math.max(310, Math.min(520, Math.round(value)));
    if (dockWidth !== state.dockWidth) patch({ dockWidth });
  },
  setDockHeight(value: number): void {
    const dockHeight = Math.max(200, Math.min(560, Math.round(value)));
    if (dockHeight !== state.dockHeight) patch({ dockHeight });
  },
  selectTab(groupId: DockGroupId, tabId: string): void {
    const group = groupWithId(groupId);
    if (!group || !group.tabs.some(tab => tab.id === tabId)) return;
    const groups = state.groups.map(group => group.id === groupId ? { ...group, activeTabId: tabId } : group);
    withGroups(groups, groupId);
  },
  activateGroup(groupId: DockGroupId): void {
    if (!groupWithId(groupId) || state.activeGroupId === groupId) return;
    patch({ activeGroupId: groupId, dockOpen: true });
  },
  openPanel(kind: Exclude<PanelKind, 'paper'>): void {
    const tab = panelTabs[kind];
    const existingGroup = state.groups.find(group => group.tabs.some(item => item.id === tab.id));
    if (existingGroup) {
      this.selectTab(existingGroup.id, tab.id);
      return;
    }
    const target = groupWithId(state.activeGroupId) ?? state.groups[0];
    const groups = state.groups.map(group => group.id === target.id
      ? { ...group, tabs: [...group.tabs, tab], activeTabId: tab.id }
      : group);
    withGroups(groups, target.id);
  },
  openPaper(paperId: string, title: string, jobId?: string): void {
    if (!paperId) return;
    const tab: LayoutTab = {
      id: `paper:${jobId ?? 'unscoped'}:${paperId}`, kind: 'paper',
      title: title.trim() || '论文详情', paperId, ...(jobId ? { jobId } : {}),
    };
    const existingGroup = state.groups.find(group => group.tabs.some(item => item.id === tab.id));
    if (existingGroup) {
      this.selectTab(existingGroup.id, tab.id);
      return;
    }
    const target = groupWithId(state.activeGroupId) ?? state.groups[0];
    withGroups(state.groups.map(group => group.id === target.id
      ? { ...group, tabs: [...group.tabs, tab], activeTabId: tab.id }
      : group), target.id);
  },
  closeTab(groupId: DockGroupId, tabId: string): void {
    const group = groupWithId(groupId);
    if (!group) return;
    const index = group.tabs.findIndex(tab => tab.id === tabId);
    if (index < 0) return;
    const tabs = group.tabs.filter(tab => tab.id !== tabId);
    const activeTabId = group.activeTabId === tabId ? tabs[Math.min(index, tabs.length - 1)]?.id ?? null : group.activeTabId;
    withGroups(state.groups.map(item => item.id === groupId ? { ...item, tabs, activeTabId } : item), groupId);
  },
  moveTab(tabId: string, destinationId: DockGroupId, destinationIndex?: number): void {
    const source = state.groups.find(group => group.tabs.some(tab => tab.id === tabId));
    const destination = groupWithId(destinationId);
    if (!source || !destination) return;
    const sourceIndex = source.tabs.findIndex(tab => tab.id === tabId);
    const tab = source.tabs[sourceIndex];
    let index = destinationIndex ?? destination.tabs.length;
    index = Math.max(0, Math.min(destination.tabs.length, Math.floor(index)));
    const sourceTabs = [...source.tabs];
    sourceTabs.splice(sourceIndex, 1);
    const sourceActive = source.activeTabId === tabId ? sourceTabs[Math.min(sourceIndex, sourceTabs.length - 1)]?.id ?? null : source.activeTabId;
    if (source.id === destination.id) {
      if (index > sourceIndex) index -= 1;
      sourceTabs.splice(index, 0, tab);
      if (index === sourceIndex) return;
      withGroups(state.groups.map(group => group.id === source.id
        ? { ...group, tabs: sourceTabs, activeTabId: tabId }
        : group), source.id);
      return;
    }
    const destinationTabs = [...destination.tabs];
    destinationTabs.splice(index, 0, tab);
    const groups = state.groups.map(group => {
      if (group.id === source.id) return { ...group, tabs: sourceTabs, activeTabId: sourceActive };
      if (group.id === destination.id) return { ...group, tabs: destinationTabs, activeTabId: tabId };
      return group;
    });
    withGroups(groups, destination.id);
  },
  splitActiveTab(): void {
    if (state.groups.length > 1) return;
    const source = groupWithId(state.activeGroupId) ?? state.groups[0];
    const tabId = source.activeTabId ?? source.tabs.at(-1)?.id;
    if (!tabId) return;
    this.splitTab(tabId);
  },
  splitTab(tabId: string): void {
    if (state.groups.length > 1) return;
    const source = state.groups.find(group => group.tabs.some(tab => tab.id === tabId));
    if (!source) return;
    const index = source.tabs.findIndex(tab => tab.id === tabId);
    const tab = source.tabs[index];
    const sourceTabs = source.tabs.filter(item => item.id !== tabId);
    const sourceActive = source.activeTabId === tabId ? sourceTabs[Math.min(index, sourceTabs.length - 1)]?.id ?? null : source.activeTabId;
    const groups: DockGroup[] = state.groups.map(group => group.id === source.id
      ? { ...group, tabs: sourceTabs, activeTabId: sourceActive }
      : group);
    groups.push({ id: 'secondary', tabs: [tab], activeTabId: tabId });
    withGroups(groups, 'secondary');
  },
  mergeGroups(): void {
    if (state.groups.length < 2) return;
    const main = groupWithId('main')!;
    const secondary = groupWithId('secondary')!;
    const merged = [...main.tabs, ...secondary.tabs];
    const activeTabId = state.activeGroupId === 'secondary' && secondary.activeTabId
      ? secondary.activeTabId
      : main.activeTabId ?? secondary.activeTabId;
    publish({ ...state, groups: [{ ...main, tabs: merged, activeTabId }], activeGroupId: 'main', dockOpen: true });
  },
};

export function useLayoutState(): LayoutState {
  return useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot, layoutStore.getSnapshot);
}
