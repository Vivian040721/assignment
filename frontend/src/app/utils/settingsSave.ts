import type { ConfigUpdate } from '../types/contracts';

export type SettingsSection = 'deepseek' | 'openalex';

export interface SettingsDraft {
  baseUrl: string;
  model: string;
  proxy: string;
  topK: number;
  concurrency: number;
  llmTimeout: number;
  httpTimeout: number;
  thinking: boolean;
  apiKey: string;
  openAlexKey: string;
  clearApiKey: boolean;
  clearOpenAlexKey: boolean;
}

/** Each form owns its own credentials and fields. Never submit unsaved values from the other form. */
export function settingsUpdateFor(section: SettingsSection, draft: SettingsDraft): ConfigUpdate {
  if (section === 'openalex') {
    return draft.clearOpenAlexKey
      ? { clear_openalex_api_key: true }
      : draft.openAlexKey.trim() ? { openalex_api_key: draft.openAlexKey.trim() } : {};
  }

  return {
    base_url: draft.baseUrl.trim(),
    model: draft.model.trim(),
    http_proxy: draft.proxy.trim(),
    default_top_k: draft.topK,
    concurrency: draft.concurrency,
    llm_timeout: draft.llmTimeout,
    http_timeout: draft.httpTimeout,
    thinking_enabled: draft.thinking,
    ...(draft.clearApiKey
      ? { clear_api_key: true }
      : draft.apiKey.trim() ? { api_key: draft.apiKey.trim() } : {}),
  };
}

/** Validate the numeric inputs even when programmatic edits bypass the HTML input constraints. */
export function deepSeekDraftError(draft: SettingsDraft): string | null {
  if (!draft.baseUrl.trim() || !draft.model.trim()) return '请填写 API Base URL 和模型名称。';
  const limits: Array<[number, number, number, string]> = [
    [draft.topK, 1, 15, '默认精选论文数量'],
    [draft.concurrency, 1, 8, '并发数'],
    [draft.llmTimeout, 5, 600, '模型超时'],
    [draft.httpTimeout, 5, 300, 'HTTP 超时'],
  ];
  for (const [value, min, max, label] of limits) {
    if (!Number.isInteger(value) || value < min || value > max) {
      return `${label}必须是 ${min}–${max} 范围内的整数。`;
    }
  }
  return null;
}
