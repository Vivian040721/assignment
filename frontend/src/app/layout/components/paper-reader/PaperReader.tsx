import { useEffect, useState } from 'react';
import { ArrowUpRight, BookOpen, Copy, FileDown, FileText } from 'lucide-react';
import type { Artifact, Job } from '../../../types/contracts';
import type { LayoutTab } from '../../../types/ui';
import { desktopApi } from '../../../services/desktopApi';
import { useWorkspaceState, workspaceStore } from '../../../stores/workspaceStore';
import { collectPapers, paperStatusLabel, summaryScopeLabel } from '../../../utils/papers';
import { safeHttpUrl } from '../../../utils/format';
import { errorLabel, paperErrorLabel } from '../../../utils/labels';
import { MarkdownContent } from '../common/MarkdownContent';

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
    ? summary?.markdown || ''
    : fulltext ?? (hasFulltext ? '' : `## 摘要\n\n${paper.abstract || '当前结果没有提供论文摘要。'}\n\n> 当前没有返回可读取的全文。`);
  const sourceUrl = safeHttpUrl(paper.paper_url);
  const score = paper.final_score ?? paper.initial_score;
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
        <span>{summary?.summary_scope ? summaryScopeLabel(summary.summary_scope) : paperStatusLabel(paper.status)}</span>
        <h2>{paper.title}</h2>
        <p>{paper.authors.join(', ')}{paper.year ? ` · ${paper.year}` : ''}</p>
        <div className="paper-tags">
          <span>{paperStatusLabel(paper.status)}</span>
          {typeof score === 'number' && Number.isFinite(score) ? <span>相关度 {Math.round(score * 100)}%</span> : null}
          {typeof paper.rank === 'number' ? <span>排名 #{paper.rank}</span> : null}
          {paper.pdf_path ? <span>PDF 已缓存</span> : null}
          {paper.markdown_path ? <span>Markdown 就绪</span> : null}
        </div>
      </div>
    </header>
    {paper.selection_reason ? <div className="scope-note"><span>入选依据</span><strong>{paper.selection_reason}</strong></div> : null}
    {summary && <div className="scope-note"><span>总结范围</span><strong>{summaryScopeLabel(summary.summary_scope)}</strong>{summary.warnings?.length ? <span>{summary.warnings.join(' · ')}</span> : null}</div>}
    {hasSummary && hasFulltext && <div className="reader-switch" role="tablist" aria-label={`${paper.title} 阅读视图`}>
      <button type="button" role="tab" aria-selected={view === 'summary'} className={view === 'summary' ? 'is-active' : ''} onClick={() => setView('summary')}>单篇总结</button>
      <button type="button" role="tab" aria-selected={view === 'fulltext'} className={view === 'fulltext' ? 'is-active' : ''} onClick={() => setView('fulltext')}>全文</button>
    </div>}
    <div className="reader-actions">
      <button className="subtle-button" type="button" disabled={!readingText} onClick={() => void copy()}><Copy size={14}/>复制</button>
      {pdfArtifact && <button className="subtle-button" type="button" onClick={() => void openArtifact(pdfArtifact)}><FileDown size={14}/>打开 PDF</button>}
      {sourceUrl && <button className="subtle-button" type="button" onClick={() => void openSource()}><ArrowUpRight size={14}/>原文链接</button>}
    </div>
    {view === 'summary' && summary && <SummaryFacts summary={summary}/>}
    {loading ? <div className="reader-loading" aria-live="polite"><span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/><span className="skeleton-line wide"/></div> : readingText ? <MarkdownContent text={readingText}/> : null}
    {truncated && <div className="inline-warning" role="status">全文超过 2 MiB，当前仅显示服务端返回的截断内容。</div>}
    {localError && <div className="inline-error" role="alert">全文读取失败：{localError}</div>}
    {paper.errors?.length ? <div className="inline-warning" role="status">{paper.errors.map((item, index) => <span key={`${item.code ?? 'error'}:${index}`}>{errorLabel(item)}{index < (paper.errors?.length ?? 0) - 1 ? '；' : ''}</span>)}</div> : paperErrorLabel(paper) ? <div className="inline-warning" role="status">{paperErrorLabel(paper)}</div> : null}
    {!summary && !markdownArtifact && <p className="reader-limitation">服务端未返回这篇论文的全文文件。此处只显示实际提供的摘要，不会将摘要标成全文。</p>}
  </article>;
}

function SummaryFacts({ summary }: { summary: NonNullable<ReturnType<typeof collectPapers>['summaries'][number]> }) {
  const sections = [
    ['核心思想', summary.core_idea],
    ['方法', summary.methods?.join('；')],
    ['实验', summary.experiments?.join('；')],
    ['主要发现', summary.findings?.join('；')],
    ['与问题的相关性', summary.relevance],
    ['局限', summary.limitations?.join('；')],
  ].filter(([, value]) => Boolean(value));
  if (!sections.length) return null;
  return <dl className="summary-facts">{sections.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
