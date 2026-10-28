import { ArrowUpRight, BookOpen, Search } from 'lucide-react';
import type { Paper, Summary } from '../../../types/contracts';
import { useWorkspaceState } from '../../../stores/workspaceStore';
import { collectPapers, summaryScopeLabel, summaryStatus, summaryStatusLabel } from '../../../utils/papers';
import { layoutStore } from '../../stores/layoutStore';

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
  const finalPapers = analysisPapers.length > 0 ? analysisPapers : collection.topPapers;

  if (loadingConversation) {
    return <div className="dock-panel-skeleton" aria-live="polite">
      <span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/>
    </div>;
  }
  if (!result) {
    return <div className="panel-empty"><span><BookOpen size={21}/></span><strong>论文结果会显示在这里</strong><p>提交研究问题后，进入分析的关键论文会集中显示在这里。</p></div>;
  }

  return (
    <div className="paper-list-view">
      <div className="panel-intro">
        <div><span className="eyebrow">本次研究</span><h2>关键论文</h2></div>
        {finalPapers.length > 0 && <span className="paper-count">{finalPapers.length} 篇</span>}
      </div>
      <p className="panel-subtitle">点击论文进入研究笔记；有 PDF 原文时可直接切换阅读。</p>
      {finalPapers.length > 0 && <section className="paper-section" aria-labelledby="effective-papers-title">
        <h3 id="effective-papers-title" className="sr-only">实际分析论文</h3>
        {finalPapers.map((paper, index) => <PaperRow
          key={paper.paper_id}
          paper={paper}
          index={index + 1}
          jobId={jobId}
          summary={summaries.get(paper.paper_id)}
        />)}
      </section>}
      {finalPapers.length === 0 && <div className="panel-empty compact-empty"><span><Search size={19}/></span><strong>任务没有返回论文</strong><p>详细原因保留在执行记录中。</p></div>}
    </div>
  );
}

function PaperRow({ paper, index, jobId, summary }: {
  paper: Paper;
  index: number;
  jobId?: string;
  summary?: Summary;
}) {
  const material = materialNote(paper, summary);
  const reason = summary?.relevance?.trim() || paper.selection_reason?.trim();
  return <article className="paper-row">
    <span className="paper-rank" aria-hidden="true">{index}</span>
    <div className="paper-row-copy">
      <strong>{paper.title}</strong>
      <span>{paper.authors.slice(0, 3).join(', ')}{paper.authors.length > 3 ? ' 等' : ''}{paper.year ? ` · ${paper.year}` : ''}</span>
      {reason && <p className="paper-reason">{reason}</p>}
      <small className="paper-material">{material}</small>
    </div>
    <button className="paper-read-button" type="button" aria-label={`阅读论文：${paper.title}`} onClick={() => layoutStore.openPaper(paper.paper_id, paper.title, jobId)}>
      阅读<ArrowUpRight size={14}/>
    </button>
  </article>;
}

function materialNote(paper: Paper, summary?: Summary): string {
  const hasPdf = Boolean(paper.pdf_artifact_id || paper.pdf_path);
  const status = summaryStatus(summary);
  if (summary) {
    const note = `${summaryStatusLabel(status)} · ${summaryScopeLabel(summary.summary_scope)}`;
    return hasPdf ? `${note} · PDF 原文可读` : `${note} · PDF 原文缺失`;
  }
  return hasPdf ? 'PDF 原文可读 · 研究笔记缺失' : '当前只提供论文元数据';
}
