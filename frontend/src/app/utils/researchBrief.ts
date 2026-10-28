import type {
  Artifact,
  DeliveryStatus,
  ExecutionMode,
  Job,
  PipelineResult,
  PublicConfig,
  ResearchBrief,
  ResearchLimitation,
} from '../types/contracts';

export const deliveryStatusLabels: Record<DeliveryStatus, string> = {
  result: '研究结果',
  preliminary_notes: '初步研究笔记',
  no_result: '未形成结果',
};

export const deliveryStatusNotes: Record<DeliveryStatus, string> = {
  result: '结论与证据可追溯；完整论证见研究报告。',
  preliminary_notes: '已有可用材料，但阅读范围或报告覆盖仍不完整。',
  no_result: '现有证据不足以形成可靠答案，下面保留可继续研究的方向。',
};

export interface BriefLimitationGroups {
  research: ResearchLimitation[];
  material: ResearchLimitation[];
  process: ResearchLimitation[];
}

const processLimitationPattern = /分块|校验|修复|字段|受限综合|有界|源文本|ocr|覆盖率|文本覆盖|模型返回|总结范围|输入|无法关联|未找到可核验|部分报告|全文总结少于|转换|读取失败|重试|预算/i;

function limitationKey(item: ResearchLimitation): string {
  return item.description.replace(/\s+/g, ' ').trim().toLocaleLowerCase();
}

export function groupBriefLimitations(limitations: ResearchLimitation[]): BriefLimitationGroups {
  const groups: BriefLimitationGroups = { research: [], material: [], process: [] };
  const seen = new Set<string>();
  for (const item of limitations) {
    const key = limitationKey(item);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    if (processLimitationPattern.test(item.description)) {
      groups.process.push(item);
    } else if ((item.impact ?? '').includes('实际综合报告')) {
      groups.research.push(item);
    } else {
      groups.material.push(item);
    }
  }
  return groups;
}

export function primaryBriefLimitations(groups: BriefLimitationGroups, limit = 2): ResearchLimitation[] {
  const primary = [...groups.material.slice(0, 1), ...groups.research.slice(0, 1)];
  if (primary.length < limit) {
    primary.push(...groups.material.slice(1), ...groups.research.slice(1));
  }
  return primary.slice(0, limit);
}

export function requestedExecutionMode(
  value: PublicConfig['default_execution_mode'] | ExecutionMode | null | undefined,
): ExecutionMode {
  if (value === 'parallel' || value === 'concurrent') return 'parallel';
  if (value === 'sequential') return 'sequential';
  return 'auto';
}

export function deliveryStatusFor(
  result: PipelineResult | null | undefined,
  job?: Job | null,
): DeliveryStatus | null {
  return result?.delivery_status ?? job?.delivery_status ?? null;
}

/** Only render supported findings and never fill the three-card target with placeholders. */
export function supportedFindings(brief: ResearchBrief | null | undefined) {
  return (brief?.findings ?? [])
    .filter(finding => finding.statement.trim().length > 0 && finding.evidence.length > 0)
    .slice(0, 3);
}

export function reportArtifactFor(
  result: PipelineResult | null | undefined,
  artifacts: Artifact[],
): Artifact | undefined {
  const canonicalId = result?.brief?.report_artifact_id ?? result?.report?.report_artifact_id;
  if (canonicalId) return artifacts.find(item => item.artifact_id === canonicalId);
  // Legacy runs did not persist a canonical ID. Keep their single existing report readable.
  return artifacts.find(item => item.kind === 'report')
    ?? artifacts.find(item => item.kind === 'markdown' && item.name.toLowerCase().includes('report'));
}
