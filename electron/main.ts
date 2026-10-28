import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  ipcMain,
  Menu,
  nativeTheme,
  session,
  shell,
  type IpcMainInvokeEvent,
  type WebContents,
} from 'electron';
import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { copyFile, open, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { installWindowShortcuts } from './shortcuts';
import type {
  ArtifactText,
  BenchmarkFile,
  Conversation,
  ConversationSummary,
  Job,
  JobEvent,
  PdfArtifact,
  PublicConfig,
  ServiceInfo,
} from '../shared/contracts';
import { SseParser } from './sse';
import {
  InputError,
  validateAfter,
  validateArtifactLocation,
  validateArtifactText,
  validateBenchmarkFile,
  validateConfigUpdate,
  validateConversation,
  validateConversationSummaries,
  validateExternalUrl,
  validateId,
  validateJob,
  validateJobEvent,
  validatePrivateEndpoint,
  validatePublicConfig,
  validateSendMessage,
  validateServiceInfo,
  type ArtifactLocation,
  type PrivateEndpoint,
} from './validators';

const API_VERSION = 3;
const DEV_ORIGIN = 'http://127.0.0.1:5173';
const JOB_EVENT_CHANNEL = 'jobs:event';
const MAX_RESPONSE_CHARS = 50 * 1024 * 1024;
const MAX_PDF_BYTES = 100 * 1024 * 1024;
const MAIN_DIR = __dirname;
const DEV_REPO_ROOT = path.resolve(MAIN_DIR, '..', '..');
const RENDERER_FILE = path.resolve(app.getAppPath(), 'frontend', 'dist', 'index.html');

class DesktopError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'DesktopError';
  }
}

function safeError(error: unknown): Error {
  if (error instanceof InputError || error instanceof DesktopError) return error;
  return new DesktopError('本地操作失败，请检查服务状态后重试。');
}

function assertTrusted(event: IpcMainInvokeEvent): void {
  if (!mainWindow || mainWindow.isDestroyed() || event.sender !== mainWindow.webContents ||
      event.senderFrame !== event.sender.mainFrame || !isAllowedRendererUrl(event.senderFrame.url)) {
    throw new DesktopError('此操作仅允许由 Paper Agent 主界面发起。');
  }
}

function isAllowedRendererUrl(raw: string): boolean {
  try {
    const url = new URL(raw);
    if (url.protocol === 'file:') {
      if (url.search || url.hash) return false;
      const actual = path.resolve(fileURLToPath(url));
      return pathEquals(actual, RENDERER_FILE);
    }
    return url.origin === DEV_ORIGIN;
  } catch {
    return false;
  }
}

function pathEquals(left: string, right: string): boolean {
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function rendererDevUrl(): string | null {
  if (app.isPackaged) return null;
  const raw = process.env.PAPER_AGENT_RENDERER_URL;
  if (!raw) return null;
  try {
    const value = new URL(raw);
    if (value.origin === DEV_ORIGIN && (value.pathname === '/' || value.pathname === '') &&
        !value.username && !value.password && !value.search && !value.hash) return DEV_ORIGIN;
  } catch { /* Invalid environment values fall back to the built renderer. */ }
  return null;
}

function timeoutController(milliseconds: number): { controller: AbortController; clear: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), milliseconds);
  timer.unref?.();
  return { controller, clear: () => clearTimeout(timer) };
}

async function readJson(response: Response): Promise<unknown> {
  const length = Number(response.headers.get('content-length') || 0);
  if (length > MAX_RESPONSE_CHARS) throw new DesktopError('本地服务返回的数据过大。');
  const body = await response.text();
  if (body.length > MAX_RESPONSE_CHARS) throw new DesktopError('本地服务返回的数据过大。');
  try { return JSON.parse(body) as unknown; } catch { throw new DesktopError('本地服务返回的数据格式不正确。'); }
}

async function errorForResponse(response: Response): Promise<DesktopError> {
  let detail = '';
  try {
    const text = (await response.text()).slice(0, 4000);
    if (text) {
      const parsed = JSON.parse(text) as unknown;
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        const message = (parsed as Record<string, unknown>).error;
        if (typeof message === 'string') detail = message.slice(0, 500);
      }
    }
  } catch { /* A non-JSON error body keeps the status-only message. */ }
  return new DesktopError(detail
    ? `${detail}（状态码 ${response.status}）`
    : `本地服务请求失败（状态码 ${response.status}）。`);
}

class ServiceClient {
  private endpoint: PrivateEndpoint | null = null;
  private startPromise: Promise<PrivateEndpoint> | null = null;
  private lastHealthCheck = 0;

  async start(): Promise<PrivateEndpoint> {
    if (this.endpoint) return this.endpoint;
    if (!this.startPromise) {
      this.startPromise = this.startControlProcess().then((endpoint) => {
        this.endpoint = endpoint;
        this.lastHealthCheck = Date.now();
        return endpoint;
      }).finally(() => { this.startPromise = null; });
    }
    return this.startPromise;
  }

  private async startControlProcess(): Promise<PrivateEndpoint> {
    let executable: string;
    let args: string[];
    const env = { ...process.env };
    if (app.isPackaged) {
      executable = path.join(process.resourcesPath, 'backend', 'PaperAgent-service',
        process.platform === 'win32' ? 'PaperAgent-service.exe' : 'PaperAgent-service');
      args = ['--service-control', 'start', '--json'];
      if (!existsSync(executable)) throw new DesktopError('随应用提供的本地服务程序缺失。');
    } else {
      const explicit = process.env.PAPER_AGENT_PYTHON?.trim();
      const candidates = process.platform === 'win32'
        ? [path.join(DEV_REPO_ROOT, '.venv-v2', 'Scripts', 'python.exe')]
        : [path.join(DEV_REPO_ROOT, '.venv-v2', 'bin', 'python'),
          path.join(DEV_REPO_ROOT, '.venv-v2', 'Scripts', 'python.exe')];
      executable = explicit || candidates.find((candidate) => existsSync(candidate)) || '';
      if (!executable) throw new DesktopError('找不到项目虚拟环境 Python；可通过 PAPER_AGENT_PYTHON 指定解释器。');
      args = ['-m', 'paper_agent.service_control', 'start', '--json'];
      env.PYTHONPATH = [path.join(DEV_REPO_ROOT, 'src'), env.PYTHONPATH].filter(Boolean).join(path.delimiter);
    }

    const stdout = await new Promise<string>((resolve, reject) => {
      execFile(executable, args, {
        cwd: app.isPackaged ? process.resourcesPath : DEV_REPO_ROOT,
        env,
        windowsHide: true,
        timeout: 60_000,
        maxBuffer: 256 * 1024,
        encoding: 'utf8',
      }, (error, out, stderr) => {
        if (error) {
          const incompatible = stderr.includes('检测到不兼容的本地服务');
          reject(new DesktopError(incompatible
            ? '检测到旧版本本地服务。请先等待旧任务完成，或在原应用中取消任务；然后运行 npm run service:stop，再重新打开应用。研究历史和配置会保留。'
            : '本地后台服务无法启动，请检查 Python 环境或应用安装。'));
          return;
        }
        resolve(out);
      });
    });

    let descriptor: unknown;
    try { descriptor = JSON.parse(stdout.trim()) as unknown; }
    catch { throw new DesktopError('本地服务启动响应格式不正确。'); }
    return validatePrivateEndpoint(descriptor, API_VERSION);
  }

  async ensure(): Promise<PrivateEndpoint> {
    if (!this.endpoint) return this.start();
    if (Date.now() - this.lastHealthCheck < 1500) return this.endpoint;
    try {
      const info = await this.request<ServiceInfo>('/api/service', { method: 'GET' }, validateServiceInfo, this.endpoint);
      if (info.api_version !== API_VERSION || info.pid !== this.endpoint.pid) throw new DesktopError('本地服务版本不匹配。');
      this.lastHealthCheck = Date.now();
      return this.endpoint;
    } catch {
      this.endpoint = null;
      return this.start();
    }
  }

  async request<T>(
    route: string,
    init: { method: 'GET' | 'POST' | 'PUT'; body?: unknown },
    validate: (value: unknown) => T,
    endpointOverride?: PrivateEndpoint,
    timeoutMs = 30_000,
  ): Promise<T> {
    const endpoint = endpointOverride || await this.ensure();
    const timeout = timeoutController(timeoutMs);
    try {
      const response = await fetch(`${endpoint.url}${route}`, {
        method: init.method,
        headers: {
          Authorization: `Bearer ${endpoint.token}`,
          Accept: 'application/json',
          ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        },
        ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
        signal: timeout.controller.signal,
      });
      if (!response.ok) throw await errorForResponse(response);
      return validate(await readJson(response));
    } catch (error) {
      if (error instanceof InputError || error instanceof DesktopError) throw error;
      throw new DesktopError('无法连接本地服务，请稍后重试。');
    } finally { timeout.clear(); }
  }

  async openJobArtifact(jobId: string, artifactId: string): Promise<ArtifactLocation> {
    const route = `/api/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactId)}/location`;
    return this.request(route, { method: 'GET' }, validateArtifactLocation);
  }

  async fetchEvents(subscription: Subscription): Promise<void> {
    let delay = 250;
    let notified = false;
    while (!subscription.controller.signal.aborted) {
      const connection = new AbortController();
      const abortConnection = () => connection.abort();
      subscription.controller.signal.addEventListener('abort', abortConnection, { once: true });
      let watchdog: ReturnType<typeof setTimeout>;
      const armWatchdog = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(abortConnection, 45_000);
        watchdog.unref?.();
      };
      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
      armWatchdog();
      try {
        const endpoint = await this.ensure();
        const url = `${endpoint.url}/api/jobs/${encodeURIComponent(subscription.jobId)}/events?after=${subscription.lastSeq}`;
        const response = await fetch(url, {
          method: 'GET',
          headers: { Authorization: `Bearer ${endpoint.token}`, Accept: 'text/event-stream' },
          signal: connection.signal,
          cache: 'no-store',
        });
        if ([401, 403, 404, 410].includes(response.status)) {
          if (response.status === 401) this.endpoint = null;
          this.sendJobEvent(subscription, { job_id: subscription.jobId, error: (await errorForResponse(response)).message });
          subscription.controller.abort();
          removeSubscription(subscription.key);
          return;
        }
        if (!response.ok || !response.body) throw await errorForResponse(response);
        const connectedAt = Date.now();
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SseParser();
        while (!subscription.controller.signal.aborted) {
          const { done, value } = await reader.read();
          armWatchdog();
          if (Date.now() - connectedAt > 15_000) { delay = 250; notified = false; }
          const frames = parser.push(done ? decoder.decode() : decoder.decode(value, { stream: true }), done);
          for (const frame of frames) {
            if (frame.event === 'update') {
              const previous = subscription.lastSeq;
              this.deliverUpdate(subscription, frame.data);
              if (subscription.lastSeq > previous) { delay = 250; notified = false; }
            }
            else if (frame.event === 'done' && this.deliverDone(subscription, frame.data)) {
              subscription.controller.abort();
              removeSubscription(subscription.key);
              return;
            }
          }
          if (done) break;
        }
      } catch {
        if (subscription.controller.signal.aborted) return;
        if (!notified) {
          this.sendJobEvent(subscription, { job_id: subscription.jobId, error: '任务进度连接中断，正在重连。' });
          notified = true;
        }
      } finally {
        clearTimeout(watchdog!);
        subscription.controller.signal.removeEventListener('abort', abortConnection);
        connection.abort();
        try { await reader?.cancel(); } catch { /* Stream closed or aborted. */ }
      }
      if (!subscription.controller.signal.aborted) {
        await waitForRetry(delay, subscription.controller.signal);
        delay = Math.min(delay * 2, 5000);
      }
    }
  }

  private deliverUpdate(subscription: Subscription, raw: string): void {
    if (raw.length > 1_000_000) return;
    try {
      const parsed = JSON.parse(raw) as unknown;
      const jobEvent = validateJobEvent({ job_id: subscription.jobId, event: parsed }, subscription.jobId);
      const seq = jobEvent.event?.seq;
      if (seq === undefined || seq <= subscription.lastSeq) return;
      subscription.lastSeq = seq;
      this.sendJobEvent(subscription, jobEvent);
    } catch { /* Ignore malformed, stale, or oversized event frames. */ }
  }

  private deliverDone(subscription: Subscription, raw: string): boolean {
    if (raw.length > 100_000) return false;
    try {
      const value = JSON.parse(raw) as unknown;
      if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
      const parsed = value as Record<string, unknown>;
      if (parsed.job_id !== undefined && parsed.job_id !== subscription.jobId) return false;
      this.sendJobEvent(subscription, validateJobEvent({ job_id: subscription.jobId, done: true }, subscription.jobId));
      return true;
    } catch { return false; }
  }

  private sendJobEvent(subscription: Subscription, value: JobEvent): void {
    if (subscription.sender.isDestroyed() || subscription.controller.signal.aborted) return;
    if (!isAllowedRendererUrl(subscription.sender.getURL())) {
      removeSubscriptionsFor(subscription.sender);
      return;
    }
    subscription.sender.send(JOB_EVENT_CHANNEL, { subscription_id: subscription.id, value });
  }
}

function waitForRetry(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', finish);
      resolve();
    };
    const timer = setTimeout(finish, milliseconds);
    signal.addEventListener('abort', finish, { once: true });
  });
}

interface Subscription {
  id: string;
  key: string;
  jobId: string;
  lastSeq: number;
  sender: WebContents;
  controller: AbortController;
}

const service = new ServiceClient();
const subscriptions = new Map<string, Subscription>();
let mainWindow: BrowserWindow | null = null;
let quitting = false;
let startupComplete = false;

function subscriptionKey(contents: WebContents, id: string): string {
  return `${contents.id}:${id}`;
}

function removeSubscription(key: string): void {
  const current = subscriptions.get(key);
  if (!current) return;
  current.controller.abort();
  subscriptions.delete(key);
}

function removeSubscriptionsFor(contents: WebContents): void {
  for (const [key, current] of subscriptions) {
    if (current.sender === contents) removeSubscription(key);
  }
}

function removeAllSubscriptions(): void {
  for (const key of subscriptions.keys()) removeSubscription(key);
}

function registerHandler<TArgs extends unknown[], TResult>(
  channel: string,
  handler: (event: IpcMainInvokeEvent, ...args: TArgs) => Promise<TResult> | TResult,
): void {
  ipcMain.handle(channel, async (event, ...args: unknown[]) => {
    try {
      assertTrusted(event);
      return await handler(event, ...(args as TArgs));
    } catch (error) {
      throw safeError(error);
    }
  });
}

function safeFilename(location: ArtifactLocation): { filePath: string; extension: string } {
  const extension = path.extname(location.name).toLowerCase();
  if (!['.pdf', '.md', '.json'].includes(extension) || path.basename(location.name) !== location.name) {
    throw new DesktopError('此文件类型不支持桌面打开或导出。');
  }
  if (!['pdf', 'markdown', 'summary', 'report', 'run'].includes(location.kind) ||
      (extension === '.pdf' && location.kind !== 'pdf') ||
      (extension === '.json' && location.kind !== 'run') ||
      (extension === '.md' && !['markdown', 'summary', 'report'].includes(location.kind))) {
    throw new DesktopError('此文件类型不支持桌面打开或导出。');
  }
  if (!path.isAbsolute(location.path)) throw new DesktopError('无法打开此文件。');
  return { filePath: path.resolve(location.path), extension };
}

async function readRegisteredPdf(location: ArtifactLocation): Promise<PdfArtifact> {
  const { filePath } = safeFilename(location);
  if (location.kind !== 'pdf' || path.extname(location.name).toLowerCase() !== '.pdf') {
    throw new DesktopError('此文件不是可读取的 PDF。');
  }
  const file = await open(filePath, 'r').catch(() => {
    throw new DesktopError('PDF 文件不存在或已移走。');
  });
  try {
    const fileStats = await file.stat();
    if (!fileStats.isFile() || fileStats.size < 1 || fileStats.size > MAX_PDF_BYTES) {
      throw new DesktopError('PDF 文件为空或超过 100 MB 读取上限。');
    }
    const data = await file.readFile();
    if (data.byteLength < 1 || data.byteLength > MAX_PDF_BYTES ||
        !data.subarray(0, Math.min(data.byteLength, 1024)).includes('%PDF-')) {
      throw new DesktopError('文件内容不是有效的 PDF。');
    }
    return {
      data: new Uint8Array(data.buffer, data.byteOffset, data.byteLength),
      name: location.name,
      kind: 'pdf',
    };
  } catch (error) {
    if (error instanceof DesktopError) throw error;
    throw new DesktopError('PDF 文件读取失败。');
  } finally {
    await file.close();
  }
}

function registerIpc(): void {
  registerHandler('service:info', async () => {
    const endpoint = await service.ensure();
    const info = await service.request('/api/service', { method: 'GET' }, validateServiceInfo, endpoint);
    if (info.api_version !== endpoint.api_version || info.pid !== endpoint.pid) {
      throw new DesktopError('本地服务版本不匹配，请重启应用。');
    }
    return info;
  });
  registerHandler('config:get', () => service.request<PublicConfig>('/api/config', { method: 'GET' }, validatePublicConfig));
  registerHandler('config:put', (_event, values: unknown) => {
    const clean = validateConfigUpdate(values);
    return service.request<PublicConfig>('/api/config', { method: 'PUT', body: clean }, validatePublicConfig);
  });
  registerHandler('conversations:list', () => service.request<ConversationSummary[]>('/api/conversations', { method: 'GET' }, validateConversationSummaries));
  registerHandler('conversations:get', (_event, rawId: unknown) => {
    const id = validateId(rawId, '会话标识');
    return service.request<Conversation>(`/api/conversations/${encodeURIComponent(id)}`, { method: 'GET' }, validateConversation);
  });
  registerHandler('conversations:send', (_event, values: unknown) => {
    const clean = validateSendMessage(values);
    return service.request<Job>('/api/conversations/messages', { method: 'POST', body: clean }, validateJob);
  });
  registerHandler('jobs:get', (_event, rawId: unknown) => {
    const id = validateId(rawId, '任务标识');
    return service.request<Job>(`/api/jobs/${encodeURIComponent(id)}`, { method: 'GET' }, validateJob);
  });
  registerHandler('jobs:cancel', async (_event, rawId: unknown) => {
    const id = validateId(rawId, '任务标识');
    try {
      return await service.request<Job>(`/api/jobs/${encodeURIComponent(id)}/cancel`,
        { method: 'POST' }, validateJob, undefined, 90_000);
    } catch (error) {
      // A slow MCP cleanup may have completed cancellation after the HTTP timeout.
      try {
        const latest = await service.request<Job>(`/api/jobs/${encodeURIComponent(id)}`,
          { method: 'GET' }, validateJob);
        if (latest.status === 'cancelled') return latest;
      } catch { /* Preserve the original cancellation error. */ }
      throw error;
    }
  });
  registerHandler('jobs:subscribe', (event, rawId: unknown, rawAfter: unknown, rawSubscriptionId: unknown) => {
    const jobId = validateId(rawId, '任务标识');
    const after = validateAfter(rawAfter);
    if (typeof rawSubscriptionId !== 'string' || !/^[A-Za-z0-9_-]{16,64}$/.test(rawSubscriptionId)) {
      throw new InputError('订阅标识格式不正确。');
    }
    const sender = event.sender;
    const key = subscriptionKey(sender, rawSubscriptionId);
    if (!subscriptions.has(key) && [...subscriptions.values()].filter((item) => item.sender === sender).length >= 32) {
      throw new DesktopError('同时订阅的任务过多。');
    }
    removeSubscription(key);
    const subscription: Subscription = {
      id: rawSubscriptionId, key, jobId, lastSeq: after, sender, controller: new AbortController(),
    };
    subscriptions.set(key, subscription);
    void service.fetchEvents(subscription).catch(() => removeSubscription(key));
    return { subscribed: true };
  });
  registerHandler('jobs:unsubscribe', (event, rawSubscriptionId: unknown) => {
    if (typeof rawSubscriptionId !== 'string' || !/^[A-Za-z0-9_-]{16,64}$/.test(rawSubscriptionId)) {
      throw new InputError('订阅标识格式不正确。');
    }
    removeSubscription(subscriptionKey(event.sender, rawSubscriptionId));
    return { unsubscribed: true };
  });
  registerHandler('artifacts:read', (_event, rawJobId: unknown, rawArtifactId: unknown) => {
    const jobId = validateId(rawJobId, '任务标识');
    const artifactId = validateId(rawArtifactId, '文件标识');
    const route = `/api/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactId)}/text`;
    return service.request<ArtifactText>(route, { method: 'GET' }, validateArtifactText);
  });
  registerHandler('artifacts:read-pdf', async (_event, rawJobId: unknown, rawArtifactId: unknown) => {
    const jobId = validateId(rawJobId, '任务标识');
    const artifactId = validateId(rawArtifactId, '文件标识');
    return readRegisteredPdf(await service.openJobArtifact(jobId, artifactId));
  });
  registerHandler('artifacts:open', async (_event, rawJobId: unknown, rawArtifactId: unknown) => {
    const location = await service.openJobArtifact(validateId(rawJobId, '任务标识'), validateId(rawArtifactId, '文件标识'));
    const { filePath } = safeFilename(location);
    const error = await shell.openPath(filePath);
    if (error) throw new DesktopError('系统无法打开该文件。');
  });
  registerHandler('artifacts:export', async (event, rawJobId: unknown, rawArtifactId: unknown) => {
    const location = await service.openJobArtifact(validateId(rawJobId, '任务标识'), validateId(rawArtifactId, '文件标识'));
    const { filePath, extension } = safeFilename(location);
    const parent = BrowserWindow.fromWebContents(event.sender) || undefined;
    const saveOptions = {
      defaultPath: location.name,
      filters: [{ name: extension.slice(1).toUpperCase(), extensions: [extension.slice(1)] }],
    };
    const choice = parent
      ? await dialog.showSaveDialog(parent, saveOptions)
      : await dialog.showSaveDialog(saveOptions);
    if (choice.canceled || !choice.filePath) return { cancelled: true };
    try { await copyFile(filePath, choice.filePath); }
    catch { throw new DesktopError('文件导出失败。'); }
    return { cancelled: false, path: choice.filePath };
  });
  registerHandler('external:open', async (_event, rawUrl: unknown) => {
    try { await shell.openExternal(validateExternalUrl(rawUrl)); }
    catch (error) { if (error instanceof InputError) throw error; throw new DesktopError('无法打开此链接。'); }
  });
  registerHandler('data:open', async () => {
    const endpoint = await service.ensure();
    const error = await shell.openPath(endpoint.data_root);
    if (error) throw new DesktopError('无法打开资料目录。');
  });
  registerHandler('clipboard:write', (_event, rawText: unknown) => {
    if (typeof rawText !== 'string' || rawText.length > 100_000) throw new InputError('复制内容过长或格式不正确。');
    clipboard.writeText(rawText);
  });
  registerHandler('benchmark:load', async (event) => {
    const parent = BrowserWindow.fromWebContents(event.sender) || undefined;
    const openOptions: Electron.OpenDialogOptions = {
      properties: ['openFile'], filters: [{ name: 'JSON', extensions: ['json'] }],
    };
    const choice = parent
      ? await dialog.showOpenDialog(parent, openOptions)
      : await dialog.showOpenDialog(openOptions);
    if (choice.canceled || !choice.filePaths.length) return null;
    const filePath = choice.filePaths[0];
    if (path.extname(filePath).toLowerCase() !== '.json') throw new DesktopError('请选择 JSON 基准文件。');
    let fileStats;
    try { fileStats = await stat(filePath); } catch { throw new DesktopError('无法读取所选文件。'); }
    if (!fileStats.isFile() || fileStats.size > 5 * 1024 * 1024) throw new DesktopError('基准文件必须小于 5 MB。');
    let parsed: unknown;
    let content: Buffer;
    try { content = await readFile(filePath); }
    catch { throw new DesktopError('无法读取所选文件。'); }
    if (content.byteLength > 5 * 1024 * 1024) throw new DesktopError('基准文件必须小于 5 MB。');
    try { parsed = JSON.parse(content.toString('utf8')) as unknown; }
    catch { throw new DesktopError('所选文件不是有效 JSON。'); }
    return validateBenchmarkFile(path.basename(filePath), parsed) as BenchmarkFile;
  });
}

async function createMainWindow(): Promise<void> {
  if (mainWindow && !mainWindow.isDestroyed()) { mainWindow.show(); mainWindow.focus(); return; }
  const rendererUrl = rendererDevUrl();
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 960,
    minHeight: 640,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#161513' : '#f5f3ee',
    webPreferences: {
      preload: path.resolve(MAIN_DIR, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      devTools: !app.isPackaged,
    },
  });
  const contents = mainWindow.webContents;
  installWindowShortcuts(contents, { packaged: app.isPackaged, quit: () => app.quit() });
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  contents.on('will-navigate', (event) => event.preventDefault());
  contents.on('will-redirect', (event) => event.preventDefault());
  contents.on('will-frame-navigate', (event) => event.preventDefault());
  contents.on('will-attach-webview', (event) => event.preventDefault());
  contents.on('did-start-navigation', (_event, _url, _isInPlace, isMainFrame) => {
    if (isMainFrame) removeSubscriptionsFor(contents);
  });
  contents.on('render-process-gone', () => removeSubscriptionsFor(contents));
  contents.on('destroyed', () => removeSubscriptionsFor(contents));
  mainWindow.once('ready-to-show', () => mainWindow?.show());
  mainWindow.on('closed', () => { mainWindow = null; });
  if (rendererUrl) await mainWindow.loadURL(rendererUrl);
  else await mainWindow.loadFile(RENDERER_FILE);
}

async function startWithRetry(): Promise<void> {
  while (!quitting) {
    try {
      await service.start();
      return;
    } catch (error) {
      const safe = safeError(error);
      const result = await dialog.showMessageBox({
        type: 'error',
        title: 'Paper Agent 服务启动失败',
        message: safe.message,
        buttons: ['重试', '退出'],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
      });
      if (result.response !== 0) {
        app.quit();
        return;
      }
    }
  }
}

app.on('before-quit', () => {
  quitting = true;
  removeAllSubscriptions();
});
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
app.on('activate', () => {
  if (startupComplete && BrowserWindow.getAllWindows().length === 0) void createMainWindow();
});

void app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  app.setAppUserModelId('org.paperagent.workbench');
  Menu.setApplicationMenu(null);
  registerIpc();
  await startWithRetry();
  startupComplete = !quitting;
  if (!quitting) await createMainWindow();
}).catch((error: unknown) => {
  dialog.showErrorBox('Paper Agent 启动失败', safeError(error).message);
  app.quit();
});

