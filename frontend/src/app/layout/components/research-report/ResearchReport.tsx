import { useEffect, useState } from 'react';
import { BookOpen, Copy, Download, FileText, FolderOpen } from 'lucide-react';
import type { Artifact } from '../../../types/contracts';
import { desktopApi } from '../../../services/desktopApi';
import { useWorkspaceState, workspaceStore } from '../../../stores/workspaceStore';
import { statusLabel } from '../../../utils/status';
import { errorLabel } from '../../../utils/labels';
import { MarkdownContent } from '../common/MarkdownContent';

export function ResearchReport() {
  const { activeJob, resultJob, conversation } = useWorkspaceState();
  const job = resultJob ?? activeJob;
  const report = job?.result?.report ?? conversation?.result?.report ?? null;
  const artifacts = job?.artifacts ?? [];
  const reportArtifact = artifacts.find(item => item.kind === 'report' || (item.kind === 'markdown' && item.name.toLowerCase().includes('report')));
  const [markdown, setMarkdown] = useState(report?.markdown ?? '');
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportedPath, setExportedPath] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setError(null);
    if (report?.markdown) {
      setMarkdown(report.markdown);
      setLoading(false);
      return () => { current = false; };
    }
    if (!job || !reportArtifact) {
      setMarkdown('');
      setLoading(false);
      return () => { current = false; };
    }
    setLoading(true);
    void Promise.resolve().then(() => desktopApi().readArtifact(job.job_id, reportArtifact.artifact_id)).then(value => {
      if (current) {
        setMarkdown(value.text);
        setTruncated(value.truncated);
      }
    }).catch(reason => {
      if (current) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [job?.job_id, report?.markdown, reportArtifact?.artifact_id]);

  const copyReport = async () => {
    if (!markdown) return;
    try {
      await desktopApi().copyText(markdown);
      workspaceStore.setNotice('研究报告已复制。');
    } catch (reason) {
      workspaceStore.setError(reason instanceof Error ? reason.message : String(reason));
    }
  };
  const exportReport = async () => {
    if (!job || !reportArtifact) return;
    try {
      const response = await desktopApi().exportArtifact(job.job_id, reportArtifact.artifact_id);
      setExportedPath(response.cancelled ? null : response.path ?? null);
      workspaceStore.setNotice(response.cancelled ? '已取消导出。' : response.path ? `报告已导出到：${response.path}` : '报告导出完成。');
    } catch (reason) {
      workspaceStore.setError(reason instanceof Error ? reason.message : String(reason));
    }
  };
  const openReport = async () => {
    if (!job || !reportArtifact) return;
    try {
      await desktopApi().openArtifact(job.job_id, reportArtifact.artifact_id);
    } catch (reason) {
      workspaceStore.setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  if (!report && !reportArtifact) return <div className="panel-empty"><span><FileText size={21}/></span><strong>研究报告尚未生成</strong><p>完成研究任务后，服务端返回的报告会在这里预览。失败和部分结果会按原状态保留。</p></div>;

  const result = job?.result ?? conversation?.result ?? null;
  const resultErrors = result?.errors ?? [];
  return <article className="report-view">
    <header className="report-header"><div><span className="eyebrow">研究产物</span><h2>{report?.overview ? '综合研究报告' : reportArtifact?.name ?? '研究报告'}</h2><p>{job?.idea ?? conversation?.title ?? '当前会话'}</p></div><span className="report-file-mark"><FileText size={17}/></span></header>
    {result && <div className="report-status-row"><span className={`status-chip status-${result.status}`}>{statusLabel(result.status)}</span>{result.metrics?.report_valid === false ? <span className="report-warning">报告校验未通过，以下内容按部分结果保留。</span> : null}</div>}
    {report?.overview && <section className="report-preview"><h3>摘要</h3><p>{report.overview}</p></section>}
    <div className="report-actions">
      <button className="subtle-button" type="button" disabled={!markdown} onClick={() => void copyReport()}><Copy size={14}/>复制</button>
      {reportArtifact && <button className="subtle-button" type="button" onClick={() => void exportReport()}><Download size={14}/>导出</button>}
      {reportArtifact && <button className="subtle-button" type="button" onClick={() => void openReport()}><FolderOpen size={14}/>打开文件</button>}
      {reportArtifact && <span className="file-name">{reportArtifact.name}</span>}
    </div>
    {exportedPath && <p className="file-confirmation">已导出：{exportedPath}</p>}
    {loading ? <div className="reader-loading"><span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/></div> : markdown ? <MarkdownContent text={markdown}/> : <div className="panel-empty compact-empty"><span><BookOpen size={19}/></span><strong>报告正文未随结果返回</strong><p>请检查活动记录中的报告生成状态和服务端错误。</p></div>}
    {truncated && <div className="inline-warning" role="status">报告超过 2 MiB，当前仅显示服务端返回的截断内容。</div>}
    {error && <div className="inline-error" role="alert">报告读取失败：{error}</div>}
    {resultErrors.length ? <details className="result-errors"><summary>任务错误（{resultErrors.length}）</summary><ul>{resultErrors.map((item, index) => <li key={`${item.stage ?? item.code ?? 'error'}:${index}`}><strong>{errorLabel(item)}</strong>{item.message}</li>)}</ul></details> : null}
  </article>;
}

export function artifactForReport(artifacts: Artifact[]): Artifact | undefined {
  return artifacts.find(item => item.kind === 'report') ?? artifacts.find(item => item.kind === 'markdown' && item.name.toLowerCase().includes('report'));
}
