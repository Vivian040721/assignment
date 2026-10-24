import type { Job, Paper, PipelineResult, Summary } from '../types/contracts';
export { paperStatusLabel, summaryScopeLabel } from './labels';

export interface PaperCollection {
  result: PipelineResult | null;
  topPapers: Paper[];
  analysisPapers: Paper[];
  replacementPairs: Array<{ original: Paper | undefined; replacement: Paper | undefined; raw: Record<string, unknown> }>;
  paperById: Map<string, Paper>;
  summaries: Summary[];
}

export function resultForJob(job: Job | null | undefined, fallback: PipelineResult | null | undefined): PipelineResult | null {
  return job?.result ?? fallback ?? null;
}

export function collectPapers(result: PipelineResult | null): PaperCollection {
  if (!result) return { result: null, topPapers: [], analysisPapers: [], replacementPairs: [], paperById: new Map(), summaries: [] };
  const candidates = new Map<string, Paper>();
  for (const paper of result.papers ?? []) candidates.set(paper.paper_id, paper);
  const topPapers = (result.selected_papers ?? []).map(original => {
    const latest = candidates.get(original.paper_id);
    if (!latest) return original;
    candidates.set(original.paper_id, { ...original, ...latest, rank: original.rank, initial_score: original.initial_score, final_score: original.final_score, selection_reason: original.selection_reason });
    return candidates.get(original.paper_id)!;
  });
  const topIds = new Set(topPapers.map(paper => paper.paper_id));
  const analysisIds = new Set(
    Array.isArray(result.metrics?.analysis_paper_ids)
      ? result.metrics.analysis_paper_ids.filter((value): value is string => typeof value === 'string')
      : [],
  );
  const replacementValues = Array.isArray(result.metrics?.replacements)
    ? result.metrics.replacements.filter((value): value is Record<string, unknown> => typeof value === 'object' && value !== null)
    : [];
  const replacementIds = new Set<string>();
  for (const item of replacementValues) {
    if (typeof item.replacement_paper_id === 'string') replacementIds.add(item.replacement_paper_id);
  }
  const analysisPapers = [...candidates.values()].filter(paper =>
    !topIds.has(paper.paper_id) && !replacementIds.has(paper.paper_id) && analysisIds.has(paper.paper_id));
  const replacementPairs = replacementValues.map(raw => ({
    original: typeof raw.original_paper_id === 'string' ? topPapers.find(paper => paper.paper_id === raw.original_paper_id) : undefined,
    replacement: typeof raw.replacement_paper_id === 'string' ? candidates.get(raw.replacement_paper_id) : undefined,
    raw,
  }));
  const paperById = new Map<string, Paper>();
  for (const paper of [...topPapers, ...analysisPapers, ...replacementPairs.flatMap(pair => [pair.original, pair.replacement]), ...candidates.values()]) {
    if (paper) paperById.set(paper.paper_id, paper);
  }
  return { result, topPapers, analysisPapers, replacementPairs, paperById, summaries: result.summaries ?? [] };
}

