import { ArrowUpRight, BookOpen, FileText, Search } from 'lucide-react';
import { useWorkspaceState } from '../../../stores/workspaceStore';
import { collectPapers, paperStatusLabel } from '../../../utils/papers';
import { errorLabel } from '../../../utils/labels';
import { statusLabel } from '../../../utils/status';
import { layoutStore } from '../../stores/layoutStore';

export function PaperList() {
  const { activeJob, resultJob, conversation, loadingConversation } = useWorkspaceState();
  const resultOwner = resultJob ?? activeJob;
  const collection = collectPapers(resultOwner?.result ?? conversation?.result ?? null);
  const result = collection.result;
  const jobId = resultOwner?.job_id;

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
      <p className="panel-subtitle">原始选择 Top-{result.top_k} 保持不变；替补论文单独标记。</p>
      {collection.topPapers.length > 0 && <section className="paper-section" aria-labelledby="original-papers-title">
        <h3 id="original-papers-title">原始 Top-k <span>{collection.topPapers.length}</span></h3>
        {collection.topPapers.map((paper, index) => <PaperRow key={paper.paper_id} paper={paper} index={paper.rank ?? index + 1} label="原始入选" jobId={jobId}/>) }
      </section>}
      {collection.replacementPairs.length > 0 && <section className="paper-section paper-section-secondary" aria-labelledby="replacement-papers-title">
        <h3 id="replacement-papers-title">全文替补 <span>{collection.replacementPairs.length}</span></h3>
        {collection.replacementPairs.map((pair, index) => <div className="replacement-pair" key={`${String(pair.raw.original_paper_id ?? index)}:${String(pair.raw.replacement_paper_id ?? index)}`}>
          {pair.original && <p className="replacement-source">替代原论文 #{pair.original.rank ?? '—'}：{pair.original.title}</p>}
          {pair.replacement ? <PaperRow paper={pair.replacement} index={index + 1} label="实际分析" jobId={jobId}/> : <p className="missing-replacement"><FileText size={15}/>替补记录存在，但候选详情未随结果返回。</p>}
        </div>)}
      </section>}
      {collection.analysisPapers.length > 0 && <section className="paper-section paper-section-secondary" aria-labelledby="analysis-papers-title">
        <h3 id="analysis-papers-title">其他实际分析论文 <span>{collection.analysisPapers.length}</span></h3>
        {collection.analysisPapers.map((paper, index) => <PaperRow key={paper.paper_id} paper={paper} index={index + 1} label="实际分析" jobId={jobId}/>) }
      </section>}
      {collection.topPapers.length === 0 && collection.analysisPapers.length === 0 && <div className="panel-empty compact-empty"><span><Search size={19}/></span><strong>任务没有返回论文</strong><p>任务状态：{statusLabel(result.status)}。详细错误保留在活动记录中。</p></div>}
      {result.errors.length > 0 && <details className="result-errors"><summary>研究错误与部分结果（{result.errors.length}）</summary><ul>{result.errors.map((error, index) => <li key={`${error.code ?? 'error'}:${index}`}><strong>{error.code ? errorLabel(error) : '任务'}</strong>{error.message}</li>)}</ul></details>}
    </div>
  );
}

function PaperRow({ paper, index, label, jobId }: { paper: import('../../../types/contracts').Paper; index: number; label: string; jobId?: string }) {
  const score = paper.final_score ?? paper.initial_score;
  return <article className="paper-row">
    <span className="paper-rank">{paper.rank ?? index}</span>
    <div className="paper-row-copy"><strong>{paper.title}</strong><span>{paper.authors.slice(0, 3).join(', ')}{paper.authors.length > 3 ? ' 等' : ''}{paper.year ? ` · ${paper.year}` : ''}</span>
      <div className="paper-tags">
        <span>{label}</span>
        <span>{paperStatusLabel(paper.status)}</span>
        {typeof score === 'number' && Number.isFinite(score) ? <span>相关度 {Math.round(score * 100)}%</span> : null}
        {paper.pdf_path ? <span>PDF 已缓存</span> : null}
        {paper.markdown_path ? <span>Markdown 就绪</span> : null}
      </div>
      {paper.selection_reason ? <p className="paper-reason">{paper.selection_reason}</p> : null}
    </div>
    <button className="icon-button paper-open" type="button" aria-label={`打开论文详情：${paper.title}`} title="打开详情" onClick={() => layoutStore.openPaper(paper.paper_id, paper.title, jobId)}><ArrowUpRight size={16}/></button>
  </article>;
}
