import type {
  Action,
  ArtifactText,
  BenchmarkFile,
  ConfigUpdate,
  Conversation,
  ConversationSummary,
  Job,
  JobEvent,
  PublicConfig,
  SendMessage,
  ServiceInfo,
} from '../shared/contracts';

const ID_RE = /^[A-Za-z0-9_-]{1,128}$/;
const CONFIG_KEYS = new Set<keyof ConfigUpdate>([
  'base_url', 'model', 'api_key', 'clear_api_key', 'openalex_api_key',
  'clear_openalex_api_key', 'thinking_enabled', 'http_proxy', 'default_top_k',
  'concurrency', 'llm_timeout', 'http_timeout',
]);
const STRING_LIMITS: Partial<Record<keyof ConfigUpdate, number>> = {
  base_url: 4096, model: 200, api_key: 4096, openalex_api_key: 4096, http_proxy: 4096,
};

export interface PrivateEndpoint {
  url: string;
  mcp_url: string;
  pid: number;
  api_version: number;
  data_root: string;
  token: string;
}

export interface ArtifactLocation {
  path: string;
  name: string;
  kind: string;
}

export class InputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'InputError';
  }
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new InputError(`${label}必须是对象。`);
  }
  return value as Record<string, unknown>;
}

function boundedString(value: unknown, label: string, max: number, allowEmpty = false): string {
  if (typeof value !== 'string' || value.length > max || (!allowEmpty && !value.trim())) {
    throw new InputError(`${label}格式不正确。`);
  }
  return value;
}

function finiteNumber(value: unknown, label: string, min: number, max: number, integer = false): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max ||
      (integer && !Number.isInteger(value))) {
    throw new InputError(`${label}超出允许范围。`);
  }
  return value;
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') throw new InputError(`${label}格式不正确。`);
  return value;
}

export function validateId(value: unknown, label = '标识'): string {
  if (typeof value !== 'string' || !ID_RE.test(value)) throw new InputError(`${label}格式不正确。`);
  return value;
}

export function validateAfter(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0 || value > 2_147_483_647) {
    throw new InputError('事件序号格式不正确。');
  }
  return value;
}

function validHttpUrl(value: string, label: string): void {
  let url: URL;
  try { url = new URL(value); } catch { throw new InputError(`${label}地址格式不正确。`); }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password ||
      url.search || url.hash) {
    throw new InputError(`${label}地址格式不正确。`);
  }
}

/** Unknown config keys are dropped to match the backend model's default extra=ignore behavior. */
export function validateConfigUpdate(value: unknown): ConfigUpdate {
  const input = object(value, '设置');
  const result: Record<string, unknown> = {};
  for (const [rawKey, rawValue] of Object.entries(input)) {
    if (!CONFIG_KEYS.has(rawKey as keyof ConfigUpdate)) continue;
    const key = rawKey as keyof ConfigUpdate;
    const max = STRING_LIMITS[key];
    if (max !== undefined) {
      const text = boundedString(rawValue, key, max, key === 'api_key' || key === 'openalex_api_key' || key === 'http_proxy');
      if (key === 'base_url' || key === 'http_proxy') {
        if (text.trim()) validHttpUrl(text.trim(), key === 'base_url' ? 'API' : '代理');
      }
      if (key === 'model' && (text.trim().length === 0 || /\s/.test(text))) {
        throw new InputError('模型名称格式不正确。');
      }
      result[key] = text;
      continue;
    }
    if (key === 'default_top_k') result[key] = finiteNumber(rawValue, key, 1, 15, true);
    else if (key === 'concurrency') result[key] = finiteNumber(rawValue, key, 1, 8, true);
    else if (key === 'llm_timeout') result[key] = finiteNumber(rawValue, key, 5, 600);
    else if (key === 'http_timeout') result[key] = finiteNumber(rawValue, key, 5, 300);
    else result[key] = boolean(rawValue, key);
  }
  return result as ConfigUpdate;
}

export function validateSendMessage(value: unknown): SendMessage {
  const input = object(value, '消息');
  const action = input.action;
  if (action !== 'research' && action !== 'chat') throw new InputError('消息类型不正确。');
  const text = boundedString(input.text, '消息', 12000);
  const result: SendMessage = { text, action: action as Action };
  if (input.conversation_id !== undefined && input.conversation_id !== null) {
    result.conversation_id = validateId(input.conversation_id, '会话标识');
  } else if (input.conversation_id === null) result.conversation_id = null;
  if (input.query !== undefined) result.query = boundedString(input.query, '检索问题', 1000, true);
  if (input.top_k !== undefined) result.top_k = finiteNumber(input.top_k, '论文数量', 1, 15, true);
  if (input.execution_mode !== undefined) {
    if (input.execution_mode !== 'sequential' && input.execution_mode !== 'concurrent') {
      throw new InputError('执行模式不正确。');
    }
    result.execution_mode = input.execution_mode;
  }
  if (input.mode !== undefined) {
    if (input.mode !== 'live' && input.mode !== 'demo') throw new InputError('研究模式不正确。');
    result.mode = input.mode;
  }
  return result;
}

export function validateExternalUrl(value: unknown): string {
  const raw = boundedString(value, '链接', 2048);
  let url: URL;
  try { url = new URL(raw); } catch { throw new InputError('链接地址格式不正确。'); }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) {
    throw new InputError('只支持打开有效的 HTTP 或 HTTPS 链接。');
  }
  return url.href;
}

export function validatePrivateEndpoint(value: unknown, expectedApiVersion: number): PrivateEndpoint {
  const input = object(value, '服务');
  const url = boundedString(input.url, '服务地址', 256);
  const mcpUrl = boundedString(input.mcp_url, 'MCP 地址', 300);
  const token = boundedString(input.token, '服务凭据', 512);
  const dataRoot = boundedString(input.data_root, '资料目录', 4096);
  let parsed: URL;
  try { parsed = new URL(url); } catch { throw new InputError('本地服务启动失败。'); }
  if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1' || !parsed.port ||
      parsed.pathname !== '/' || parsed.search || parsed.hash || parsed.username || parsed.password ||
      mcpUrl !== `${url}/mcp` || token.length < 32 || !Number.isInteger(input.pid) ||
      (input.pid as number) < 1 || input.api_version !== expectedApiVersion ||
      !isAbsolutePath(dataRoot)) {
    throw new InputError('本地服务启动失败。');
  }
  return {
    url, mcp_url: mcpUrl, pid: input.pid as number,
    api_version: expectedApiVersion, data_root: dataRoot, token,
  };
}

function isAbsolutePath(value: string): boolean {
  return value.startsWith('/') || /^[A-Za-z]:[\\/]/.test(value) || value.startsWith('\\\\');
}

export function validateServiceInfo(value: unknown): ServiceInfo {
  const input = object(value, '服务状态');
  if (typeof input.version !== 'string' || input.version.length > 80 ||
      !Number.isInteger(input.api_version) || !Number.isInteger(input.pid) ||
      (input.pid as number) < 1 || (input.api_version as number) < 1 ||
      !Number.isInteger(input.active_jobs) || (input.active_jobs as number) < 0 ||
      typeof input.data_root !== 'string' || input.data_root.length > 4096 ||
      typeof input.config_path !== 'string' || input.config_path.length > 4096) {
    throw new InputError('无法读取本地服务状态。');
  }
  return {
    version: input.version, api_version: input.api_version as number,
    pid: input.pid as number, active_jobs: input.active_jobs as number,
    data_root: input.data_root, config_path: input.config_path,
  };
}

export function validatePublicConfig(value: unknown): PublicConfig {
  const input = object(value, '模型设置');
  if (typeof input.provider !== 'string' || typeof input.base_url !== 'string' ||
      typeof input.model !== 'string' || typeof input.api_key_configured !== 'boolean' ||
      typeof input.openalex_api_key_configured !== 'boolean' || typeof input.thinking_enabled !== 'boolean' ||
      typeof input.http_proxy !== 'string' || !Number.isInteger(input.default_top_k) ||
      (input.default_execution_mode !== 'concurrent' && input.default_execution_mode !== 'sequential') ||
      !Number.isInteger(input.concurrency) || typeof input.llm_timeout !== 'number' ||
      typeof input.http_timeout !== 'number') {
    throw new InputError('无法读取模型设置。');
  }
  return {
    provider: input.provider, base_url: input.base_url, model: input.model,
    api_key_configured: input.api_key_configured,
    openalex_api_key_configured: input.openalex_api_key_configured,
    thinking_enabled: input.thinking_enabled, http_proxy: input.http_proxy,
    default_top_k: input.default_top_k as number,
    default_execution_mode: input.default_execution_mode,
    concurrency: input.concurrency as number,
    llm_timeout: input.llm_timeout, http_timeout: input.http_timeout,
  };
}

export function validateArtifactText(value: unknown): ArtifactText {
  const input = object(value, '文件内容');
  if (typeof input.text !== 'string' || input.text.length > 2 * 1024 * 1024 ||
      typeof input.name !== 'string' || input.name.length > 500 ||
      typeof input.kind !== 'string' || input.kind.length > 80 ||
      typeof input.truncated !== 'boolean') {
    throw new InputError('无法读取文件内容。');
  }
  return { text: input.text, name: input.name, kind: input.kind, truncated: input.truncated };
}

export function validateArtifactLocation(value: unknown): ArtifactLocation {
  const input = object(value, '文件位置');
  if (typeof input.path !== 'string' || input.path.length > 4096 || !isAbsolutePath(input.path) ||
      typeof input.name !== 'string' || input.name.length > 500 ||
      typeof input.kind !== 'string' || input.kind.length > 80) {
    throw new InputError('无法打开此文件。');
  }
  return { path: input.path, name: input.name, kind: input.kind };
}

export function validateBenchmarkFile(name: string, value: unknown): BenchmarkFile {
  const input = object(value, '基准文件');
  return { name: boundedString(name, '文件名', 255), data: input };
}

export function validateConversationSummaries(value: unknown): ConversationSummary[] {
  if (!Array.isArray(value) || value.length > 10000) throw new InputError('无法读取会话列表。');
  return value.map((item) => {
    const summary = object(item, '会话');
    const conversationId = validateId(summary.conversation_id, '会话标识');
    if (typeof summary.title !== 'string' || summary.title.length > 500 ||
        typeof summary.updated_at !== 'string' || summary.updated_at.length > 100 ||
        typeof summary.status !== 'string' || summary.status.length > 100 ||
        (summary.read_only !== undefined && typeof summary.read_only !== 'boolean') ||
        (summary.job_id !== null && summary.job_id !== undefined &&
          (typeof summary.job_id !== 'string' || !ID_RE.test(summary.job_id)))) {
      throw new InputError('无法读取会话列表。');
    }
    return {
      conversation_id: conversationId,
      title: summary.title,
      updated_at: summary.updated_at,
      status: summary.status,
      job_id: (summary.job_id as string | null | undefined) ?? null,
      ...(summary.read_only === undefined ? {} : { read_only: summary.read_only as boolean }),
    };
  });
}

export function validateConversation(value: unknown): Conversation {
  const input = object(value, '会话');
  const summaries = validateConversationSummaries([{
    conversation_id: input.conversation_id,
    title: input.title,
    updated_at: input.updated_at,
    status: input.status,
    job_id: input.job_id,
    read_only: input.read_only,
  }]);
  if (!Array.isArray(input.messages) || input.messages.length > 100000 ||
      (input.result !== null && input.result !== undefined &&
        (input.result === null || typeof input.result !== 'object' || Array.isArray(input.result)))) {
    throw new InputError('无法读取会话内容。');
  }
  for (const raw of input.messages) {
    const message = object(raw, '消息');
    if (typeof message.id !== 'string' || message.id.length > 128 ||
        typeof message.role !== 'string' || message.role.length > 80 ||
        typeof message.content !== 'string' || message.content.length > 2_000_000 ||
        typeof message.kind !== 'string' || message.kind.length > 80 ||
        (message.status !== undefined && (typeof message.status !== 'string' || message.status.length > 80)) ||
        (message.job_id !== undefined && (typeof message.job_id !== 'string' || !ID_RE.test(message.job_id)))) {
      throw new InputError('无法读取会话内容。');
    }
  }
  return { ...summaries[0], messages: input.messages as Conversation['messages'],
    result: (input.result ?? null) as Conversation['result'],
    ...(input.result_job_id === undefined ? {} : { result_job_id: input.result_job_id as string | null }) };
}

export function validateJob(value: unknown): Job {
  const input = object(value, '任务');
  if (typeof input.job_id !== 'string' || !ID_RE.test(input.job_id) ||
      typeof input.session_id !== 'string' || !ID_RE.test(input.session_id) ||
      typeof input.idea !== 'string' || input.idea.length > 10000 ||
      typeof input.status !== 'string' || input.status.length > 100 ||
      (input.result !== null && input.result !== undefined &&
        (typeof input.result !== 'object' || Array.isArray(input.result))) ||
      !Array.isArray(input.events) || input.events.length > 100000 ||
      !Array.isArray(input.artifacts) || input.artifacts.length > 10000 ||
      typeof input.created_at !== 'string' || input.created_at.length > 100 ||
      typeof input.updated_at !== 'string' || input.updated_at.length > 100 ||
      (input.error !== null && input.error !== undefined &&
        (typeof input.error !== 'string' || input.error.length > 10000))) {
    throw new InputError('无法读取任务信息。');
  }
  for (const raw of input.artifacts) {
    const artifact = object(raw, '文件');
    if (typeof artifact.artifact_id !== 'string' || !ID_RE.test(artifact.artifact_id) ||
        typeof artifact.name !== 'string' || artifact.name.length > 500 ||
        typeof artifact.kind !== 'string' || artifact.kind.length > 80) {
      throw new InputError('无法读取任务文件。');
    }
  }
  return input as unknown as Job;
}

export function validateJobEvent(value: unknown, jobId: string): JobEvent {
  const input = object(value, '任务事件');
  if (input.job_id !== jobId) throw new InputError('任务事件格式不正确。');
  if (input.done === true) return { job_id: jobId, done: true };
  if (input.error !== undefined) {
    const message = typeof input.error === 'string' && input.error.trim()
      ? input.error.trim().slice(0, 300)
      : '任务事件连接暂时中断。';
    return { job_id: jobId, error: message };
  }
  const event = object(input.event, '任务事件内容');
  if (!Number.isSafeInteger(event.seq) || (event.seq as number) < 1 ||
      typeof event.timestamp !== 'string' || event.timestamp.length > 100 ||
      typeof event.kind !== 'string' || event.kind.length > 100 ||
      (event.message !== undefined && (typeof event.message !== 'string' || event.message.length > 10000)) ||
      (event.stage !== undefined && (typeof event.stage !== 'string' || event.stage.length > 200)) ||
      (event.data !== undefined && (event.data === null || typeof event.data !== 'object' || Array.isArray(event.data)))) {
    throw new InputError('任务事件格式不正确。');
  }
  return { job_id: jobId, event: event as unknown as JobEvent['event'] };
}
