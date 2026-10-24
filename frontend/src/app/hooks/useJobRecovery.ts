import { useEffect } from 'react';
import { desktopApi } from '../services/desktopApi';
import type { Job } from '../types/contracts';
import { workspaceStore } from '../stores/workspaceStore';
import { isJobActive } from '../utils/status';

const RECOVERY_POLL_MS = 3200;
const activeCleanups = new Set<() => void>();

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    for (const cleanup of [...activeCleanups]) cleanup();
  });
}

export function useJobRecovery(job: Job | null): void {
  useEffect(() => {
    if (!job || !isJobActive(job.status)) return;
    const jobId = job.job_id;
    let disposed = false;
    let polling = false;
    let terminalPending = false;
    let timer: number | undefined;
    const after = job.events.at(-1)?.seq ?? 0;

    const poll = async (terminalCheck = false) => {
      if (disposed) return;
      if (polling) {
        terminalPending ||= terminalCheck;
        return;
      }
      polling = true;
      try {
        if (!disposed) await workspaceStore.refreshJob(jobId);
      } catch (error) {
        if (!disposed) workspaceStore.setError(error instanceof Error ? error.message : String(error));
      } finally {
        polling = false;
        if (!disposed && terminalPending) {
          terminalPending = false;
          void poll(true);
        }
      }
    };

    let unsubscribe: (() => void) | undefined;
    try {
      unsubscribe = desktopApi().subscribeJob(jobId, after, event => {
        if (disposed) return;
        if (event.event) workspaceStore.onJobEvent(jobId, event.event);
        if (event.error) {
          workspaceStore.setError(event.error);
          void poll();
        } else if (event.done) {
          void poll(true);
        }
      });
    } catch (error) {
      workspaceStore.setError(error instanceof Error ? error.message : String(error));
    }

    timer = window.setInterval(() => void poll(), RECOVERY_POLL_MS);
    void poll();
    const cleanup = () => {
      if (disposed) return;
      disposed = true;
      activeCleanups.delete(cleanup);
      try {
        unsubscribe?.();
      } catch {
        // A stale preload subscription is already detached.
      }
      if (timer !== undefined) window.clearInterval(timer);
      // Detaching the renderer observer never cancels the backend job.
    };
    activeCleanups.add(cleanup);
    return cleanup;
  }, [job?.job_id, job?.status]);
}
