import type { Status } from '../types/contracts';

export { statusLabel } from './labels';

export function isJobActive(status: Status | undefined): boolean {
  return status === 'pending' || status === 'queued' || status === 'running';
}

export function isJobFinished(status: Status | undefined): boolean {
  return status === 'completed' || status === 'partial_success' || status === 'partial'
    || status === 'demo_completed' || status === 'failed' || status === 'cancelled';
}
