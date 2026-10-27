import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, BookOpen, Copy, Download, FileText, FolderOpen } from 'lucide-react';
import type { Artifact, ContractError, PipelineResult } from '../../../types/contracts';
import { desktopApi } from '../../../services/desktopApi';
import { useWorkspaceState, workspaceStore } from '../../../stores/workspaceStore';
import { statusLabel } from '../../../utils/status';
import { errorHint, errorLabel, summaryScopeLabel } from '../../../utils/labels';
import { readableReportMarkdown } from '../../../utils/reportMarkdown';
import { MarkdownContent } from '../common/MarkdownContent';
import { TargetCoverageSummary } from '../common/ResultInsights';

export function ResearchReport() {
  const { activeJob, resultJob, conversation } = useWorkspaceState();
  const job = resultJob ?? activeJob;
  const report = job?.result?.report ?? conversation?.result?.report ?? null;
  const result = job?.result ?? conversation?.result ?? null;
  const artifacts = job?.artifacts ?? [];
  const reportArtifact = artifacts.find(item => item.kind === 'report' || (item.kind === 'markdown' && item.name.toLowerCase().includes('report')));
  const [markdown, setMarkdown] = useState(report?.markdown ?? '');
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportedPath, setExportedPath] = useState<string | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const displayMarkdown = useMemo(
    () => readableReportMarkdown(markdown, report?.comparison),
    [markdown, report?.comparison],
  );

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
    if (!displayMarkdown) return;
    try {
      await desktopApi().copyText(displayMarkdown);
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
  const scrollToHeading = (title: string) => {
    const root = contentRef.current;
    if (!root) return;
    const nodes = Array.from(root.querySelectorAll('h2, h3'));
    const target = nodes.find(node => node.textContent?.trim() === title)
      ?? nodes.find(node => node.textContent?.includes(title));
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  const scrollToTop = () => {
    const container = contentRef.current?.closest('.dock-panel-content') as HTMLElement | null;
    container?.scrollTo({ top: 0, behavior: 'smooth' });
  };

  if (!report && !reportArtifact) return <div className="panel-empty"><span><FileText size={21}/></span><strong>研究报告尚未生成</strong><p>完成研究任务后，服务端返回的报告会在这里预览。失败和部分结果会按原状态保留。</p></div>;

  const reportErrors = collectReportErrors(result, report);
  const state = reportState(result, report, reportErrors);
  const validity = reportValidity(result);
  const headings = markdownHeadings(displayMarkdown);
  const references = report?.references ?? [];
  const reportLimitations = (report?.limitations ?? []).filter(item =>
    !item.startsWith('部分报告：') && !item.startsWith('报告提示使用了') &&
    !item.startsWith('报告输入超过') && !item.startsWith('完整总结集'));
  const scopeCounts = new Map<string, number>();
  for (const summary of result?.summaries ?? []) {
    scopeCounts.set(summary.summary_scope, (scopeCounts.get(summary.summary_scope) ?? 0) + 1);
  }
  return <article className="report-view">
    <header className="report-header"><div><span className="eyebrow">研究产物</span><h2>{state.title}</h2><p>{job?.idea ?? conversation?.title ?? '当前会话'}</p></div><span className="report-file-mark"><FileText size={17}/></span></header>
    <div className="report-status-row"><span className={`status-chip status-${state.tone}`}>{state.label}</span>{result && <span className={`status-chip status-${result.status}`}>{statusLabel(result.status)}</span>}{validity && <span className={`status-chip status-${validity.tone}`}>{validity.label}</span>}{references.length > 0 && <span className="report-meta">引用 {references.length} 篇</span>}</div>
    <p className={`report-state-note ${state.tone === 'failed' ? 'is-error' : ''}`}>{state.note}</p>
    {scopeCounts.size > 0 && <div className="report-coverage">
      {[...scopeCounts.entries()].map(([scope, count]) => <span key={scope}>{summaryScopeLabel(scope)} <strong>{count}</strong></span>)}
    </div>}
    {result && <TargetCoverageSummary result={result} compact/>}
    <div className="report-actions">
      <button className="subtle-button" type="button" disabled={!displayMarkdown} onClick={() => void copyReport()}><Copy size={14}/>复制</button>
      {reportArtifact && <button className="subtle-button" type="button" onClick={() => void exportReport()}><Download size={14}/>导出</button>}
      {reportArtifact && <button className="subtle-button" type="button" onClick={() => void openReport()}><FolderOpen size={14}/>打开文件</button>}
      {displayMarkdown.length > 6000 && <button className="subtle-button" type="button" onClick={scrollToTop}><ArrowUp size={14}/>回到顶部</button>}
      {reportArtifact && <span className="file-name">{reportArtifact.name}</span>}
    </div>
    {exportedPath && <p className="file-confirmation">已导出：{exportedPath}</p>}
    {headings.length > 1 && <details className="report-outline">
      <summary>报告目录（{headings.length}）</summary>
      <ol>{headings.map((heading, index) => <li key={`${index}:${heading.title}`} className={`outline-level-${heading.level}`}>
        <button type="button" onClick={() => scrollToHeading(heading.title)}>{heading.title}</button>
      </li>)}</ol>
    </details>}
    {reportErrors.length > 0 && <details className="report-errors" open={state.tone === 'failed' || undefined}>
      <summary>报告错误与校验信息（{reportErrors.length}）</summary>
      <ul>{reportErrors.map((item, index) => <li key={`${item.stage ?? item.code ?? 'error'}:${index}`}>
        <strong>{errorLabel(item)}</strong>
        <span>{errorHint(item.code)}{item.code ? ` · ${item.code}` : ''}{item.stage ? ` · ${item.stage}` : ''}</span>
      </li>)}</ul>
    </details>}
    {loading ? <div className="reader-loading" aria-live="polite"><span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/></div>
      : displayMarkdown ? <div ref={contentRef} className="report-markdown"><MarkdownContent text={displayMarkdown}/></div>
        : report?.overview ? <div className="report-preview"><h3>报告概述</h3><p>{report.overview}</p>{reportLimitations.length ? <ul>{reportLimitations.map(limitation => <li key={limitation}>{limitation}</li>)}</ul> : null}</div>
          : <div className="panel-empty compact-empty"><span><BookOpen size={19}/></span><strong>报告正文未随结果返回</strong><p>请检查活动记录中的报告生成状态和服务端错误。</p></div>}
    {truncated && <div className="inline-warning" role="status">报告超过 2 MiB，当前仅显示服务端返回的截断内容。</div>}
    {error && <div className="inline-error" role="alert">报告读取失败：{error}</div>}
  </article>;
}

function collectReportErrors(result: PipelineResult | null, report: PipelineResult['report']): ContractError[] {
  const values = [...(report?.errors ?? []), ...(result?.errors ?? []).filter(item => item.stage === 'report')];
  const seen = new Set<string>();
  const output: ContractError[] = [];
  for (const item of values) {
    const key = `${item.code ?? ''}:${item.stage ?? ''}:${item.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    output.push(item);
  }
  return output;
}

function reportValidity(result: PipelineResult | null): { label: string; tone: string } | null {
  const value = result?.metrics?.report_valid;
  if (typeof value !== 'boolean') return null;
  return value
    ? { label: '报告有效', tone: 'completed' }
    : { label: '报告未通过校验', tone: 'failed' };
}

function reportState(result: PipelineResult | null, report: PipelineResult['report'], errors: ContractError[]) {
  const valid = result?.metrics?.report_valid;
  const invalid = errors.some(item => ['invalid_model_output', 'llm_budget', 'llm_failed', 'persist_failed'].includes(item.code ?? ''));
  const partial = errors.some(item => item.code === 'partial_report') || result?.status === 'partial';
  const inputCompacted = errors.some(item => item.code === 'partial_report' && item.details?.reason === 'report_input_compacted')
    || (report?.limitations ?? []).some(item => item.includes('有界摘录'));
  if (invalid) {
    return {
      label: '校验未通过', tone: 'failed', title: '部分研究报告',
      note: '报告生成或严格校验未通过；以下内容按部分结果保留，缺失引用不会被补写为完整报告。',
    };
  }
  if (inputCompacted && valid !== true) {
    return {
      label: '部分报告', tone: 'partial', title: '综合研究报告',
      note: '报告提示使用了每篇总结的有界摘录，未包含完整字段；完整内容仍保存在单篇总结中，本次不按完整输入报告处理。',
    };
  }
  if (partial) {
    return valid === true
      ? { label: '部分结果报告', tone: 'partial', title: '综合研究报告', note: '报告内容通过引用与结构校验，但输入总结仍不完整；图表和实验以单篇总结为准。' }
      : { label: '部分报告', tone: 'partial', title: '部分研究报告', note: '报告只覆盖实际可用的单篇总结；输入不完整，未按完整报告处理。' };
  }
  if (valid === true) {
    return { label: '完整报告', tone: 'completed', title: '综合研究报告', note: '报告已通过引用与结构校验，范围以实际成功总结的论文为准。' };
  }
  if (valid === false) {
    return {
      label: '未通过校验', tone: 'failed', title: '部分研究报告',
      note: '报告未通过严格引用或结构校验，按部分结果保留；不会把未核验内容显示为完整报告。',
    };
  }
  return {
    label: partial ? '部分结果报告' : '状态未记录', tone: partial ? 'partial' : 'pending',
    title: '研究报告', note: '这是旧记录或缺少 report_valid 指标；请以报告错误和单篇总结范围为准。',
  };
}

function markdownHeadings(markdown: string): Array<{ level: number; title: string }> {
  const output: Array<{ level: number; title: string }> = [];
  for (const match of markdown.matchAll(/^(#{2,3})\s+(.+)$/gm)) {
    const title = match[2].replace(/[*_`]/g, '').trim();
    if (!title) continue;
    output.push({ level: match[1].length, title });
    if (output.length >= 16) break;
  }
  return output;
}

export function artifactForReport(artifacts: Artifact[]): Artifact | undefined {
  return artifacts.find(item => item.kind === 'report') ?? artifacts.find(item => item.kind === 'markdown' && item.name.toLowerCase().includes('report'));
}
