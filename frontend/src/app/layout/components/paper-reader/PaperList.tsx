import { ArrowUpRight, BookOpen, Search } from 'lucide-react';
import type { Artifact, Paper, Summary } from '../../../types/contracts';
import { useWorkspaceState } from '../../../stores/workspaceStore';
import { collectPapers, summaryScopeLabel, summaryStatus, summaryStatusLabel } from '../../../utils/papers';
import { displayedPapers, paperMaterialNote } from '../../../utils/resultPresentation';
import { layoutStore } from '../../stores/layoutStore';

export function PaperList() {
  const { activeJob, resultJob, conversation, loadingConversation } = useWorkspaceState();
  const resultOwner = resultJob ?? activeJob;
  const collection = collectPapers(resultOwner?.result ?? conversation?.result ?? null);
  const result = collection.result;
  const jobId = resultOwner?.job_id;
  const summaries = new Map(collection.summaries.map(summary => [summary.paper_id, summary] as const));
  const displayed = displayedPapers(collection);
  const artifacts = resultOwner?.artifacts ?? [];

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
        {displayed.papers.length > 0 && <span className="paper-count">{displayed.papers.length} 篇</span>}
      </div>
      <p className="panel-subtitle">{displayed.description} 点击论文进入研究笔记；已关联 PDF 文件时可尝试原文阅读。</p>
      {displayed.papers.length > 0 && <section className="paper-section" aria-labelledby="effective-papers-title">
        <h3 id="effective-papers-title" className="sr-only">{displayed.stage === 'analysis' ? '实际分析论文' : '精选候选论文'}</h3>
        {displayed.papers.map((paper, index) => <PaperRow
          key={paper.paper_id}
          paper={paper}
          index={index + 1}
          jobId={jobId}
          summary={summaries.get(paper.paper_id)}
          artifacts={artifacts}
        />)}
      </section>}
      {displayed.papers.length === 0 && <div className="panel-empty compact-empty"><span><Search size={19}/></span><strong>任务没有返回论文</strong><p>详细原因保留在执行记录中。</p></div>}
    </div>
  );
}

function PaperRow({ paper, index, jobId, summary, artifacts }: {
  paper: Paper;
  index: number;
  jobId?: string;
  summary?: Summary;
  artifacts: Artifact[];
}) {
  const material = materialNote(paper, summary, artifacts);
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

function materialNote(paper: Paper, summary: Summary | undefined, artifacts: Artifact[]): string {
  const material = paperMaterialNote(paper, artifacts);
  const status = summaryStatus(summary);
  if (summary) {
    const note = `${summaryStatusLabel(status)} · ${summaryScopeLabel(summary.summary_scope)}`;
    return `${note} · ${material}`;
  }
  return `${material} · 研究笔记缺失`;
}
