import type { ContractError, Job, Paper, PipelineResult, Summary } from '../types/contracts';
import {
  errorHint,
  errorLabel,
  paperStatusLabel,
  rankingFallbackLabel,
  summaryScopeLabel,
} from './labels';
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

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export interface RunRanking {
  requested: string;
  actual: string;
  modelName?: string;
  fallbackReason?: string;
  fallbackMessage?: string;
  candidateCount?: number;
  beforeTruncation?: number;
  afterTruncation?: number;
  durationSeconds?: number;
}

export function rankingForResult(result: PipelineResult | null): RunRanking | null {
  if (!result) return null;
  const value = asRecord(result.metrics?.ranking);
  if (value) {
    return {
      requested: asString(value.requested_ranker) ?? 'keyword',
      actual: asString(value.actual_ranker) ?? 'keyword',
      modelName: asString(value.model_name),
      fallbackReason: asString(value.fallback_reason),
      fallbackMessage: asString(value.fallback_message),
      candidateCount: asNumber(value.candidate_count),
      beforeTruncation: asNumber(value.candidate_count_before_truncation),
      afterTruncation: asNumber(value.candidate_count_after_truncation),
      durationSeconds: asNumber(value.duration_seconds),
    };
  }
  const semantic = (result.papers ?? []).some(paper => paper.ranking_details?.ranker === 'semantic_rrf');
  if (!result.papers?.length) return null;
  const semanticPaper = (result.papers ?? []).find(paper => paper.ranking_details?.ranker === 'semantic_rrf');
  return {
    requested: semantic ? 'semantic' : 'keyword',
    actual: semantic ? 'semantic' : 'keyword',
    modelName: semanticPaper?.ranking_details?.model_name
      ? String(semanticPaper.ranking_details.model_name)
      : undefined,
  };
}

export function shortModelName(name?: string): string {
  if (!name) return '';
  const parts = name.split('/');
  return parts[parts.length - 1] || name;
}

export function rankingModeLabel(ranking: RunRanking | null): string {
  if (!ranking) return '排序方式未记录';
  const model = shortModelName(ranking.modelName);
  if (ranking.actual === 'semantic') {
    return `语义 + RRF 混合排序${model ? ` · ${model}` : ''}`;
  }
  if (ranking.requested === 'semantic') return '关键词排序（语义已降级）';
  return '关键词排序';
}

export interface PaperRankingView {
  keywordRank?: number;
  semanticRank?: number;
  rrfRank?: number;
  cosineSimilarity?: number;
  rrfScore?: number;
  modelName?: string;
}

export function paperRanking(paper: Paper): PaperRankingView {
  const details = paper.ranking_details ?? {};
  return {
    keywordRank: asNumber(details.keyword_rank),
    semanticRank: asNumber(details.semantic_rank),
    rrfRank: asNumber(details.rrf_rank),
    cosineSimilarity: asNumber(details.cosine_similarity),
    rrfScore: asNumber(details.rrf_score),
    modelName: asString(details.model_name),
  };
}

export function scoreShort(paper: Paper): string {
  if (typeof paper.final_score === 'number' && Number.isFinite(paper.final_score)) {
    return `LLM 评分 ${paper.final_score.toFixed(3)}`;
  }
  if (typeof paper.initial_score === 'number' && Number.isFinite(paper.initial_score)) {
    return `关键词分 ${paper.initial_score.toFixed(3)}`;
  }
  return '未评分';
}

export function scoreDescription(paper: Paper): string {
  if (typeof paper.final_score === 'number' && Number.isFinite(paper.final_score)) {
    return `LLM 终评分 ${paper.final_score.toFixed(3)}（0–1 分，不是概率）`;
  }
  if (typeof paper.initial_score === 'number' && Number.isFinite(paper.initial_score)) {
    return `关键词初排分 ${paper.initial_score.toFixed(3)}（0–1 分，不是概率）`;
  }
  return '模型未给出评分。';
}

export type StageState = 'done' | 'partial' | 'failed' | 'pending' | 'skipped';

export interface PaperStageView {
  key: string;
  label: string;
  state: StageState;
  detail?: string;
}

const TERMINAL_RESULT_STATUSES = new Set(['completed', 'demo_completed', 'partial', 'failed', 'cancelled']);

export function isTerminalStatus(status?: string): boolean {
  return Boolean(status && TERMINAL_RESULT_STATUSES.has(status));
}

export function isTerminalResult(result: PipelineResult | null): boolean {
  return Boolean(result && isTerminalStatus(result.status));
}

export function paperStages(
  paper: Paper, summary: Summary | undefined, result: PipelineResult | null,
  terminalOverride?: boolean,
): PaperStageView[] {
  const terminal = terminalOverride ?? isTerminalResult(result);
  const selected = Boolean(result?.selected_papers.some(item => item.paper_id === paper.paper_id));
  const replacementItems = Array.isArray(result?.metrics?.replacements)
    ? result!.metrics.replacements as unknown[]
    : [];
  const replacement = replacementItems.some(item => asRecord(item)?.replacement_paper_id === paper.paper_id);
  const analysisIds = new Set(stringList(result?.metrics?.analysis_paper_ids));
  const attemptedIds = new Set(stringList(result?.metrics?.attempted_paper_ids));
  const analyzed = analysisIds.has(paper.paper_id);
  const attempted = attemptedIds.has(paper.paper_id) || analyzed || selected || replacement;
  const complete = paper.fulltext_quality?.complete === true;
  const reportIds = new Set((result?.report?.references ?? []).map(reference => reference.paper_id));
  const paperReason = paper.errors?.length ? errorLabel(paper.errors[0]) : paper.error ?? '';
  const summaryErrors = summary?.errors ?? [];
  const summaryReason = summaryErrors.length ? errorLabel(summaryErrors[0]) : '';
  const selectedDetail = selected
    ? `LLM 入选名次 #${paper.rank ?? '—'}`
    : replacement ? '原始 Top-k 之外的全替补' : analyzed ? '候补进入实际分析' : undefined;
  const downloadState: StageState = paper.pdf_path ? 'done'
    : attempted ? (terminal ? 'failed' : 'pending') : 'skipped';
  const downloadDetail = paper.pdf_path ? 'PDF 已保存'
    : attempted ? (paperReason || (paper.status === 'metadata_only' ? '未取得 PDF' : paperStatusLabel(paper.status)))
      : '未进入全文处理';
  const extractionState: StageState = paper.markdown_path
    ? complete && paper.status === 'converted' ? 'done' : 'partial'
    : paper.pdf_path ? (terminal ? 'failed' : 'pending') : 'skipped';
  const extractionDetail = paper.markdown_path
    ? complete && paper.status === 'converted' ? '完整提取' : (paperReason || '部分提取')
    : paper.pdf_path ? (terminal ? (paperReason || '未生成 Markdown') : '等待转换') : '没有可转换的 PDF';
  // Reading scope and validation are separate: a full-text scope with failed
  // chunk/merge checks must not look like a successful full-text summary.
  const summaryState: StageState = !summary
    ? analyzed ? (terminal ? 'failed' : 'pending') : 'skipped'
    : summary.summary_scope === 'failed' ? 'failed'
      : summaryErrors.length ? 'partial'
        : summary.summary_scope === 'fulltext' ? 'done' : 'partial';
  const summaryDetail = summary
    ? [summaryScopeLabel(summary.summary_scope), summaryReason].filter(Boolean).join(' · ')
    : analyzed ? (terminal ? (paperReason || '总结未返回') : '等待结果') : '未进入分析';
  const reportState: StageState = reportIds.has(paper.paper_id) ? 'done'
    : terminal ? (analyzed ? 'failed' : 'skipped') : 'pending';
  const reportDetail = reportIds.has(paper.paper_id) ? '已被报告引用'
    : terminal ? (analyzed ? (result?.report ? '未被当前报告引用' : '报告未生成') : '未进入报告')
      : '等待报告生成';
  return [
    { key: 'metadata', label: '论文元数据', state: 'done', detail: paper.year ? `${paper.year} · ${paper.sources.join(' / ')}` : paper.sources.join(' / ') },
    { key: 'selection', label: '原始选择', state: selected ? 'done' : replacement || analyzed ? 'partial' : 'skipped', detail: selectedDetail },
    { key: 'download', label: '全文下载', state: downloadState, detail: downloadDetail },
    { key: 'extraction', label: 'Markdown 提取', state: extractionState, detail: extractionDetail },
    { key: 'summary', label: '单篇总结', state: summaryState, detail: summaryDetail },
    { key: 'report', label: '报告纳入', state: reportState, detail: reportDetail },
  ];
}

export interface PartialReason {
  key: string;
  title: string;
  hint: string;
  count?: number;
  kind: 'limitation' | 'partial';
}

export function partialReasons(result: PipelineResult | null): PartialReason[] {
  if (!result) return [];
  const reasons: PartialReason[] = [];
  const seen = new Set<string>();
  const terminal = isTerminalResult(result);
  const completed = result.status === 'completed' || result.status === 'demo_completed';
  const add = (kind: PartialReason['kind'], key: string, title: string, hint: string, count?: number) => {
    if (seen.has(key)) return;
    seen.add(key);
    reasons.push({ kind, key, title, hint, count });
  };
  const ranking = rankingForResult(result);
  if (ranking?.requested === 'semantic' && ranking.actual !== 'semantic') {
    add('limitation', 'ranking_fallback', '语义排序已回退', ranking.fallbackMessage || rankingFallbackLabel(ranking.fallbackReason) || '实际使用关键词排序。');
  }
  if (result.metrics?.selector_degraded) {
    add('limitation', 'selector_degraded', 'LLM 筛选已降级', 'Top-k 来自配置允许的初始排序；请查看论文选择记录。');
  }
  if (result.metrics?.query_degraded) {
    add('limitation', 'query_degraded', '检索词规划降级', '检索使用了降级词或显式词；请查看活动记录中的 query_plan。');
  }
  const replacements = Array.isArray(result.metrics?.replacements)
    ? result.metrics.replacements.filter(item => typeof item === 'object' && item !== null)
    : [];
  if (replacements.length) {
    add('limitation', 'replacements', `${replacements.length} 篇全文替补`, '原始 Top-k 选择未改写；替补对应关系保留在论文列表和 metrics 中。', replacements.length);
  }
  const summaryCounts = new Map<string, number>();
  for (const summary of result.summaries ?? []) {
    if (result.status === 'demo_completed' && summary.summary_scope === 'demo_fixture') continue;
    if (summary.summary_scope !== 'fulltext') {
      summaryCounts.set(summary.summary_scope, (summaryCounts.get(summary.summary_scope) ?? 0) + 1);
    }
  }
  if (summaryCounts.size) {
    const detail = [...summaryCounts.entries()].map(([scope, count]) => `${summaryScopeLabel(scope)} ${count} 篇`).join('；');
    add(
      completed ? 'limitation' : 'partial',
      'summary_scope',
      completed ? '部分论文的非全文总结范围' : '总结范围不完整',
      `${detail}。未读内容没有被标记为全文。`,
    );
  }
  const fulltextSummaries = asNumber(result.metrics?.fulltext_summary_count);
  const topK = result.top_k;
  if (!completed && terminal && typeof fulltextSummaries === 'number' && fulltextSummaries < topK) {
    add('partial', 'fulltext_summary_count', `全文总结 ${fulltextSummaries}/${topK}`, '只有通过完整全文校验的总结才会计入该数量。');
  }
  const errorCodes = new Map<string, { error: ContractError; count: number }>();
  for (const error of result.errors ?? []) {
    const code = error.code ?? 'unknown';
    if (code === 'partial_report') continue;
    if (result.status === 'demo_completed' && code === 'partial_report') continue;
    const existing = errorCodes.get(code);
    errorCodes.set(code, { error, count: (existing?.count ?? 0) + 1 });
  }
  for (const [code, item] of errorCodes) {
    add(completed ? 'limitation' : 'partial', `error:${code}`, errorLabel(item.error), errorHint(code), item.count);
  }
  const reportInputCompacted = (result.errors ?? []).some(error =>
    error.code === 'partial_report' && error.details?.reason === 'report_input_compacted');
  const reportPartial = (result.errors ?? []).some(error => error.code === 'partial_report');
  if (reportInputCompacted) {
    add(completed ? 'limitation' : 'partial', 'report_compacted', '报告输入使用了有界摘录', '报告按部分输入生成；单篇完整内容仍保留，未按完整报告处理。');
  } else if (!completed && result.metrics?.report_valid === false) {
    add(
      'partial',
      'report_invalid',
      reportPartial ? '报告覆盖不完整' : '报告校验未通过',
      reportPartial ? '报告只覆盖实际可用的单篇总结。' : '报告按部分结果保留；未通过校验的引用不会显示为完整报告。',
    );
  }
  if (result.status === 'failed') {
    add('partial', 'status_failed', '任务失败', '已有产物和错误记录会保留；请查看活动记录中的具体阶段。');
  }
  return reasons;
}

