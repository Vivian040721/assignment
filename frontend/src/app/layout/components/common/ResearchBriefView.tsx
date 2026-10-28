import { ArrowRight, BookOpen, FileText, Lightbulb } from 'lucide-react';
import type { EvidenceLevel, Job, KeyPaper, Paper, PipelineResult } from '../../../types/contracts';
import { layoutStore } from '../../stores/layoutStore';
import {
  deliveryStatusFor,
  deliveryStatusLabels,
  deliveryStatusNotes,
  groupBriefLimitations,
  primaryBriefLimitations,
  reportArtifactFor,
  supportedFindings,
} from '../../../utils/researchBrief';

const evidenceLevelLabels: Record<EvidenceLevel, string> = {
  metadata_only: '仅元数据',
  abstract_only: '摘要证据',
  partial_fulltext: '部分全文',
  fulltext: '全文证据',
};

export function ResearchBriefView({ result, job }: { result: PipelineResult; job?: Job | null }) {
  const brief = result.brief;
  const delivery = deliveryStatusFor(result, job);
  if (!brief && !delivery) return null;
  const findings = supportedFindings(brief);
  const papers = new Map<string, Paper>();
  for (const paper of [...result.selected_papers, ...result.papers]) papers.set(paper.paper_id, paper);
  const keyPapers = (brief?.key_papers ?? [])
    .map(item => ({ item, paper: papers.get(item.paper_id) }))
    .filter((value): value is { item: KeyPaper; paper: Paper } => Boolean(value.paper));
  const reportArtifact = reportArtifactFor(result, job?.artifacts ?? []);
  const hasLegacyReport = Boolean(result.report?.markdown?.trim());
  const stateLabel = delivery ? deliveryStatusLabels[delivery] : '旧记录 · 交付状态未保存';
  const stateNote = delivery
    ? deliveryStatusNotes[delivery]
    : '这条旧记录没有保存交付分类；请结合实际总结范围和限制阅读。';
  const limitationGroups = groupBriefLimitations(brief?.limitations ?? []);
  const primaryLimitations = primaryBriefLimitations(limitationGroups);
  const allReadableLimitations = [...limitationGroups.material, ...limitationGroups.research];
  const remainingLimitations = allReadableLimitations.filter(item => !primaryLimitations.includes(item));
  const hasLimitations = primaryLimitations.length > 0;

  return <section className={`research-brief delivery-${delivery ?? 'legacy'}`} aria-label="研究简报">
    <header className="brief-header">
      <div><span className="eyebrow">研究简报</span><h2>{brief ? '关键发现与证据' : '研究交付状态'}</h2></div>
      <span className={`delivery-badge delivery-${delivery ?? 'legacy'}`}>{stateLabel}</span>
    </header>
    <p className="brief-state-note">{stateNote}</p>

    {findings.length > 0 && <ol className="brief-findings">
      {findings.map((finding, index) => {
        const evidence = finding.evidence.map(item => {
          const title = papers.get(item.paper_id)?.title ?? item.paper_id;
          return `${title}${typeof item.page_number === 'number' ? ` · 第 ${item.page_number} 页` : ''}`;
        });
        return <li key={`${index}:${finding.statement}`}>
          <span className="finding-number">{index + 1}</span>
          <div><p>{finding.statement}</p><small>证据：{[...new Set(evidence)].join('；')}</small></div>
        </li>;
      })}
    </ol>}

    {brief && findings.length === 0 && <p className="brief-empty">当前没有可展示的有证据结论，不以占位内容补足三条。</p>}

    {keyPapers.length > 0 && <section className="brief-key-papers" aria-label="关键论文">
      <h3><BookOpen size={14}/>关键论文 <span>{keyPapers.length}</span></h3>
      <div>{keyPapers.map(({ item, paper }) => <button type="button" key={item.paper_id} onClick={() => layoutStore.openPaper(paper.paper_id, paper.title, job?.job_id)}>
        <strong>{paper.title}</strong>
        <span>{item.evidence_level ? evidenceLevelLabels[item.evidence_level] : '证据范围未记录'}</span>
        {item.selection_reason && <p>{item.selection_reason}</p>}
        <ArrowRight size={14}/>
      </button>)}</div>
    </section>}

    {brief?.next_step?.action && <section className="brief-next-step" aria-label="推荐下一步">
      <span><Lightbulb size={16}/></span>
      <div><h3>推荐下一步</h3><p>{brief.next_step.action}</p>{brief.next_step.rationale && <small>{brief.next_step.rationale}</small>}</div>
    </section>}

    {(reportArtifact || hasLegacyReport) && <button className="brief-report-link" type="button" onClick={() => layoutStore.openPanel('report')}>
      <FileText size={16}/><span>阅读完整研究报告</span><ArrowRight size={15}/>
    </button>}

    {hasLimitations && <section className="brief-limitations" aria-label="影响阅读的限制">
      <div className="brief-limitations-head"><h3>影响理解的限制</h3><span>{allReadableLimitations.length} 条</span></div>
      <ul>{primaryLimitations.map((item, index) => <li key={`primary:${index}:${item.description}`}><strong>{item.description}</strong>{item.impact && <p>{item.impact}</p>}</li>)}</ul>
      {remainingLimitations.length > 0 && <details>
        <summary>查看其余限制（{remainingLimitations.length}）</summary>
        <ul>{remainingLimitations.map((item, index) => <li key={`more:${index}:${item.description}`}><strong>{item.description}</strong>{item.impact && <p>{item.impact}</p>}</li>)}</ul>
      </details>}
      <button className="inline-link-button" type="button" onClick={() => layoutStore.openPanel('activity')}>处理细节与执行记录</button>
    </section>}
  </section>;
}
