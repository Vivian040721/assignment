import { AlertTriangle, GitMerge, Info } from 'lucide-react';
import type { Paper, PipelineResult, Summary } from '../../../types/contracts';
import { errorHint, errorLabel, rankingFallbackLabel } from '../../../utils/labels';
import {
  isTerminalResult,
  partialReasons,
  paperRanking,
  paperStages,
  rankingForResult,
  rankingModeLabel,
  scoreDescription,
  shortModelName,
} from '../../../utils/papers';

export function RankingSummary({ result }: { result: PipelineResult }) {
  const ranking = rankingForResult(result);
  if (!ranking) return null;
  const fellBack = ranking.requested === 'semantic' && ranking.actual !== 'semantic';
  const semantic = ranking.actual === 'semantic';
  return <section className={`ranking-summary ${fellBack ? 'is-fallback' : ''}`} aria-label="本次排序方式">
    <div className="ranking-summary-head">
      <span className="ranking-summary-icon"><GitMerge size={14}/></span>
      <div><span className="eyebrow">本次排序</span><strong>{rankingModeLabel(ranking)}</strong></div>
      {ranking.durationSeconds !== undefined && <small>{ranking.durationSeconds.toFixed(2)}s</small>}
    </div>
    {fellBack && <p className="ranking-fallback">{ranking.fallbackMessage || rankingFallbackLabel(ranking.fallbackReason)}</p>}
    <div className="ranking-counts">
      {ranking.beforeTruncation !== undefined && <span>去重候选 <strong>{ranking.beforeTruncation}</strong></span>}
      {ranking.afterTruncation !== undefined && <span>进入筛选 <strong>{ranking.afterTruncation}</strong></span>}
      {ranking.modelName && <span>模型 <strong>{shortModelName(ranking.modelName)}</strong></span>}
    </div>
    {semantic && <p className="ranking-note"><Info size={12}/>关键词名次与 Sentence-BERT 语义名次按 0.5/(60+rank) 融合；RRF 分数不是概率。展开论文可查看各自名次。</p>}
  </section>;
}

export function PartialResultSummary({ result, compact = false }: { result: PipelineResult; compact?: boolean }) {
  const reasons = partialReasons(result);
  if (!reasons.length) return null;
  const terminal = isTerminalResult(result);
  const completed = result.status === 'completed' || result.status === 'demo_completed';
  const limitations = reasons.filter(reason => reason.kind === 'limitation');
  const partial = reasons.filter(reason => reason.kind === 'partial');
  let title = '本次研究的限制与替补';
  let note = '这些记录会影响结果范围，但不会改变当前完成状态。';
  let primary = limitations;
  let secondary = partial;
  let secondaryLabel = '当前记录的问题';
  if (!terminal) {
    title = '当前处理情况';
    note = '任务仍在运行；以下为已记录的限制或问题，不代表最终结论。';
    primary = limitations.length ? limitations : partial;
    secondary = limitations.length ? partial : [];
  } else if (completed) {
    primary = limitations;
    secondary = [];
  } else if (partial.length) {
    title = '为什么不是完整结果';
    note = '以下原因来自实际结果、单篇总结范围与结构化错误。';
    primary = partial;
    secondary = limitations;
    secondaryLabel = '本次研究的限制与替补';
  } else {
    title = '本次研究的限制与替补';
    note = '任务未完整完成；以下为已记录的限制。';
    primary = limitations;
    secondary = [];
  }
  if (!primary.length && !secondary.length) return null;
  const visible = compact ? primary.slice(0, 2) : primary.slice(0, 3);
  const remaining = primary.slice(visible.length);
  return <section className="partial-summary" aria-label={title}>
    <div className="partial-summary-head"><AlertTriangle size={14}/><strong>{title}</strong><span>{reasons.length} 项</span></div>
    <p className="partial-summary-note">{note}</p>
    {visible.length > 0 && <ReasonList items={visible}/>}
    {remaining.length > 0 && <details className="partial-summary-more">
      <summary>还有 {remaining.length} 项</summary>
      <ReasonList items={remaining}/>
    </details>}
    {secondary.length > 0 && <details className="partial-summary-more">
      <summary>{secondaryLabel}（{secondary.length}）</summary>
      <ReasonList items={secondary}/>
    </details>}
  </section>;
}

function ReasonList({ items }: { items: ReturnType<typeof partialReasons> }) {
  return <ul>{items.map(reason => <li key={reason.key}>
    <div><strong>{reason.title}</strong>{reason.count !== undefined && <span> × {reason.count}</span>}</div>
    <p>{reason.hint}</p>
  </li>)}</ul>;
}

export function PaperProgress({ paper, summary, result, terminal }: {
  paper: Paper;
  summary?: Summary;
  result: PipelineResult | null;
  terminal?: boolean;
}) {
  const stages = paperStages(paper, summary, result, terminal);
  return <ol className="paper-stage-list" aria-label="论文实际处理阶段">
    {stages.map(stage => <li key={stage.key} className={`paper-stage stage-${stage.state}`}>
      <span className="paper-stage-dot"/>
      <div><strong>{stage.label}</strong>{stage.detail && <small>{stage.detail}</small>}</div>
    </li>)}
  </ol>;
}

export function PaperRankingDetails({ paper, selected, compact = false }: { paper: Paper; selected: boolean; compact?: boolean }) {
  const ranking = paperRanking(paper);
  const rows: Array<[string, string]> = [];
  if (ranking.keywordRank !== undefined) rows.push(['关键词名次', `#${ranking.keywordRank}`]);
  if (ranking.semanticRank !== undefined) rows.push(['语义名次', `#${ranking.semanticRank}`]);
  if (ranking.cosineSimilarity !== undefined) rows.push(['余弦相似度', ranking.cosineSimilarity.toFixed(4)]);
  if (ranking.rrfRank !== undefined) rows.push(['RRF 融合名次', `#${ranking.rrfRank}`]);
  if (ranking.rrfScore !== undefined) rows.push(['RRF 融合分', `${ranking.rrfScore.toFixed(5)}（名次融合值，不是概率）`]);
  if (typeof paper.initial_score === 'number') rows.push(['关键词初排分', paper.initial_score.toFixed(3)]);
  if (typeof paper.final_score === 'number') rows.push(['LLM 终评分', paper.final_score.toFixed(3)]);
  if (selected && paper.rank !== null && paper.rank !== undefined) rows.push(['LLM 入选名次', `#${paper.rank}`]);
  if (ranking.modelName) rows.push(['语义模型', shortModelName(ranking.modelName)]);
  const errors = paper.errors ?? [];
  const hasReason = Boolean(paper.ranking_reason || paper.selection_reason);
  if (!rows.length && !hasReason && !errors.length) {
    return <p className="ranking-empty">当前结果没有返回这篇论文的排序依据。</p>;
  }
  return <details className={`paper-ranking-details ${compact ? 'is-compact' : ''}`}>
    <summary>排序与入选依据</summary>
    <div className="ranking-detail-body">
      {rows.length > 0 && <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>}
      <p className="ranking-caveat">{scoreDescription(paper)}{paper.final_score !== null && paper.final_score !== undefined ? '；LLM 评分也不是校准概率。' : ''}</p>
      {paper.ranking_reason && <div className="ranking-reason"><strong>初排与融合依据</strong><p>{paper.ranking_reason}</p></div>}
      {selected && paper.selection_reason && <div className="ranking-reason"><strong>LLM 选择依据</strong><p>{paper.selection_reason}</p></div>}
      {errors.length ? <div className="ranking-errors"><strong>失败与限制</strong><ul>{errors.map((error, index) => <li key={`${error.code ?? 'error'}:${index}`}><span>{errorLabel(error)}</span><small>{errorHint(error.code)}{error.code ? ` · ${error.code}` : ''}</small></li>)}</ul></div> : null}
    </div>
  </details>;
}
