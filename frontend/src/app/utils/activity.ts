import type { EventItem, PipelineResult } from '../types/contracts';
import { kindLabel, stageLabel, toolLabel } from './labels';

export type ToolCall = NonNullable<PipelineResult['tool_calls']>[number];

export function groupEvents(events: EventItem[]) {
  const groups = new Map<string, EventItem[]>();
  for (const event of events) {
    const key = event.stage || event.kind || 'other';
    const group = groups.get(key) ?? [];
    group.push(event);
    groups.set(key, group);
  }
  return [...groups.entries()].map(([key, items]) => ({
    key,
    label: key === 'trace' ? '服务端记录' : (stageLabel(key).startsWith('未知阶段') ? kindLabel(key) : stageLabel(key)),
    items,
    hasError: items.some(item => item.kind === 'error' || item.stage === 'error'),
    firstSeq: items[0]?.seq ?? 0,
  })).sort((left, right) => left.firstSeq - right.firstSeq);
}

export function groupToolCalls(calls: ToolCall[]) {
  const groups = new Map<string, ToolCall[]>();
  for (const call of calls) {
    const group = groups.get(call.tool) ?? [];
    group.push(call);
    groups.set(call.tool, group);
  }
  return [...groups.entries()].map(([tool, items]) => ({
    tool,
    label: toolLabel(tool),
    items,
    hasError: items.some(item => item.status === 'error'),
    duration: items.reduce((total, item) => total + (Number.isFinite(item.duration_seconds) ? item.duration_seconds : 0), 0),
  }));
}
