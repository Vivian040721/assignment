import { useEffect, useState } from 'react';
import { ArrowUpRight, BookOpen, Copy, Download, FileText, FolderOpen, MoreHorizontal } from 'lucide-react';
import type { Artifact, EvidenceReference, Job } from '../../../types/contracts';
import type { LayoutTab } from '../../../types/ui';
import { desktopApi } from '../../../services/desktopApi';
import { useWorkspaceState, workspaceStore } from '../../../stores/workspaceStore';
import { collectPapers, summaryScopeLabel, summaryStatus, summaryStatusLabel } from '../../../utils/papers';
import { safeHttpUrl } from '../../../utils/format';
import { canLocatePdfEvidence } from '../../../utils/pdf';
import { MarkdownContent } from '../common/MarkdownContent';
import { PdfViewer } from './PdfViewer';

type ReaderView = 'summary' | 'pdf';

export function PaperReader({ tab }: { tab: LayoutTab }) {
  const { activeJob, resultJob, conversation, loadingConversation } = useWorkspaceState();
  const currentJob = resultJob ?? activeJob;
  const [remoteJob, setRemoteJob] = useState<Job | null>(null);
  const [remoteLoading, setRemoteLoading] = useState(false);
  const [remoteError, setRemoteError] = useState<{ jobId: string; message: string } | null>(null);
  const remoteTargetId = tab.jobId && tab.jobId !== currentJob?.job_id ? tab.jobId : null;
  const job = remoteTargetId
    ? (remoteJob?.job_id === remoteTargetId ? remoteJob : null)
    : currentJob;

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
      if (!cancelled) setRemoteError({ jobId: tab.jobId!, message: error instanceof Error ? error.message : String(error) });
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
  const registeredPdf = artifacts.find(item => item.kind === 'pdf' && item.paper_id === tab.paperId);
  const fallbackPdf = paper?.pdf_artifact_id
    ? { artifact_id: paper.pdf_artifact_id, name: `${paper.title}.pdf`, kind: 'pdf', paper_id: paper.paper_id } satisfies Artifact
    : undefined;
  const defaultPdfArtifact = registeredPdf ?? fallbackPdf;
  const [view, setView] = useState<ReaderView>(summary?.markdown ? 'summary' : defaultPdfArtifact ? 'pdf' : 'summary');
  const [selectedPdfId, setSelectedPdfId] = useState<string | null>(null);
  const [requestedPage, setRequestedPage] = useState(1);
  const [jumpRevision, setJumpRevision] = useState(0);
  const [showFulltext, setShowFulltext] = useState(false);
  const [fulltext, setFulltext] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    setView(summary?.markdown ? 'summary' : defaultPdfArtifact ? 'pdf' : 'summary');
    setSelectedPdfId(null);
    setRequestedPage(1);
    setJumpRevision(0);
    setShowFulltext(false);
    setFulltext(null);
    setTruncated(false);
    setLocalError(null);
  }, [tab.paperId, tab.jobId]);

  useEffect(() => {
    if (!showFulltext || !job || !markdownArtifact) return;
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
  }, [showFulltext, job?.job_id, markdownArtifact?.artifact_id]);

  if ((loadingConversation || remoteLoading) && !paper) {
    return <div className="dock-panel-skeleton" aria-live="polite">
      <span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/>
    </div>;
  }
  if (remoteError?.jobId === remoteTargetId) return <div className="panel-empty"><span><FileText size={20}/></span><strong>所属任务暂时不可读取</strong><p>{remoteError.message}</p></div>;
  if (!paper) return <div className="panel-empty"><span><FileText size={20}/></span><strong>该论文不在所属任务结果中</strong><p>论文标签会保留最近打开的任务上下文；可在标签栏关闭后从当前结果重新打开。</p></div>;

  const selectedPdfArtifact = selectedPdfId
    ? artifacts.find(item => item.artifact_id === selectedPdfId && item.kind === 'pdf')
      ?? { artifact_id: selectedPdfId, name: `${paper.title}.pdf`, kind: 'pdf', paper_id: paper.paper_id }
    : defaultPdfArtifact;
  const readingText = view === 'summary'
    ? (summary?.deep_analysis?.trim() || summary?.markdown || (paper.abstract ? `## 摘要\n\n${paper.abstract}` : ''))
    : '';
  const sourceUrl = safeHttpUrl(paper.paper_url);
  const derivedSummaryStatus = summaryStatus(summary);
  const analysisIds = collection.result?.metrics?.analysis_paper_ids;
  const analyzed = Array.isArray(analysisIds) && analysisIds.includes(paper.paper_id);
  const summaryStatusText = summary
    ? `${summaryStatusLabel(derivedSummaryStatus)} · ${summaryScopeLabel(summary.summary_scope)}`
    : analyzed ? '总结缺失' : '未进入总结';
  const summaryOverview = summary
    ? (summary.overview?.trim() || summary.core_idea?.trim() || summary.relevance?.trim() || '')
    : '';
  const evidence = summary?.evidence ?? [];
  const hasProcessingLimitations = Boolean(summary?.errors?.length || paper.errors?.length || paper.error);

  const copy = async () => {
    if (!readingText) return;
    try {
      await desktopApi().copyText(readingText);
      workspaceStore.setNotice('研究笔记已复制。');
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
  const exportArtifact = async (artifact: Artifact) => {
    if (!job) return;
    try {
      const response = await desktopApi().exportArtifact(job.job_id, artifact.artifact_id);
      workspaceStore.setNotice(response.cancelled ? '已取消下载。' : response.path ? `已下载到：${response.path}` : '下载完成。');
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
  const locateEvidence = (item: EvidenceReference) => {
    if (!canLocatePdfEvidence(item) || item.paper_id !== paper.paper_id) return;
    setSelectedPdfId(item.pdf_artifact_id!);
    setRequestedPage(item.page_number!);
    setJumpRevision(value => value + 1);
    setView('pdf');
  };

  return <article className="reader-view">
    <header className="reader-header">
      <div className="reader-icon"><BookOpen size={17}/></div>
      <div className="reader-title">
        <span>{summaryStatusText}</span>
        <h2 title={paper.title}>{paper.title}</h2>
        <p>{paper.authors.join(', ')}{paper.year ? ` · ${paper.year}` : ''}</p>
      </div>
      <details className="reader-more">
        <summary className="icon-button" aria-label="更多论文操作" title="更多操作"><MoreHorizontal size={17}/></summary>
        <div className="reader-more-menu">
          {defaultPdfArtifact && <button type="button" onClick={() => void openArtifact(defaultPdfArtifact)}><FolderOpen size={14}/>在外部打开 PDF</button>}
          {defaultPdfArtifact && <button type="button" onClick={() => void exportArtifact(defaultPdfArtifact)}><Download size={14}/>下载 PDF</button>}
          {sourceUrl && <button type="button" onClick={() => void openSource()}><ArrowUpRight size={14}/>打开论文来源页面</button>}
        </div>
      </details>
    </header>

    <div className="reader-switch" role="tablist" aria-label={`${paper.title} 阅读视图`}>
      <button type="button" role="tab" aria-selected={view === 'summary'} className={view === 'summary' ? 'is-active' : ''} onClick={() => setView('summary')}>研究笔记</button>
      <button type="button" role="tab" aria-selected={view === 'pdf'} className={view === 'pdf' ? 'is-active' : ''} disabled={!selectedPdfArtifact} onClick={() => setView('pdf')}>PDF 原文</button>
    </div>
    {!selectedPdfArtifact && <p className="reader-limitation">本次结果没有可定位的 PDF artifact；不会把 PDF 候选链接或 Markdown 当作已获取原文。</p>}

    {view === 'summary' ? <>
      {summaryOverview && <section className="summary-overview" aria-label="研究笔记概览"><span>研究笔记概览</span><p>{summaryOverview}</p></section>}
      {paper.selection_reason ? <div className="scope-note"><span>精选依据</span><strong>{paper.selection_reason}</strong></div> : null}
      {(derivedSummaryStatus === 'failed' || derivedSummaryStatus === 'missing') && analyzed && <div className="inline-warning" role="status">
        {derivedSummaryStatus === 'failed'
          ? '这篇论文的研究笔记未完成，现有内容不会按完整分析交付。'
          : '这篇论文进入了分析，但没有返回研究笔记；不会默认显示为部分成功。'}
      </div>}
      {hasProcessingLimitations && <div className="inline-warning" role="status">这篇论文的获取、阅读或校验存在缺口，关键结论请结合原文核对；原始诊断位于执行记录。</div>}
      <div className="reader-actions"><button className="subtle-button" type="button" disabled={!readingText} onClick={() => void copy()}><Copy size={14}/>复制研究笔记</button></div>
      {readingText ? <MarkdownContent text={readingText}/> : <div className="panel-empty compact-empty"><span><FileText size={19}/></span><strong>没有可读研究笔记</strong><p>当前结果未提供这篇论文的摘要或研究笔记。</p></div>}
      {evidence.length > 0 && <section className="evidence-list" aria-label="研究笔记证据与页码">
        <h3>证据与原文定位</h3>
        <ol>{evidence.map((item, index) => {
          const locatable = item.paper_id === paper.paper_id && canLocatePdfEvidence(item);
          return <li key={`${item.paper_id}:${item.page_number ?? 'none'}:${index}`}>
            {(item.quote || item.claim) && <p>{item.quote || item.claim}</p>}
            <div>{item.section && <span>{item.section}</span>}{typeof item.page_number === 'number' && (locatable
              ? <button type="button" onClick={() => locateEvidence(item)}>跳到 PDF 第 {item.page_number} 页</button>
              : <span className="evidence-unlocatable">第 {item.page_number} 页 · 不可定位</span>)}{typeof item.page_number !== 'number' && <span className="evidence-unlocatable">未保存 PDF 页码 · 不可定位</span>}</div>
          </li>;
        })}</ol>
      </section>}
    </> : job && selectedPdfArtifact ? <PdfViewer
      key={`${selectedPdfArtifact.artifact_id}:${jumpRevision}`}
      jobId={job.job_id}
      artifact={selectedPdfArtifact}
      initialPage={requestedPage}
      onPageChange={setRequestedPage}
    /> : <div className="inline-warning">所属任务或 PDF artifact 不可用，无法读取原文。</div>}

    {markdownArtifact && <details className="technical-details reader-technical-details" onToggle={event => { if (event.currentTarget.open) setShowFulltext(true); }}>
      <summary>查看提取的 Markdown</summary>
      <p>该文本由服务端从 PDF 提取，只用于核对；阅读原文请使用上方 PDF。</p>
      {loading && <div className="reader-loading" aria-live="polite"><span className="skeleton-line wide"/><span className="skeleton-line"/></div>}
      {fulltext && <MarkdownContent text={fulltext}/>}
      {truncated && <div className="inline-warning" role="status">全文超过 2 MiB，当前仅显示服务端返回的截断内容。</div>}
      {localError && <div className="inline-error" role="alert">Markdown 读取失败：{localError}</div>}
    </details>}
    {!summary && !markdownArtifact && !selectedPdfArtifact && <p className="reader-limitation">服务端未返回这篇论文的全文文件。此处只显示实际提供的摘要，不会将摘要标成全文。</p>}
  </article>;
}
