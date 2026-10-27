const COMPARISON_LABELS: Record<string, string> = {
  paper_ids: '涉及论文', paper_id: '涉及论文', dimension: '比较维度', detail: '说明',
  method: '方法', mechanism: '机制', memory: '记忆机制', evidence: '证据',
  findings: '发现', results: '结果', limitations: '局限', difference: '差异',
  differences: '差异', relevance: '相关性', scope: '范围', claim: '主张',
  approach: '方法', evaluation: '评估', task: '任务', experiment: '实验', experiments: '实验',
};

function label(key: string): string {
  return COMPARISON_LABELS[key] ?? COMPARISON_LABELS[key.toLowerCase()] ?? key.replace(/_/g, ' ');
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return value.replace(/\s+/g, ' ').trim();
  if (typeof value === 'number') return String(value);
  if (Array.isArray(value)) {
    return value.map(renderValue).filter(Boolean).join('；');
  }
  if (isRecord(value)) {
    return Object.entries(value)
      .map(([key, item]) => {
        const rendered = renderValue(item);
        return rendered ? `${label(key)}：${rendered}` : '';
      })
      .filter(Boolean)
      .join('；');
  }
  return '';
}

export function readableComparisonMarkdown(rows: unknown[]): string {
  if (!rows.length) return '未生成。';
  return rows.map((row, index) => {
    if (!isRecord(row)) {
      return `### 比较 ${index + 1}\n\n该比较记录格式未知，完整数据保留在报告 JSON 产物中。`;
    }
    const rawIds = row.paper_ids ?? row.paper_id;
    const ids = Array.isArray(rawIds)
      ? rawIds.filter((value): value is string => typeof value === 'string')
      : typeof rawIds === 'string' ? [rawIds] : [];
    const lines = [
      `### 比较 ${index + 1}${ids.length ? '：' + ids.map(id => `[${id}]`).join('、') : ''}`,
    ];
    if (ids.length) lines.push(`- **涉及论文**：${ids.map(id => `[${id}]`).join('、')}`);
    for (const [key, value] of Object.entries(row)) {
      if (key === 'paper_ids' || key === 'paper_id') continue;
      const rendered = renderValue(value);
      if (rendered) lines.push(`- **${label(key)}**：${rendered}`);
    }
    if (lines.length === 1) lines.push('仅标注了涉及论文。');
    return lines.join('\n');
  }).join('\n\n');
}

type ParsedComparison =
  | { kind: 'rows'; rows: Record<string, unknown>[] }
  | { kind: 'empty' }
  | { kind: 'readable' }
  | { kind: 'json_unreliable' };

function expandComparisonValue(value: unknown): Record<string, unknown>[] | null {
  if (Array.isArray(value)) {
    const output: Record<string, unknown>[] = [];
    for (const item of value) {
      const expanded = expandComparisonValue(item);
      if (expanded === null) return null;
      output.push(...expanded);
    }
    return output;
  }
  return isRecord(value) ? [value] : null;
}

function parseComparisonBody(body: string): ParsedComparison {
  const trimmed = body.trim();
  if (!trimmed || trimmed === '未生成。') return { kind: 'empty' };
  try {
    const rows = expandComparisonValue(JSON.parse(trimmed));
    if (rows === null) return { kind: 'json_unreliable' };
    return rows.length ? { kind: 'rows', rows } : { kind: 'empty' };
  } catch {
    // Not one complete JSON value; only an all-lines legacy object block is
    // safe to treat as structured comparison data.
  }
  const lines = trimmed.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const candidateLines = lines.length > 0
    && lines.every(line => line.startsWith('{') || line.startsWith('['));
  const wholeJson = (trimmed.startsWith('{') && trimmed.endsWith('}'))
    || (trimmed.startsWith('[') && trimmed.endsWith(']'));
  if (!candidateLines) {
    return wholeJson ? { kind: 'json_unreliable' } : { kind: 'readable' };
  }
  const values: unknown[] = [];
  for (const line of lines) {
    try {
      values.push(JSON.parse(line));
    } catch {
      // A bracketed Markdown line such as "[paper_id] ..." is prose, not JSON.
      return line.startsWith('{') ? { kind: 'json_unreliable' } : { kind: 'readable' };
    }
  }
  const rows = expandComparisonValue(values);
  if (rows === null) return { kind: 'json_unreliable' };
  return rows.length ? { kind: 'rows', rows } : { kind: 'empty' };
}

function preserveOriginalComparison(body: string): string {
  const longestRun = (body.match(/`+/g) ?? []).reduce((longest, run) => Math.max(longest, run.length), 0);
  const fence = '`'.repeat(Math.max(3, longestRun + 1));
  return '> 旧版比较段无法可靠解析，以下按原文保留。\n\n'
    + `${fence}text\n${body.trim()}\n${fence}`;
}

/** Keep new readable reports intact; convert only reliably identified legacy comparison data. */
export function readableReportMarkdown(
  markdown: string, comparison?: Record<string, unknown>[] | null,
): string {
  if (!markdown) return markdown;
  const rows = Array.isArray(comparison) ? comparison : [];
  const match = /^##\s+论文比较\s*$/m.exec(markdown);
  if (match && match.index !== undefined) {
    const headingEnd = match.index + match[0].length;
    const rest = markdown.slice(headingEnd);
    const next = /^##\s+/m.exec(rest);
    const bodyEnd = next ? headingEnd + next.index : markdown.length;
    const body = markdown.slice(headingEnd, bodyEnd).trim();
    const parsed = parseComparisonBody(body);
    if (parsed.kind === 'readable') {
      // New backend already rendered a readable section; do not duplicate it.
      return markdown;
    }
    let rendered: string;
    if (parsed.kind === 'rows') {
      rendered = readableComparisonMarkdown(parsed.rows);
    } else if (parsed.kind === 'empty') {
      rendered = rows.length ? readableComparisonMarkdown(rows) : '未生成。';
    } else {
      rendered = preserveOriginalComparison(body);
    }
    return `${markdown.slice(0, headingEnd)}\n\n${rendered}\n\n${markdown.slice(bodyEnd)}`;
  }
  if (rows.length) {
    return `${markdown.trimEnd()}\n\n## 论文比较\n\n${readableComparisonMarkdown(rows)}\n`;
  }
  return markdown;
}
