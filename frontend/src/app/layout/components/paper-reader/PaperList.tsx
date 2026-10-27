import { ArrowUpRight, BookOpen, FileText, Search } from 'lucide-react';
import type { Paper, Summary } from '../../../types/contracts';
import { useWorkspaceState } from '../../../stores/workspaceStore';
import {
  collectPapers,
  paperIsCompleteFulltext,
  paperRanking,
  paperStatusLabel,
  replacementAttemptsForResult,
  scoreShort,
  summaryIsComplete,
  summaryScopeLabel,
} from '../../../utils/papers';
import { errorHint, errorLabel, paperErrorLabel } from '../../../utils/labels';
import { statusLabel } from '../../../utils/status';
import { layoutStore } from '../../stores/layoutStore';
import {
  PaperRankingDetails,
  PartialResultSummary,
  RankingSummary,
  TargetCoverageSummary,
} from '../common/ResultInsights';

export function PaperList() {
  const { activeJob, resultJob, conversation, loadingConversation } = useWorkspaceState();
  const resultOwner = resultJob ?? activeJob;
  const collection = collectPapers(resultOwner?.result ?? conversation?.result ?? null);
  const result = collection.result;
  const jobId = resultOwner?.job_id;
  const summaries = new Map(collection.summaries.map(summary => [summary.paper_id, summary] as const));
  const analysisIds = result && Array.isArray(result.metrics?.analysis_paper_ids)
    ? result.metrics.analysis_paper_ids.filter((value): value is string => typeof value === 'string')
    : [];
  const analysisPapers = analysisIds
    .map(paperId => collection.paperById.get(paperId))
    .filter((paper): paper is Paper => Boolean(paper));
  const selectedIds = new Set(collection.topPapers.map(paper => paper.paper_id));
  const replacementIds = new Set(collection.replacementPairs
    .map(pair => pair.replacement?.paper_id)
    .filter((value): value is string => Boolean(value)));
  const replacementAttempts = replacementAttemptsForResult(result);
  const attemptById = new Map(
    replacementAttempts
      .filter(item => typeof item.replacement_paper_id === 'string')
      .map(item => [item.replacement_paper_id as string, item] as const),
  );
  const reportIds = new Set((result?.report?.references ?? []).map(reference => reference.paper_id));
  const finalPapers = analysisPapers.length > 0 ? analysisPapers : collection.topPapers;
  const roleLabel = (paper: Paper): string => {
    if (selectedIds.has(paper.paper_id)) return '原始入选';
    const attempt = attemptById.get(paper.paper_id);
    if (attempt) {
      return attempt.complete ? '完整全文替补' : attempt.in_analysis ? '部分全文替补' : '候补未进入分析';
    }
    if (replacementIds.has(paper.paper_id)) return '全文替补';
    return '候补分析';
  };

  if (loadingConversation) {
    return <div className="dock-panel-skeleton" aria-live="polite">
      <span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/>
    </div>;
  }
  if (!result) {
    return <div className="panel-empty"><span><BookOpen size={21}/></span><strong>论文结果会显示在这里</strong><p>提交研究问题后，原始 Top-k、全文处理情况和实际分析论文会保留在同一处。</p></div>;
  }

  return (
    <div className="paper-list-view">
      <div className="panel-intro">
        <div><span className="eyebrow">本次研究</span><h2>论文</h2></div>
        <span className={`status-chip status-${result.status}`}>{statusLabel(result.status)}</span>
      </div>
      <p className="panel-subtitle">最终分析 Top-k 会在全文不可用时按受限候补队列顺延；原始 LLM 选择保留在审计区，排序模式、全文范围与总结范围均来自本次运行。</p>
      <RankingSummary result={result}/>
      <TargetCoverageSummary result={result}/>
      <PartialResultSummary result={result} compact/>
      {analysisPapers.length > 0 && <section className="paper-section" aria-labelledby="effective-papers-title">
        <h3 id="effective-papers-title">最终分析 Top-k <span>{finalPapers.length}</span></h3>
        {finalPapers.map((paper, index) => <PaperRow
          key={paper.paper_id}
          paper={paper}
          index={index + 1}
          displayRank={index + 1}
          label={roleLabel(paper)}
          selected={selectedIds.has(paper.paper_id)}
          jobId={jobId}
          summary={summaries.get(paper.paper_id)}
          reportIds={reportIds}
        />)}
      </section>}
      {analysisPapers.length === 0 && collection.topPapers.length > 0 && <section className="paper-section" aria-labelledby="original-papers-title">
        <h3 id="original-papers-title">原始 Top-k <span>{collection.topPapers.length}</span></h3>
        {collection.topPapers.map((paper, index) => <PaperRow key={paper.paper_id} paper={paper} index={paper.rank ?? index + 1} label="原始入选" selected jobId={jobId} summary={summaries.get(paper.paper_id)} reportIds={reportIds}/>)}
      </section>}
      {(analysisPapers.length > 0 || replacementAttempts.length > 0) && (collection.topPapers.length > 0 || collection.replacementPairs.length > 0 || replacementAttempts.length > 0) && <details className="paper-audit">
        <summary>原始 Top-k 与替补审计</summary>
        {analysisPapers.length > 0 && collection.topPapers.length > 0 && <div className="paper-audit-group">
          <h3>原始 LLM Top-k <span>{collection.topPapers.length}</span></h3>
          {collection.topPapers.map((paper, index) => <PaperRow key={`audit-original:${paper.paper_id}`} paper={paper} index={paper.rank ?? index + 1} label="原始入选" selected jobId={jobId} summary={summaries.get(paper.paper_id)} reportIds={reportIds}/>)}
        </div>}
        {replacementAttempts.length > 0 && <div className="paper-audit-group">
          <h3>全文候补尝试 <span>{replacementAttempts.length}</span></h3>
          <p className="paper-audit-note">候补使用完整排序候选池；完整替补保留在 replacements，部分全文候补单独标识，原始 LLM Top-k 不被改写。</p>
          {replacementAttempts.map((attempt, index) => {
            const replacement = typeof attempt.replacement_paper_id === 'string'
              ? collection.paperById.get(attempt.replacement_paper_id)
              : undefined;
            const original = typeof attempt.original_paper_id === 'string'
              ? collection.paperById.get(attempt.original_paper_id) ?? collection.topPapers.find(paper => paper.paper_id === attempt.original_paper_id)
              : undefined;
            const failure = attempt.failure_code ? errorLabel({ code: attempt.failure_code }) : '';
            return <div className="replacement-attempt" key={`${String(attempt.replacement_paper_id ?? index)}:${index}`}>
              <div className="replacement-source">
                <p>候补 #{attempt.attempt_order ?? index + 1}{original ? ` → 原始论文：${original.title}` : '（无对应原始论文）'}</p>
                <small>
                  {paperStatusLabel(attempt.status ?? replacement?.status)}
                  {failure ? ` · ${failure}` : ''}
                  {attempt.in_analysis
                    ? ` · 进入分析${attempt.summary_scope ? `（${summaryScopeLabel(attempt.summary_scope)}）` : ''}`
                    : ' · 未进入分析'}
                </small>
              </div>
              {replacement ? <PaperRow
                paper={replacement}
                index={index + 1}
                label={attempt.complete ? '完整全文替补' : attempt.in_analysis ? '部分全文替补' : '候补未进入分析'}
                selected={false}
                jobId={jobId}
                summary={summaries.get(replacement.paper_id)}
                reportIds={reportIds}
              /> : <p className="missing-replacement"><FileText size={15}/>候补记录存在，但候选详情未随结果返回。</p>}
            </div>;
          })}
        </div>}
        {replacementAttempts.length === 0 && collection.replacementPairs.length > 0 && <div className="paper-audit-group">
          <h3>全文替补映射 <span>{collection.replacementPairs.length}</span></h3>
          {collection.replacementPairs.map((pair, index) => <div className="replacement-pair" key={`${String(pair.raw.original_paper_id ?? index)}:${String(pair.raw.replacement_paper_id ?? index)}`}>
            {pair.original && <div className="replacement-source">
              <p>替代原论文 #{pair.original.rank ?? '—'}：{pair.original.title}</p>
              <small>{paperStatusLabel(pair.original.status)}{paperErrorLabel(pair.original) ? ` · ${paperErrorLabel(pair.original)}` : ' · 未取得完整全文'}</small>
            </div>}
            {pair.replacement ? <PaperRow paper={pair.replacement} index={index + 1} label="替补进入分析" selected={false} jobId={jobId} summary={summaries.get(pair.replacement.paper_id)} reportIds={reportIds}/> : <p className="missing-replacement"><FileText size={15}/>替补记录存在，但候选详情未随结果返回。</p>}
          </div>)}
        </div>}
        {collection.analysisPapers.length > 0 && <div className="paper-audit-group">
          <h3>其他实际分析 <span>{collection.analysisPapers.length}</span></h3>
          {collection.analysisPapers.map((paper, index) => <PaperRow key={`audit-analysis:${paper.paper_id}`} paper={paper} index={index + 1} label="候补分析" selected={false} jobId={jobId} summary={summaries.get(paper.paper_id)}/>)}
        </div>}
      </details>}
      {finalPapers.length === 0 && <div className="panel-empty compact-empty"><span><Search size={19}/></span><strong>任务没有返回论文</strong><p>任务状态：{statusLabel(result.status)}。详细错误保留在活动记录中。</p></div>}
      {result.errors.length > 0 && <details className="result-errors">
        <summary>技术错误与状态码（{result.errors.length}）</summary>
        <ul>{result.errors.map((error, index) => <li key={`${error.code ?? 'error'}:${index}`}>
          <strong>{error.code ? errorLabel(error) : '任务'}</strong>
          <span>{errorHint(error.code)}{error.code ? ` · ${error.code}` : ''}</span>
        </li>)}</ul>
      </details>}
    </div>
  );
}

function PaperRow({ paper, index, displayRank, label, selected, jobId, summary, reportIds }: {
  paper: Paper;
  index: number;
  displayRank?: number;
  label: string;
  selected: boolean;
  jobId?: string;
  summary?: Summary;
  reportIds?: Set<string>;
}) {
  const ranking = paperRanking(paper);
  const completeFulltext = paperIsCompleteFulltext(paper);
  const summaryComplete = summaryIsComplete(summary);
  const summaryTag = !summary
    ? '总结：未生成'
    : summaryComplete
      ? '总结完整'
      : `总结：${summaryScopeLabel(summary.summary_scope)}${summary.errors?.length ? '（有错误）' : ''}`;
  return <article className="paper-row">
    <span className="paper-rank" title={displayRank ? '最终分析编号' : '原始排序名次'}>{displayRank ?? (selected ? paper.rank ?? index : ranking.rrfRank ?? ranking.keywordRank ?? paper.rank ?? index)}</span>
    <div className="paper-row-copy"><strong>{paper.title}</strong><span>{paper.authors.slice(0, 3).join(', ')}{paper.authors.length > 3 ? ' 等' : ''}{paper.year ? ` · ${paper.year}` : ''}</span>
      <div className="paper-tags">
        <span>{label}</span>
        <span>{paperStatusLabel(paper.status)}</span>
        <span>{scoreShort(paper)}</span>
        {typeof ranking.keywordRank === 'number' && <span>关键词 #{ranking.keywordRank}</span>}
        {typeof ranking.semanticRank === 'number' && <span>语义 #{ranking.semanticRank}</span>}
        {typeof ranking.rrfRank === 'number' && <span>RRF #{ranking.rrfRank}</span>}
        {selected && typeof paper.rank === 'number' && <span>LLM 名次 #{paper.rank}</span>}
        <span>{summaryTag}</span>
        {paper.pdf_path ? <span>PDF 已保存</span> : null}
        {paper.markdown_path ? <span>{completeFulltext ? '全文完整' : '部分全文'}</span> : null}
        {reportIds?.has(paper.paper_id) ? <span>报告已引用</span> : null}
      </div>
      {paper.selection_reason ? <p className="paper-reason">{paper.selection_reason}</p> : null}
      <PaperRankingDetails paper={paper} selected={selected} compact/>
    </div>
    <button className="icon-button paper-open" type="button" aria-label={`打开论文详情：${paper.title}`} title="打开详情" onClick={() => layoutStore.openPaper(paper.paper_id, paper.title, jobId)}><ArrowUpRight size={16}/></button>
  </article>;
}
