import type { Status } from '../types/contracts';

export { statusLabel } from './labels';

export function isJobActive(status: Status | undefined): boolean {
  return status === 'queued' || status === 'running';
}

export function isJobFinished(status: Status | undefined): boolean {
  return status === 'completed' || status === 'partial' || status === 'failed' || status === 'cancelled';
}
