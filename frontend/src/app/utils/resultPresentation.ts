import type { Artifact, Paper } from '../types/contracts';
import type { PaperCollection } from './papers';

/** The selection stage and the actual analysis stage are different claims. */
export interface DisplayedPaperSelection {
  papers: Paper[];
  stage: 'analysis' | 'selection' | 'empty';
  description: string;
}

export function displayedPapers(collection: PaperCollection): DisplayedPaperSelection {
  const result = collection.result;
  if (!result) return { papers: [], stage: 'empty', description: '' };

  const recorded = result.metrics?.analysis_paper_ids;
  if (Array.isArray(recorded)) {
    const seen = new Set<string>();
    const analyzed = recorded
      .filter((id): id is string => typeof id === 'string' && Boolean(id.trim()))
      .map(id => collection.paperById.get(id))
      .filter((paper): paper is Paper => {
        if (!paper || seen.has(paper.paper_id)) return false;
        seen.add(paper.paper_id);
        return true;
      });
    if (analyzed.length > 0) {
      return { papers: analyzed, stage: 'analysis', description: '以下论文实际进入分析；是否形成完整研究笔记，以每篇状态为准。' };
    }
    if (collection.topPapers.length > 0) {
      return { papers: collection.topPapers, stage: 'selection', description: '本次有精选候选论文，但没有可确认的实际分析论文；候选不等于已完成阅读或总结。' };
    }
    return { papers: [], stage: 'empty', description: '本次没有返回可展示的精选论文或实际分析论文。' };
  }

  // Older results may not record analysis_paper_ids. Do not infer that
  // selecting a paper means the backend read or summarized it.
  if (collection.topPapers.length > 0) {
    return { papers: collection.topPapers, stage: 'selection', description: '旧结果未记录实际分析论文列表；这里展示的是精选论文，不能据此确认已阅读全文。' };
  }
  return { papers: [], stage: 'empty', description: '本次没有返回可展示的论文。' };
}

/** A path alone is not a renderer-readable PDF artifact. */
export function paperMaterialNote(paper: Paper, artifacts: Artifact[]): string {
  if (paper.pdf_artifact_id || artifacts.some(item => item.kind === 'pdf' && item.paper_id === paper.paper_id)) {
    return '已关联 PDF 文件';
  }
  if (paper.pdf_path) return '仅记录 PDF 路径，尚无可读取的 PDF 文件标识';
  return '未提供 PDF 文件';
}

export interface LoadedReportDocument {
  key: string;
  text: string;
  truncated: boolean;
  error: string | null;
  loading: boolean;
}

/** Never use bytes/error/truncation from an artifact belonging to another job. */
export function visibleReportDocument(
  inlineMarkdown: string | null | undefined,
  artifactKey: string | null,
  document: LoadedReportDocument | null,
): { markdown: string; truncated: boolean; loading: boolean; error: string | null } {
  if (inlineMarkdown?.trim()) return { markdown: inlineMarkdown, truncated: false, loading: false, error: null };
  if (!artifactKey) return { markdown: '', truncated: false, loading: false, error: null };
  if (document?.key !== artifactKey) return { markdown: '', truncated: false, loading: true, error: null };
  return { markdown: document.text, truncated: document.truncated, loading: document.loading, error: document.error };
}
