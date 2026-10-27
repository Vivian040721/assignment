import { AlertTriangle, Crosshair, GitMerge, Info } from 'lucide-react';
import type { Paper, PipelineResult, Summary, TargetCoverageEntry } from '../../../types/contracts';
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
  targetCoverageForResult,
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

export function TargetCoverageSummary({ result, compact = false }: { result: PipelineResult; compact?: boolean }) {
  const coverage = targetCoverageForResult(result);
  if (!coverage?.targets?.length) return null;
  const metricValid = result.metrics?.report_valid;
  const reportValid = coverage.report_valid ?? (typeof metricValid === 'boolean' ? metricValid : undefined);
  const terminal = isTerminalResult(result);
  return <section className={`target-coverage ${compact ? 'is-compact' : ''}`} aria-label="点名目标覆盖">
    <div className="target-coverage-head">
      <span className="target-coverage-icon"><Crosshair size={14}/></span>
      <div><span className="eyebrow">点名目标</span><strong>真实检索与后续覆盖</strong></div>
      {typeof reportValid === 'boolean' && <span className={`status-chip status-${reportValid ? 'completed' : 'partial'}`}>{reportValid ? '报告有效' : '报告未通过校验'}</span>}
    </div>
    <p className="target-coverage-note">标题匹配只是词面线索，不代表已验证原始论文身份；是否选入、是否构成有效分析仍由真实检索、模型判断与后续结果决定。</p>
    <ul className="target-coverage-list">
      {coverage.targets.map((item, index) => <TargetCoverageRow
        key={`${item.target}:${index}`}
        item={item}
        reportValid={reportValid}
        terminal={terminal}
        stage={coverage.stage}
      />)}
    </ul>
  </section>;
}

function TargetCoverageRow({ item, reportValid, terminal, stage }: {
  item: TargetCoverageEntry; reportValid?: boolean; terminal: boolean; stage?: string;
}) {
  const direct = item.direct_title_paper_ids ?? [];
  const abstract = item.abstract_mention_paper_ids ?? [];
  const pool = item.selector_pool_direct_paper_ids ?? [];
  const analysis = item.analysis_paper_ids ?? [];
  const complete = item.complete_fulltext_paper_ids ?? [];
  const partial = item.partial_fulltext_paper_ids ?? [];
  const summarized = item.summarized_paper_ids ?? [];
  const fulltextSummary = item.fulltext_summary_paper_ids ?? [];
  const partialSummary = item.partial_summary_paper_ids ?? [];
  const report = item.report_paper_ids ?? [];
  const stageReached = (...values: string[]) => Boolean(stage && values.includes(stage));
  const selectionPending = !terminal && !stageReached('selection', 'fulltexts', 'summarize', 'report');
  const analysisPending = !terminal && !stageReached('fulltexts', 'summarize', 'report');
  const summaryPending = !terminal && !stageReached('summarize', 'report');
  const reportPending = !terminal && !stageReached('report');
  const retrieval = direct.length
    ? { label: `标题词面匹配 ${direct.length}`, tone: 'is-good', title: direct.join(', ') }
    : abstract.length
      ? { label: `仅摘要提及 ${abstract.length}`, tone: 'is-warn', title: abstract.join(', ') }
      : { label: '未检索到', tone: 'is-warn', title: '' };
  const selectorChip = selectionPending
    ? { label: '等待筛选', tone: '', title: '' }
    : !direct.length
      ? { label: '无直接候选', tone: '', title: '' }
      : pool.length
        ? { label: `进入 Selector 池 ${pool.length}`, tone: 'is-good', title: pool.join(', ') }
        : { label: '直接匹配未进入 Selector 池', tone: 'is-warn', title: direct.join(', ') };
  const analysisChip = analysisPending
    ? { label: '等待全文/分析', tone: '', title: '' }
    : analysis.length
      ? { label: `最终分析 ${analysis.length}`, tone: 'is-good', title: analysis.join(', ') }
      : { label: terminal ? '未进入最终分析' : '暂无最终分析', tone: 'is-warn', title: direct.join(', ') };
  const fulltextChip = analysisPending
    ? { label: '等待全文处理', tone: '', title: '' }
    : !analysis.length
      ? { label: '未进入全文分析', tone: '', title: '' }
      : complete.length
        ? { label: `全文完整 ${complete.length}`, tone: 'is-good', title: complete.join(', ') }
        : partial.length
          ? { label: `部分全文 ${partial.length}`, tone: 'is-warn', title: partial.join(', ') }
          : { label: terminal ? '无可读全文' : '全文处理中', tone: terminal ? 'is-warn' : '', title: '' };
  const summaryChip = summaryPending
    ? { label: '等待总结', tone: '', title: '' }
    : !analysis.length
      ? { label: '未进入总结', tone: '', title: '' }
      : fulltextSummary.length
        ? { label: `总结完整 ${fulltextSummary.length}`, tone: 'is-good', title: fulltextSummary.join(', ') }
        : summarized.length
          ? { label: `部分总结 ${summarized.length}`, tone: 'is-warn', title: summarized.join(', ') }
          : { label: terminal ? '总结未完成' : '总结生成中', tone: terminal ? 'is-warn' : '', title: '' };
  const reportChip = reportPending
    ? { label: '等待报告', tone: '', title: '' }
    : report.length
      ? {
        label: reportValid === false ? `报告引用 ${report.length}（报告未通过校验）` : `报告引用 ${report.length}`,
        tone: reportValid === false ? 'is-warn' : 'is-good',
        title: report.join(', '),
      }
      : { label: terminal ? '未纳入报告' : '报告处理中', tone: terminal ? 'is-warn' : '', title: '' };
  const chips = [
    { label: retrieval.label, tone: retrieval.tone, title: retrieval.title },
    {
      label: item.query_covered && item.query ? `定向查询：${item.query}` : '无定向查询',
      tone: item.query_covered ? '' : 'is-warn', title: item.query ?? '',
    },
    selectorChip,
    analysisChip,
    fulltextChip,
    { ...summaryChip, title: [...fulltextSummary, ...partialSummary].join(', ') || summaryChip.title },
    reportChip,
  ];
  return <li className="target-coverage-row">
    <div className="target-coverage-name"><strong>{item.target}</strong></div>
    <div className="target-coverage-chips">
      {chips.map((chip, index) => <span key={`${chip.label}:${index}`} className={`target-chip ${chip.tone}`} title={chip.title || undefined}>{chip.label}</span>)}
    </div>
  </li>;
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
