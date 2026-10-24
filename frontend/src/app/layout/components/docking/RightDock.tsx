import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Activity, BookOpen, ChevronDown, ChevronLeft, ChevronRight, Columns2, FileText, Gauge, MoreHorizontal, Plus, Settings2, X } from 'lucide-react';
import type { CSSProperties, DragEvent, KeyboardEvent } from 'react';
import type { DockGroup, LayoutTab, PanelKind } from '../../../types/ui';
import { layoutStore, useLayoutState } from '../../stores/layoutStore';
import { ActivityPanel } from '../activity/ActivityPanel';
import { PaperList } from '../paper-reader/PaperList';
import { PaperReader } from '../paper-reader/PaperReader';
import { ResearchReport } from '../research-report/ResearchReport';
import { SettingsPanel } from '../settings/SettingsPanel';
import { MetricsPanel } from '../metrics/MetricsPanel';
import { measureDockTabs } from './tabMetrics';

const TAB_MIME = 'application/x-paper-agent-dock-tab';
const panelOptions: Array<{ kind: Exclude<PanelKind, 'paper'>; title: string; icon: typeof BookOpen }> = [
  { kind: 'papers', title: '论文', icon: BookOpen },
  { kind: 'report', title: '研究报告', icon: FileText },
  { kind: 'activity', title: '活动记录', icon: Activity },
  { kind: 'metrics', title: '性能数据', icon: Gauge },
];

export function RightDock({ hidden = false }: { hidden?: boolean }) {
  const layout = useLayoutState();
  const [addOpen, setAddOpen] = useState(false);
  const rootRef = useRef<HTMLElement | null>(null);
  const split = layout.groups.length > 1;
  const active = layout.groups.find(group => group.id === layout.activeGroupId);
  const canSplit = Boolean(active?.activeTabId) && !split;

  useEffect(() => {
    if (!rootRef.current) return;
    if (hidden) rootRef.current.setAttribute('inert', '');
    else rootRef.current.removeAttribute('inert');
  }, [hidden]);

  return <aside ref={rootRef} className={`right-dock ${split ? 'dock-split' : ''} ${hidden ? 'is-hidden' : ''}`} aria-label="资料 Dock" aria-hidden={hidden || undefined}>
    <header className="dock-header">
      <div className="dock-heading"><span className="dock-heading-mark"><BookOpen size={15}/></span><div><strong>研究资料</strong><small>{split ? '双组视图 · 拖动标签可移动' : '论文、报告与任务记录'}</small></div></div>
      <div className="dock-header-actions">
        <button className="dock-action" type="button" aria-label={split ? '合并分组' : '分成两个组'} title={split ? '合并分组' : '把当前标签分到第二组'} disabled={!split && !canSplit} onClick={() => split ? layoutStore.mergeGroups() : layoutStore.splitActiveTab()}><Columns2 size={15}/></button>
        <div className="add-panel-wrap">
          <button className="dock-action" type="button" aria-label="添加 Dock 面板" aria-expanded={addOpen} onClick={() => setAddOpen(value => !value)}><Plus size={16}/><ChevronDown size={11}/></button>
          {addOpen && <div className="add-panel-menu" role="menu">{panelOptions.map(option => {
            const Icon = option.icon;
            const alreadyOpen = layout.groups.some(group => group.tabs.some(tab => tab.id === `panel:${option.kind}`));
            return <button key={option.kind} role="menuitem" type="button" disabled={alreadyOpen} onClick={() => { layoutStore.openPanel(option.kind); setAddOpen(false); }}><Icon size={15}/>{option.title}<span>{alreadyOpen ? '已打开' : ''}</span></button>;
          })}</div>}
        </div>
        <button className="dock-action" type="button" aria-label="收起资料 Dock" title="收起 Dock" onClick={() => layoutStore.toggleDock()}><X size={16}/></button>
      </div>
    </header>
    <div className={`dock-groups ${split ? 'is-split' : ''}`}>
      {layout.groups.map(group => <DockGroupView group={group} key={group.id}/>) }
    </div>
  </aside>;
}

function DockGroupView({ group }: { group: DockGroup }) {
  const layout = useLayoutState();
  const activeTab = group.tabs.find(tab => tab.id === group.activeTabId) ?? null;
  const tabsRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [tabWidth, setTabWidth] = useState(160);
  const [overflow, setOverflow] = useState(false);
  const [scrollState, setScrollState] = useState({ left: false, right: false });
  const [menuOpen, setMenuOpen] = useState(false);
  const showOverflowMenu = overflow || group.tabs.length > 2;

  const updateMetrics = useCallback(() => {
    const scroller = tabsRef.current;
    if (!scroller) return;
    const { width, overflow: nextOverflow } = measureDockTabs(scroller.clientWidth, group.tabs.length);
    setTabWidth(width);
    setOverflow(nextOverflow);
    setScrollState({
      left: scroller.scrollLeft > 1,
      right: scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 1,
    });
  }, [group.tabs.length]);

  useLayoutEffect(() => {
    const scroller = tabsRef.current;
    if (!scroller) return;
    const observer = new ResizeObserver(() => requestAnimationFrame(updateMetrics));
    observer.observe(scroller);
    updateMetrics();
    return () => observer.disconnect();
  }, [updateMetrics]);

  useEffect(() => {
    const scroller = tabsRef.current;
    if (!scroller) return;
    const onWheel = (event: WheelEvent) => {
      if (!overflow || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      event.preventDefault();
      scroller.scrollLeft += event.deltaY;
    };
    scroller.addEventListener('wheel', onWheel, { passive: false });
    return () => scroller.removeEventListener('wheel', onWheel);
  }, [overflow]);

  const revealTab = useCallback((tabId: string | null) => {
    const scroller = tabsRef.current;
    if (!tabId || !scroller) return;
    const target = scroller.querySelector<HTMLElement>(`[data-dock-tab="${CSS.escape(tabId)}"]`);
    if (!target) return;
    const targetRect = target.getBoundingClientRect();
    const scrollerRect = scroller.getBoundingClientRect();
    if (targetRect.left < scrollerRect.left + 6) {
      scroller.scrollBy({ left: targetRect.left - scrollerRect.left - 6, behavior: 'smooth' });
    } else if (targetRect.right > scrollerRect.right - 6) {
      scroller.scrollBy({ left: targetRect.right - scrollerRect.right + 6, behavior: 'smooth' });
    }
  }, []);

  useEffect(() => {
    revealTab(group.activeTabId);
    updateMetrics();
  }, [group.activeTabId, tabWidth, updateMetrics, revealTab]);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [menuOpen]);

  const scrollTabs = (direction: 1 | -1) => {
    tabsRef.current?.scrollBy({ left: direction * Math.max(120, tabWidth), behavior: 'smooth' });
  };

  const dropTab = (event: DragEvent<HTMLElement>, index?: number) => {
    event.preventDefault();
    const tabId = event.dataTransfer.getData(TAB_MIME);
    if (!tabId || !group.tabs) return;
    if (layout.groups.length === 1 && index === undefined) {
      const bounds = event.currentTarget.getBoundingClientRect();
      const edge = Math.min(76, bounds.width * 0.14);
      const atEdge = event.clientX < bounds.left + edge || event.clientX > bounds.right - edge;
      if (atEdge) {
        layoutStore.splitTab(tabId);
        return;
      }
    }
    layoutStore.moveTab(tabId, group.id, index);
  };

  const keyDown = (event: KeyboardEvent<HTMLButtonElement>, tab: LayoutTab) => {
    const tabs = tabsRef.current ? [...tabsRef.current.querySelectorAll<HTMLButtonElement>('[role="tab"]')] : [];
    const currentIndex = tabs.indexOf(event.currentTarget);
    if (event.ctrlKey && event.shiftKey && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
      const destinationIndex = currentIndex + (event.key === 'ArrowLeft' ? -1 : 2);
      if (destinationIndex >= 0 && destinationIndex <= group.tabs.length) {
        event.preventDefault();
        layoutStore.moveTab(tab.id, group.id, destinationIndex);
        requestAnimationFrame(() => tabsRef.current?.querySelector<HTMLButtonElement>(`[data-dock-tab="${CSS.escape(tab.id)}"]`)?.focus());
      }
      return;
    }
    let nextIndex: number | undefined;
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
    if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = tabs.length - 1;
    if (nextIndex !== undefined && tabs.length) {
      event.preventDefault();
      tabs[nextIndex]?.focus();
      return;
    }
    if (event.altKey && event.key === 'ArrowDown' && layout.groups.length === 1) {
      event.preventDefault();
      layoutStore.splitTab(tab.id);
    } else if (event.altKey && event.key === 'ArrowUp' && layout.groups.length === 2) {
      event.preventDefault();
      const other = layout.groups.find(item => item.id !== group.id);
      if (other) layoutStore.moveTab(tab.id, other.id);
    }
  };

  return <section className={`dock-group ${layout.activeGroupId === group.id ? 'is-active-group' : ''}`} aria-label={`${group.id === 'main' ? '主' : '第二'}组资料`} onDragOver={event => { if (event.dataTransfer.types.includes(TAB_MIME)) event.preventDefault(); }} onDrop={event => dropTab(event)} onPointerDown={() => layoutStore.activateGroup(group.id)}>
    <div className="dock-tab-strip">
      {overflow && <button className="dock-scroll-button" type="button" aria-label="向左滚动标签" title="向左滚动标签" disabled={!scrollState.left} onClick={() => scrollTabs(-1)}><ChevronLeft size={14}/></button>}
      <div className="dock-tabs-scroll" ref={tabsRef} role="tablist" aria-label="资料标签" aria-orientation="horizontal" onScroll={updateMetrics} style={{ '--dock-tab-w': `${tabWidth}px` } as CSSProperties}>
        {group.tabs.map(tab => <div className="dock-tab-slot" key={tab.id}>
          <button className={`dock-tab ${activeTab?.id === tab.id ? 'is-active' : ''}`} type="button" role="tab" aria-selected={activeTab?.id === tab.id} aria-controls={`dock-panel-${group.id}`} aria-label={`${tab.title}。方向键浏览；Ctrl+Shift+方向键重排；Alt+向下分栏，Alt+向上移动到另一组。`} data-dock-tab={tab.id} tabIndex={activeTab?.id === tab.id ? 0 : -1} draggable onClick={() => layoutStore.selectTab(group.id, tab.id)} onKeyDown={event => keyDown(event, tab)} onFocus={() => revealTab(tab.id)} onDragStart={event => { event.dataTransfer.setData(TAB_MIME, tab.id); event.dataTransfer.effectAllowed = 'move'; }} onDragOver={event => { if (event.dataTransfer.types.includes(TAB_MIME)) event.preventDefault(); }} onDrop={event => { event.stopPropagation(); const bounds = event.currentTarget.getBoundingClientRect(); dropTab(event, event.clientX > bounds.left + bounds.width / 2 ? group.tabs.findIndex(item => item.id === tab.id) + 1 : group.tabs.findIndex(item => item.id === tab.id)); }} title={tab.title}>
            <TabIcon kind={tab.kind}/><span>{tab.title}</span>
          </button>
          <button className="close-dock-tab" type="button" aria-label={`关闭 ${tab.title}`} title={`关闭 ${tab.title}`} onClick={() => layoutStore.closeTab(group.id, tab.id)}><X size={12}/></button>
        </div>)}
      </div>
      {overflow && <button className="dock-scroll-button" type="button" aria-label="向右滚动标签" title="向右滚动标签" disabled={!scrollState.right} onClick={() => scrollTabs(1)}><ChevronRight size={14}/></button>}
      {showOverflowMenu && <div className="dock-overflow" ref={menuRef}>
        <button className="dock-overflow-trigger" type="button" aria-label="更多资料标签" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen(value => !value)}><MoreHorizontal size={15}/></button>
        {menuOpen && <div className="dock-overflow-menu" role="menu">
          {group.tabs.map(tab => <button key={tab.id} type="button" role="menuitemradio" aria-checked={activeTab?.id === tab.id} className={activeTab?.id === tab.id ? 'is-active' : ''} onClick={() => { layoutStore.selectTab(group.id, tab.id); setMenuOpen(false); revealTab(tab.id); }}><TabIcon kind={tab.kind}/><span>{tab.title}</span></button>)}
        </div>}
      </div>}
      {group.id === 'secondary' && <span className="group-label">第二组</span>}
    </div>
    <div className="dock-panel-content" id={`dock-panel-${group.id}`} role="tabpanel" aria-label={activeTab?.title ?? '空分组'} onDragOver={event => { if (event.dataTransfer.types.includes(TAB_MIME)) event.preventDefault(); }} onDrop={event => { event.stopPropagation(); dropTab(event); }}>
      {activeTab ? <PanelContent tab={activeTab}/> : <div className="dock-drop-empty"><Columns2 size={19}/><strong>将标签拖到此组</strong><span>也可以从上方添加面板</span></div>}
    </div>
  </section>;
}

function TabIcon({ kind }: { kind: PanelKind }) {
  if (kind === 'papers' || kind === 'paper') return <BookOpen size={14}/>;
  if (kind === 'report') return <FileText size={14}/>;
  if (kind === 'activity') return <Activity size={14}/>;
  if (kind === 'metrics') return <Gauge size={14}/>;
  return <Settings2 size={14}/>;
}

function PanelContent({ tab }: { tab: LayoutTab }) {
  switch (tab.kind) {
    case 'papers': return <PaperList/>;
    case 'paper': return <PaperReader tab={tab}/>;
    case 'report': return <ResearchReport/>;
    case 'activity': return <ActivityPanel/>;
    case 'metrics': return <MetricsPanel/>;
    case 'settings': return <SettingsPanel/>;
  }
}
