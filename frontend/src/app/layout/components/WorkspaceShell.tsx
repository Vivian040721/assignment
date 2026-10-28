import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { X } from 'lucide-react';
import { useJobRecovery } from '../../hooks/useJobRecovery';
import { useWorkspaceState, workspaceStore } from '../../stores/workspaceStore';
import { layoutStore, useLayoutState } from '../stores/layoutStore';
import { Conversation } from './conversation/Conversation';
import { RightDock } from './docking/RightDock';
import { Sidebar } from './sidebar/Sidebar';

function useViewport(): { width: number; height: number } {
  const [size, setSize] = useState(() => ({ width: window.innerWidth, height: window.innerHeight }));
  useEffect(() => {
    const resize = () => setSize({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', resize, { passive: true });
    return () => window.removeEventListener('resize', resize);
  }, []);
  return size;
}

export function WorkspaceShell() {
  const state = useWorkspaceState();
  const layout = useLayoutState();
  const viewport = useViewport();
  const compact = viewport.width < 900;
  const stacked = viewport.width < 1180;
  useJobRecovery(state.activeJob);

  useEffect(() => { void workspaceStore.initialize(); }, []);

  const rail = compact || layout.sidebarCollapsed;
  const sidebarWidth = rail ? 58 : layout.sidebarWidth;
  const focusReading = layout.focusReading;
  useEffect(() => {
    if (!focusReading) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      const target = event.target;
      if (target instanceof HTMLElement && target.closest('input, textarea, select, [contenteditable="true"]')) return;
      event.preventDefault();
      layoutStore.setFocusReading(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [focusReading]);
  useEffect(() => {
    if (!layout.dockOpen && layout.focusReading) layoutStore.setFocusReading(false);
  }, [layout.dockOpen, layout.focusReading]);
  const dockWidthLimit = Math.max(310, Math.min(520, viewport.width - sidebarWidth - 470));
  const effectiveDockWidth = stacked || !layout.dockOpen
    ? layout.dockWidth
    : Math.min(layout.dockWidth, dockWidthLimit);
  const dockHeightLimit = Math.max(200, Math.min(560, viewport.height - 280));
  const style = {
    '--sidebar-width': `${sidebarWidth}px`,
    '--dock-width': `${effectiveDockWidth}px`,
    '--dock-height': `${Math.min(layout.dockHeight, dockHeightLimit)}px`,
  } as CSSProperties;

  const startResize = (
    event: React.PointerEvent<HTMLDivElement>,
    move: (pointer: PointerEvent) => void,
  ) => {
    event.preventDefault();
    if (event.button !== 0) return;
    const controller = new AbortController();
    const finish = () => {
      controller.abort();
      document.body.classList.remove('is-resizing');
    };
    document.body.classList.add('is-resizing');
    window.addEventListener('pointermove', move, { signal: controller.signal });
    window.addEventListener('pointerup', finish, { signal: controller.signal });
    window.addEventListener('pointercancel', finish, { signal: controller.signal });
    window.addEventListener('blur', finish, { signal: controller.signal });
    window.addEventListener('keydown', pointer => {
      if (pointer.key === 'Escape') finish();
    }, { signal: controller.signal });
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* Synthetic pointers have no capture. */ }
  };

  const resizeSidebar = (event: React.PointerEvent<HTMLDivElement>) => {
    if (compact || rail) return;
    startResize(event, pointer => {
      const max = Math.min(380, viewport.width - (layout.dockOpen ? effectiveDockWidth : 0) - 470);
      layoutStore.setSidebarWidth(Math.max(220, Math.min(Math.max(220, max), pointer.clientX)));
    });
  };

  const resizeDock = (event: React.PointerEvent<HTMLDivElement>) => {
    startResize(event, pointer => {
      if (stacked) {
        layoutStore.setDockHeight(Math.min(dockHeightLimit, Math.max(200, viewport.height - pointer.clientY)));
      } else {
        layoutStore.setDockWidth(Math.max(310, Math.min(dockWidthLimit, viewport.width - pointer.clientX)));
      }
    });
  };

  return <div className={`workspace ${stacked ? 'workspace-stacked' : ''} ${compact ? 'workspace-compact' : ''} ${rail ? 'workspace-sidebar-rail' : ''} ${layout.dockOpen ? '' : 'workspace-no-dock'} ${focusReading ? 'workspace-focus-reading' : ''}`} style={style}>
    <Sidebar compact={compact}/>
    <ResizeHandle className={`sidebar-resize ${rail ? 'is-disabled' : ''}`} disabled={rail} orientation="vertical" value={sidebarWidth} label="调整会话侧栏宽度" onPointerDown={resizeSidebar} onStep={step => layoutStore.setSidebarWidth(layout.sidebarWidth + step)}/>
    <Conversation/>
    <ResizeHandle className={`dock-resize ${layout.dockOpen && !focusReading ? '' : 'is-disabled'}`} disabled={!layout.dockOpen || focusReading} orientation={stacked ? 'horizontal' : 'vertical'} value={stacked ? layout.dockHeight : layout.dockWidth} label={stacked ? '调整资料 Dock 高度' : '调整资料 Dock 宽度'} onPointerDown={resizeDock} onStep={step => stacked ? layoutStore.setDockHeight(layout.dockHeight + step) : layoutStore.setDockWidth(layout.dockWidth - step)}/>
    <RightDock hidden={!layout.dockOpen}/>
    {state.error && <div className="global-feedback feedback-error" role="alert" aria-live="assertive">
      <span>{state.error}</span><button type="button" aria-label="关闭错误提示" onClick={() => workspaceStore.clearError()}><X size={15}/></button>
    </div>}
  </div>;
}

function ResizeHandle({ className, orientation, value, label, onPointerDown, onStep, disabled = false }: {
  className: string;
  orientation: 'vertical' | 'horizontal';
  value: number;
  label: string;
  onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
  onStep: (step: number) => void;
  disabled?: boolean;
}) {
  const sidebarHandle = className.includes('sidebar-resize');
  const minimum = sidebarHandle ? 220 : orientation === 'vertical' ? 310 : 200;
  const maximum = sidebarHandle ? 380 : orientation === 'vertical' ? 520 : 560;
  return <div className={`resize-handle ${className}`} role="separator" aria-label={label} aria-orientation={orientation} aria-valuenow={Math.round(value)} aria-valuemin={minimum} aria-valuemax={maximum} aria-disabled={disabled || undefined} tabIndex={disabled ? -1 : 0} onPointerDown={onPointerDown} onKeyDown={event => {
    if (disabled) return;
    const direction = orientation === 'vertical'
      ? event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0
      : event.key === 'ArrowUp' ? 1 : event.key === 'ArrowDown' ? -1 : 0;
    if (direction) {
      event.preventDefault();
      onStep(direction * 12);
    }
  }}/>;
}
