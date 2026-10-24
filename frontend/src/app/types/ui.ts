export type PanelKind = 'papers' | 'report' | 'activity' | 'metrics' | 'settings' | 'paper';
export type LayoutTab = {
  id: string;
  kind: PanelKind;
  title: string;
  paperId?: string;
  jobId?: string;
};
export type DockGroupId = 'main' | 'secondary';
export type DockGroup = {
  id: DockGroupId;
  tabs: LayoutTab[];
  activeTabId: string | null;
};
