import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, BookOpen, Copy, Download, FileText, FolderOpen } from 'lucide-react';
import type { Artifact, ContractError, PipelineResult } from '../../../types/contracts';
import { desktopApi } from '../../../services/desktopApi';
import { useWorkspaceState, workspaceStore } from '../../../stores/workspaceStore';
import { errorLabel } from '../../../utils/labels';
import { readableReportMarkdown } from '../../../utils/reportMarkdown';
import { visibleReportDocument, type LoadedReportDocument } from '../../../utils/resultPresentation';
import { deliveryStatusFor, deliveryStatusLabels, reportArtifactFor } from '../../../utils/researchBrief';
import { MarkdownContent } from '../common/MarkdownContent';

export function ResearchReport() {
  const { activeJob, resultJob, conversation } = useWorkspaceState();
  const job = resultJob ?? activeJob;
  const report = job?.result?.report ?? conversation?.result?.report ?? null;
  const result = job?.result ?? conversation?.result ?? null;
  const artifacts = job?.artifacts ?? [];
  const reportArtifact = reportArtifactFor(result, artifacts);
  // Keep artifact text attached to the job/artifact that supplied it. A new
  // result must never briefly display or copy an earlier report while loading.
  const reportKey = job && reportArtifact ? `${job.job_id}:${reportArtifact.artifact_id}` : null;
  const [artifactDocument, setArtifactDocument] = useState<LoadedReportDocument | null>(null);
  const [exported, setExported] = useState<{ key: string; path: string } | null>(null);
  const inlineMarkdown = report?.markdown?.trim() ? report.markdown : '';
  const { markdown, truncated, loading, error } = visibleReportDocument(inlineMarkdown, reportKey, artifactDocument);
  const exportedPath = reportKey && exported?.key === reportKey ? exported.path : null;
  const contentRef = useRef<HTMLDivElement | null>(null);
  const displayMarkdown = useMemo(
    () => readableReportMarkdown(markdown, report?.comparison),
    [markdown, report?.comparison],
  );
  const hasSubstance = reportHasSubstance(report, displayMarkdown);

  useEffect(() => {
    if (!job || !reportArtifact || !reportKey || inlineMarkdown) return;
    let current = true;
    setArtifactDocument({ key: reportKey, text: '', truncated: false, error: null, loading: true });
    void Promise.resolve().then(() => desktopApi().readArtifact(job.job_id, reportArtifact.artifact_id)).then(value => {
      if (current) setArtifactDocument({ key: reportKey, text: value.text, truncated: value.truncated, error: null, loading: false });
    }).catch(reason => {
      if (current) setArtifactDocument({ key: reportKey, text: '', truncated: false, error: reason instanceof Error ? reason.message : String(reason), loading: false });
    });
    return () => { current = false; };
  }, [reportKey, inlineMarkdown]);

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
      if (reportKey) setExported(response.cancelled || !response.path ? null : { key: reportKey, path: response.path });
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
  const state = loading
    ? { label: '读取中', tone: 'pending', title: '正在读取研究报告', note: '正在读取当前任务的报告文件。' }
    : reportState(result, report, reportErrors, hasSubstance);
  const headings = markdownHeadings(displayMarkdown);
  const references = report?.references ?? [];
  const reportLimitations = (report?.limitations ?? []).filter(item =>
    !item.startsWith('部分报告：') && !item.startsWith('报告提示使用了') &&
    !item.startsWith('报告输入超过') && !item.startsWith('完整总结集'));
  const delivery = deliveryStatusFor(result, job);
  return <article className="report-view">
    <header className="report-header"><div><span className="eyebrow">研究产物</span><h2>{state.title}</h2><p>{job?.idea ?? conversation?.title ?? '当前会话'}</p></div><span className="report-file-mark"><FileText size={17}/></span></header>
    <div className="report-status-row">{delivery && <span className={`delivery-badge delivery-${delivery}`}>{deliveryStatusLabels[delivery]}</span>}{references.length > 0 && <span className="report-meta">引用 {references.length} 篇</span>}</div>
    <p className={`report-state-note ${state.tone === 'failed' ? 'is-error' : ''}`}>{state.note}</p>
    <div className="report-actions">
      <button className="subtle-button" type="button" disabled={!displayMarkdown || !hasSubstance} onClick={() => void copyReport()}><Copy size={14}/>复制</button>
      {reportArtifact && <button className="subtle-button" type="button" onClick={() => void exportReport()}><Download size={14}/>导出</button>}
      {reportArtifact && <button className="subtle-button" type="button" onClick={() => void openReport()}><FolderOpen size={14}/>打开文件</button>}
      {displayMarkdown.length > 6000 && <button className="subtle-button" type="button" onClick={scrollToTop}><ArrowUp size={14}/>回到顶部</button>}
      {reportArtifact && <span className="file-name">{reportArtifact.name}</span>}
    </div>
    {exportedPath && <p className="file-confirmation">已导出：{exportedPath}</p>}
    {headings.length > 1 && hasSubstance && <details className="report-outline">
      <summary>报告目录（{headings.length}）</summary>
      <ol>{headings.map((heading, index) => <li key={`${index}:${heading.title}`} className={`outline-level-${heading.level}`}>
        <button type="button" onClick={() => scrollToHeading(heading.title)}>{heading.title}</button>
      </li>)}</ol>
    </details>}
    {loading ? <div className="reader-loading" aria-live="polite"><span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/></div>
      : !hasSubstance ? <section className="report-unavailable" role="status">
        <strong>暂无可读的综合正文</strong>
        <p>{reportFailureNote(reportErrors, state.note)}</p>
        <small>原始报告、错误记录、已有引用和真实任务产物仍保留；不会用整套空章节占位。</small>
      </section>
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

function reportState(
  result: PipelineResult | null, report: PipelineResult['report'], errors: ContractError[],
  hasSubstance = true,
) {
  const valid = result?.metrics?.report_valid;
  const invalid = errors.some(item => ['invalid_model_output', 'llm_budget', 'llm_failed', 'persist_failed'].includes(item.code ?? ''));
  const partial = errors.some(item => item.code === 'partial_report') || result?.status === 'partial';
  const inputCompacted = errors.some(item => item.code === 'partial_report' && item.details?.reason === 'report_input_compacted')
    || (report?.limitations ?? []).some(item => item.includes('有界摘录'));
  if (!hasSubstance) {
    return {
      label: '无可读报告', tone: 'failed', title: '研究报告未生成',
      note: '报告生成失败或未通过校验，没有可读的综合正文；已有错误、引用和真实产物仍保留。',
    };
  }
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

function reportHasSubstance(
  report: PipelineResult['report'], markdown: string,
): boolean {
  if (report) {
    const hasFields = Boolean(
      report.overview?.trim() || report.related_work?.length || report.comparison?.length
      || report.researched_questions?.length || report.research_directions?.length,
    );
    if (hasFields) return true;
  }
  return markdownHasSubstance(markdown);
}

function markdownHasSubstance(markdown: string): boolean {
  if (!markdown.trim()) return false;
  let insideReferences = false;
  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const heading = /^#{1,6}\s+(.+)$/.exec(line);
    if (heading) {
      const title = heading[1];
      insideReferences = title.includes('参考文献') || title.includes('保留的论文引用');
      continue;
    }
    if (insideReferences) continue;
    if (line.startsWith('研究想法：') || line.startsWith('>')) continue;
    if (['未生成。', '未明确报告。', '未在所读材料中明确报告。'].includes(line)) continue;
    if (line.startsWith('未生成可读的综合正文') || line.startsWith('原因：')) continue;
    return true;
  }
  return false;
}

function reportFailureNote(errors: ContractError[], fallback: string): string {
  const primary = errors.find(item =>
    ['invalid_model_output', 'llm_budget', 'llm_failed', 'persist_failed', 'no_summaries'].includes(item.code ?? ''),
  ) ?? errors[0];
  if (!primary) return fallback || '报告生成失败或没有可读正文；请查看错误与真实产物。';
  return errorLabel(primary);
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
  return reportArtifactFor(undefined, artifacts);
}
