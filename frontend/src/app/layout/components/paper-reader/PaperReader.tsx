import { useEffect, useState } from 'react';
import { ArrowUpRight, BookOpen, Copy, FileDown, FileText } from 'lucide-react';
import type { Artifact, Job } from '../../../types/contracts';
import type { LayoutTab } from '../../../types/ui';
import { desktopApi } from '../../../services/desktopApi';
import { useWorkspaceState, workspaceStore } from '../../../stores/workspaceStore';
import { collectPapers, isTerminalResult, isTerminalStatus, paperStatusLabel, scoreShort, summaryScopeLabel } from '../../../utils/papers';
import { safeHttpUrl } from '../../../utils/format';
import { errorHint, errorLabel, paperErrorLabel } from '../../../utils/labels';
import { MarkdownContent } from '../common/MarkdownContent';
import { PaperProgress, PaperRankingDetails } from '../common/ResultInsights';

type ReaderView = 'summary' | 'fulltext';

export function PaperReader({ tab }: { tab: LayoutTab }) {
  const { activeJob, resultJob, conversation, loadingConversation } = useWorkspaceState();
  const currentJob = resultJob ?? activeJob;
  const [remoteJob, setRemoteJob] = useState<Job | null>(null);
  const [remoteLoading, setRemoteLoading] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const job = tab.jobId && tab.jobId !== currentJob?.job_id ? remoteJob : currentJob;

  useEffect(() => {
    if (!tab.jobId || tab.jobId === currentJob?.job_id) {
      setRemoteJob(null);
      setRemoteError(null);
      setRemoteLoading(false);
      return;
    }
    let cancelled = false;
    setRemoteLoading(true);
    setRemoteError(null);
    void Promise.resolve().then(() => desktopApi().job(tab.jobId!)).then(value => {
      if (!cancelled) setRemoteJob(value);
    }).catch(error => {
      if (!cancelled) setRemoteError(error instanceof Error ? error.message : String(error));
    }).finally(() => {
      if (!cancelled) setRemoteLoading(false);
    });
    return () => { cancelled = true; };
  }, [tab.jobId, currentJob?.job_id]);

  const collection = collectPapers(job?.result ?? (tab.jobId ? null : conversation?.result) ?? null);
  const paper = tab.paperId ? collection.paperById.get(tab.paperId) : undefined;
  const summary = collection.summaries.find(item => item.paper_id === tab.paperId);
  const artifacts = job?.artifacts ?? [];
  const markdownArtifact = artifacts.find(item => item.kind === 'markdown' && item.paper_id === tab.paperId);
  const pdfArtifact = artifacts.find(item => item.kind === 'pdf' && item.paper_id === tab.paperId);
  const [view, setView] = useState<ReaderView>(summary?.markdown ? 'summary' : 'fulltext');
  const [fulltext, setFulltext] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    setView(summary?.markdown ? 'summary' : 'fulltext');
    setFulltext(null);
    setTruncated(false);
    setLocalError(null);
  }, [tab.paperId, summary?.markdown]);

  useEffect(() => {
    if (view !== 'fulltext' || !job || !markdownArtifact) return;
    let current = true;
    setLoading(true);
    setLocalError(null);
    void Promise.resolve().then(() => desktopApi().readArtifact(job.job_id, markdownArtifact.artifact_id)).then(value => {
      if (!current) return;
      setFulltext(value.text);
      setTruncated(value.truncated);
    }).catch(error => {
      if (current) setLocalError(error instanceof Error ? error.message : String(error));
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [view, job?.job_id, markdownArtifact?.artifact_id]);

  if ((loadingConversation || remoteLoading) && !paper) {
    return <div className="dock-panel-skeleton" aria-live="polite">
      <span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/>
    </div>;
  }
  if (remoteError) return <div className="panel-empty"><span><FileText size={20}/></span><strong>所属任务暂时不可读取</strong><p>{remoteError}</p></div>;
  if (!paper) return <div className="panel-empty"><span><FileText size={20}/></span><strong>该论文不在所属任务结果中</strong><p>论文标签会保留最近打开的任务上下文；可在标签栏关闭后从当前结果重新打开。</p></div>;

  const hasSummary = Boolean(summary?.markdown);
  const hasFulltext = Boolean(markdownArtifact);
  const readingText = view === 'summary'
    ? (summary?.deep_analysis?.trim() || summary?.markdown || '')
    : fulltext ?? (hasFulltext ? '' : `## 摘要\n\n${paper.abstract || '当前结果没有提供论文摘要。'}\n\n> 当前没有返回可读取的全文。`);
  const sourceUrl = safeHttpUrl(paper.paper_url);
  const selected = Boolean(collection.result?.selected_papers.some(item => item.paper_id === paper.paper_id));
  const terminal = job ? isTerminalStatus(job.status) : isTerminalResult(collection.result);
  const summaryErrors = summary?.errors ?? [];
  const paperErrors = paper.errors ?? [];
  const summaryOverview = summary
    ? (summary.overview?.trim() || summary.core_idea?.trim() || summary.relevance?.trim() || '')
    : '';
  const copy = async () => {
    if (!readingText) return;
    try {
      await desktopApi().copyText(readingText);
      workspaceStore.setNotice('论文内容已复制。');
    } catch (error) {
      workspaceStore.setError(error instanceof Error ? error.message : String(error));
    }
  };
  const openArtifact = async (artifact: Artifact) => {
    if (!job) return;
    try {
      await desktopApi().openArtifact(job.job_id, artifact.artifact_id);
    } catch (error) {
      workspaceStore.setError(error instanceof Error ? error.message : String(error));
    }
  };
  const openSource = async () => {
    if (!sourceUrl) return;
    try {
      await desktopApi().openExternal(sourceUrl);
    } catch (error) {
      workspaceStore.setError(error instanceof Error ? error.message : String(error));
    }
  };

  return <article className="reader-view">
    <header className="reader-header">
      <div className="reader-icon"><BookOpen size={17}/></div>
      <div className="reader-title">
        <span>{summary?.summary_scope
          ? `${summaryScopeLabel(summary.summary_scope)}${summaryErrors.length ? ' · 校验有错误' : ''}`
          : paperStatusLabel(paper.status)}</span>
        <h2>{paper.title}</h2>
        <p>{paper.authors.join(', ')}{paper.year ? ` · ${paper.year}` : ''}</p>
        <div className="paper-tags">
          <span>{paperStatusLabel(paper.status)}</span>
          <span>{scoreShort(paper)}</span>
          {typeof paper.rank === 'number' ? <span>{selected ? 'LLM 入选' : '候选'}名次 #{paper.rank}</span> : null}
          {paper.pdf_path ? <span>PDF 已保存</span> : null}
          {paper.markdown_path ? <span>{paper.fulltext_quality?.complete === true ? '完整 Markdown' : '部分 Markdown'}</span> : null}
        </div>
      </div>
    </header>
    <PaperProgress paper={paper} summary={summary} result={collection.result} terminal={terminal}/>
    {paper.selection_reason ? <div className="scope-note"><span>LLM 入选依据</span><strong>{paper.selection_reason}</strong></div> : null}
    {summaryOverview && <section className="summary-overview" aria-label="全文概览">
      <span>全文概览</span>
      <p>{summaryOverview}</p>
    </section>}
    <PaperRankingDetails paper={paper} selected={selected}/>
    {summaryErrors.length ? <details className="reader-diagnostics is-error">
      <summary>校验状态（{summaryErrors.length}）</summary>
      <ul>{summaryErrors.map((error, index) => <li key={`${error.code ?? 'error'}:${index}`}><strong>{errorLabel(error)}</strong><span>{errorHint(error.code)}{error.code ? ` · ${error.code}` : ''}</span></li>)}</ul>
    </details> : null}
    {hasSummary && hasFulltext && <div className="reader-switch" role="tablist" aria-label={`${paper.title} 阅读视图`}>
      <button type="button" role="tab" aria-selected={view === 'summary'} className={view === 'summary' ? 'is-active' : ''} onClick={() => setView('summary')}>单篇报告</button>
      <button type="button" role="tab" aria-selected={view === 'fulltext'} className={view === 'fulltext' ? 'is-active' : ''} onClick={() => setView('fulltext')}>全文阅读</button>
    </div>}
    <div className="reader-actions">
      <button className="subtle-button" type="button" disabled={!readingText} onClick={() => void copy()}><Copy size={14}/>复制</button>
      {hasFulltext && <button className="subtle-button" type="button" onClick={() => setView('fulltext')}><BookOpen size={14}/>阅读全文</button>}
      {pdfArtifact && <button className="subtle-button" type="button" onClick={() => void openArtifact(pdfArtifact)}><FileDown size={14}/>打开 PDF</button>}
      {sourceUrl && <button className="subtle-button" type="button" onClick={() => void openSource()}><ArrowUpRight size={14}/>原文链接</button>}
    </div>
    {view === 'fulltext' && hasFulltext && <p className="fulltext-source-note">全文由 PyMuPDF4LLM 从 PDF 提取并保存为 Markdown，可直接在应用内阅读。</p>}
    {loading ? <div className="reader-loading" aria-live="polite"><span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/><span className="skeleton-line wide"/></div> : readingText ? <MarkdownContent text={readingText}/> : null}
    {truncated && <div className="inline-warning" role="status">全文超过 2 MiB，当前仅显示服务端返回的截断内容。</div>}
    {localError && <div className="inline-error" role="alert">全文读取失败：{localError}</div>}
    {paperErrors.length ? <div className="inline-warning" role="status">{paperErrors.map((item, index) => <span key={`${item.code ?? 'error'}:${index}`}>{errorLabel(item)}{index < paperErrors.length - 1 ? '；' : ''}</span>)}</div> : paperErrorLabel(paper) ? <div className="inline-warning" role="status">{paperErrorLabel(paper)}</div> : null}
    {!summary && !markdownArtifact && <p className="reader-limitation">服务端未返回这篇论文的全文文件。此处只显示实际提供的摘要，不会将摘要标成全文。</p>}
  </article>;
}
