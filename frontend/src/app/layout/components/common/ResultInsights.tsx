import { AlertTriangle, Crosshair, GitMerge, Info } from 'lucide-react';
import type { Paper, PipelineResult, Summary, TargetCoverageEntry } from '../../../types/contracts';
import { rankingFallbackLabel, targetStatusLabel } from '../../../utils/labels';
import {
  isTerminalResult,
  partialReasons,
  paperRanking,
  paperStages,
  rankingForResult,
  rankingModeLabel,
  scoreDescription,
  shortModelName,
  summaryStatus,
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

const coverageReasonLabels: Record<string, string> = {
  related_title_only: '仅找到相关变体，未确认原作',
  abstract_mention_only: '仅摘要提及，未确认原作',
  no_name_clue: '未检索到名称线索',
  direct_not_selected_by_llm: '标题直接线索未被 LLM 选入精选论文',
  selected_but_no_readable_fulltext: '入选但没有可读全文',
  summary_failed: '总结失败，无可用验证内容',
  summary_missing: '总结产物缺失',
  usable_partial_summary: '总结不完整，仅部分可用',
  complete_summary: '已有完整总结',
  pending: '',
};

export function TargetCoverageSummary({ result, compact = false }: { result: PipelineResult; compact?: boolean }) {
  const coverage = targetCoverageForResult(result);
  if (!coverage?.targets?.length) return null;
  const metricValid = result.metrics?.report_valid;
  const reportValid = coverage.report_valid ?? (typeof metricValid === 'boolean' ? metricValid : undefined);
  const terminal = isTerminalResult(result);
  const summariesById = new Map((result.summaries ?? []).map(summary => [summary.paper_id, summary] as const));
  const directCount = coverage.targets.filter(item => (item.direct_title_paper_ids ?? []).length).length;
  const relatedCount = coverage.targets.filter(item =>
    !(item.direct_title_paper_ids ?? []).length && (item.related_title_paper_ids ?? []).length).length;
  const abstractCount = coverage.targets.filter(item =>
    !(item.direct_title_paper_ids ?? []).length && !(item.related_title_paper_ids ?? []).length
    && (item.abstract_mention_paper_ids ?? []).length).length;
  const unresolvedCount = coverage.targets.length - directCount - relatedCount - abstractCount;
  return <details className={`target-coverage ${compact ? 'is-compact' : ''}`} aria-label="点名目标覆盖">
    <summary className="target-coverage-summary">
      <span className="target-coverage-icon"><Crosshair size={14}/></span>
      <div><span className="eyebrow">点名目标</span><strong>{coverage.targets.length} 个 · 直接线索 {directCount} · 相关变体 {relatedCount} · 仅摘要 {abstractCount} · 未解决 {unresolvedCount}</strong></div>
      {typeof reportValid === 'boolean' && <span className={`status-chip status-${reportValid ? 'completed' : 'partial'}`}>{reportValid ? '报告有效' : '报告未通过校验'}</span>}
      <span className="target-coverage-toggle">展开/收起审计</span>
    </summary>
    <p className="target-coverage-note">标题线索默认不等于原论文身份确认；只有用户提供确切完整标题并完全匹配时才标记确认。简称、相关变体和摘要提及均保留未确认状态，是否选入和是否构成有效分析仍由真实检索、模型判断与后续结果决定。</p>
    <ul className="target-coverage-list">
      {coverage.targets.map((item, index) => <TargetCoverageRow
        key={`${item.target}:${index}`}
        item={item}
        reportValid={reportValid}
        terminal={terminal}
        stage={coverage.stage}
        summariesById={summariesById}
      />)}
    </ul>
  </details>;
}

function TargetCoverageRow({ item, reportValid, terminal, stage, summariesById }: {
  item: TargetCoverageEntry; reportValid?: boolean; terminal: boolean; stage?: string;
  summariesById: Map<string, Summary>;
}) {
  const direct = item.direct_title_paper_ids ?? [];
  const related = item.related_title_paper_ids ?? [];
  const abstract = item.abstract_mention_paper_ids ?? [];
  const pool = item.selector_pool_direct_paper_ids ?? [];
  const analysis = item.analysis_paper_ids ?? [];
  const complete = item.complete_fulltext_paper_ids ?? [];
  const partial = item.partial_fulltext_paper_ids ?? [];
  const summarized = item.summarized_paper_ids ?? [];
  const report = item.report_paper_ids ?? [];
  const legacy = item.status === 'title_match' || item.status === 'not_retrieved';
  const summaryStatuses = [...new Set([...summarized, ...(item.partial_summary_paper_ids ?? [])])]
    .map(paperId => ({ paperId, status: summaryStatus(summariesById.get(paperId)) }));
  const legacyComplete = summaryStatuses.filter(entry => entry.status === 'complete').map(entry => entry.paperId);
  const legacyPartial = summaryStatuses.filter(entry => entry.status === 'usable_partial').map(entry => entry.paperId);
  const legacyFailed = summaryStatuses.filter(entry => entry.status === 'failed').map(entry => entry.paperId);
  const completeSummary = item.complete_summary_paper_ids ?? legacyComplete;
  const partialSummary = item.usable_partial_summary_paper_ids
    ?? (item.partial_summary_paper_ids ?? []).filter(paperId => summaryStatus(summariesById.get(paperId)) === 'usable_partial');
  const failedSummary = item.failed_summary_paper_ids
    ?? legacyFailed;
  const missingSummary = item.missing_summary_paper_ids
    ?? analysis.filter(paperId => !summariesById.has(paperId));
  const legacyPartialCount = legacyPartial.length && !item.usable_partial_summary_paper_ids ? legacyPartial.length : 0;
  const stageReached = (...values: string[]) => Boolean(stage && values.includes(stage));
  const selectionPending = !terminal && !stageReached('selection', 'fulltexts', 'summarize', 'report');
  const analysisPending = !terminal && !stageReached('fulltexts', 'summarize', 'report');
  const summaryPending = !terminal && !stageReached('summarize', 'report');
  const reportPending = !terminal && !stageReached('report');
  const retrieval = legacy
    ? { label: targetStatusLabel(item.status), tone: 'is-warn', title: [...direct, ...related, ...abstract].join(', ') }
    : direct.length
      ? { label: `标题直接线索 ${direct.length}`, tone: 'is-good', title: direct.join(', ') }
      : related.length
        ? { label: `标题相关/变体 ${related.length}`, tone: 'is-warn', title: related.join(', ') }
        : abstract.length
          ? { label: `仅摘要提及 ${abstract.length}`, tone: 'is-warn', title: abstract.join(', ') }
          : { label: '身份未解决', tone: 'is-warn', title: '' };
  const identity = item.identity_confirmed
    ? { label: '身份：用户确切标题', tone: 'is-good', title: (item.identity_basis ?? []).join(', ') }
    : legacy
      ? { label: '旧记录：身份未确认', tone: '', title: (item.identity_basis ?? []).join(', ') }
      : item.identity_status && item.identity_status !== 'unresolved'
        ? { label: '身份未确认', tone: '', title: (item.identity_basis ?? []).join(', ') }
        : { label: '身份未解决', tone: 'is-warn', title: '' };
  const execution = item.query_execution?.find(entry => entry.mode === 'title') ?? item.query_execution?.[0];
  const queryLabel = execution?.mode === 'title'
    ? `标题查询：${execution.executed ?? item.query ?? ''}`
    : item.query_covered && item.query ? `定向查询：${item.query}` : '无定向查询';
  const queryTitle = execution
    ? `${execution.source ?? ''} · ${execution.planned ?? ''} → ${execution.executed ?? ''} · ${execution.result_count ?? '—'} 条${execution.error_code ? ` · ${execution.error_code}` : ''}`
    : item.query ?? '';
  const selectorChip = selectionPending
    ? { label: '等待筛选', tone: '', title: '' }
    : !direct.length && !related.length
      ? { label: '无标题线索候选', tone: '', title: '' }
      : pool.length
        ? { label: `进入 Selector 池 ${pool.length}`, tone: 'is-good', title: pool.join(', ') }
        : { label: '标题线索未进入 Selector 池', tone: 'is-warn', title: [...direct, ...related].join(', ') };
  const analysisChip = analysisPending
    ? { label: '等待全文/分析', tone: '', title: '' }
    : analysis.length
      ? { label: `最终分析 ${analysis.length}`, tone: 'is-good', title: analysis.join(', ') }
      : { label: terminal ? '未进入最终分析' : '暂无最终分析', tone: 'is-warn', title: [...direct, ...related, ...abstract].join(', ') };
  const fulltextChip = analysisPending
    ? { label: '等待全文处理', tone: '', title: '' }
    : !analysis.length
      ? { label: '未进入全文分析', tone: '', title: '' }
      : complete.length
        ? { label: `可读全文 ${complete.length}`, tone: 'is-good', title: complete.join(', ') }
        : partial.length
          ? { label: `部分全文 ${partial.length}`, tone: 'is-warn', title: partial.join(', ') }
          : { label: terminal ? '无可读全文' : '全文处理中', tone: terminal ? 'is-warn' : '', title: '' };
  const summaryParts = [
    completeSummary.length ? `完整 ${completeSummary.length}` : '',
    partialSummary.length || legacyPartialCount ? `可用部分 ${partialSummary.length || legacyPartialCount}` : '',
    failedSummary.length ? `失败 ${failedSummary.length}` : '',
    missingSummary.length ? `缺失 ${missingSummary.length}` : '',
  ].filter(Boolean);
  const summaryChip = summaryPending
    ? { label: '等待总结', tone: '', title: '' }
    : !analysis.length
      ? { label: '未进入总结', tone: '', title: '' }
      : summaryParts.length
        ? {
          label: `总结 ${summaryParts.join(' · ')}`,
          tone: failedSummary.length || missingSummary.length ? 'is-warn' : partialSummary.length || legacyPartialCount ? 'is-warn' : 'is-good',
          title: [...completeSummary, ...partialSummary, ...failedSummary, ...missingSummary].join(', '),
        }
        : { label: terminal ? '总结失败或缺失' : '总结生成中', tone: terminal ? 'is-warn' : '', title: '' };
  const reasonLabel = (() => {
    if (!terminal && !stageReached('report')) return '';
    const backendReason = item.coverage_reason ? coverageReasonLabels[item.coverage_reason] : '';
    if (backendReason) return backendReason;
    if (!direct.length && !related.length && !abstract.length) return '未检索到名称线索';
    if (!analysis.length) return '有线索但未进入最终分析';
    if (failedSummary.length) return '总结失败，无可用验证内容';
    if (!summaryParts.length) return '总结产物缺失';
    if (partialSummary.length || legacyPartialCount) return '总结不完整，仅部分可用';
    return '';
  })();
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
    { label: identity.label, tone: identity.tone, title: identity.title },
    ...(direct.length && related.length ? [{
      label: `另有相关变体 ${related.length}`, tone: 'is-warn', title: related.join(', '),
    }] : []),
    ...((direct.length || related.length) && abstract.length ? [{
      label: `另有摘要提及 ${abstract.length}`, tone: '', title: abstract.join(', '),
    }] : []),
    { label: queryLabel, tone: item.query_covered ? '' : 'is-warn', title: queryTitle },
    selectorChip,
    analysisChip,
    fulltextChip,
    summaryChip,
    reportChip,
    ...(reasonLabel ? [{ label: `原因：${reasonLabel}`, tone: 'is-warn', title: '' }] : []),
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
      {errors.length ? <p className="ranking-caveat">这篇论文存在处理限制；原始诊断已归入默认折叠的执行记录。</p> : null}
    </div>
  </details>;
}
