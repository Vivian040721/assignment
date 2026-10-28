import type { ContractError, PipelineResult } from '../types/contracts';

export interface ExecutionErrorGroup {
  key: string;
  error: ContractError;
  items: ContractError[];
  count: number;
}

export function groupExecutionErrors(errors: ContractError[]): ExecutionErrorGroup[] {
  const groups = new Map<string, ContractError[]>();
  for (const error of errors) {
    const key = JSON.stringify([
      error.code ?? 'unknown', error.stage ?? '', error.paper_id ?? '', error.message,
    ]);
    const items = groups.get(key) ?? [];
    items.push(error);
    groups.set(key, items);
  }
  return [...groups.entries()].map(([key, items]) => ({
    key,
    error: items[0],
    items,
    count: items.length,
  }));
}

export function collectExecutionErrors(result: PipelineResult | null | undefined): ContractError[] {
  if (!result) return [];
  const errors = [...result.errors];
  for (const paper of [...result.selected_papers, ...result.papers]) errors.push(...(paper.errors ?? []));
  for (const summary of result.summaries) errors.push(...(summary.errors ?? []));
  errors.push(...(result.report?.errors ?? []));
  return errors;
}
