import { useSyncExternalStore } from 'react';
import { desktopApi } from '../services/desktopApi';
import { isReadOnlyConversation, type Action, type Conversation, type ConversationSummary, type ExecutionMode, type Job, type Message, type PublicConfig, type ServiceInfo } from '../types/contracts';
import { isJobActive, isJobFinished } from '../utils/status';
import { requestedExecutionMode } from '../utils/researchBrief';

type BootStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface WorkspaceState {
  bootStatus: BootStatus;
  serviceInfo: ServiceInfo | null;
  config: PublicConfig | null;
  conversations: ConversationSummary[];
  conversationId: string | null;
  conversation: Conversation | null;
  loadingConversation: boolean;
  activeJob: Job | null;
  resultJob: Job | null;
  action: Action;
  executionMode: ExecutionMode;
  topK: number;
  drafts: Record<string, string>;
  sending: boolean;
  error: string | null;
  notice: string | null;
}

const NEW_DRAFT_KEY = '__new_conversation__';
const initialState: WorkspaceState = {
  bootStatus: 'idle',
  serviceInfo: null,
  config: null,
  conversations: [],
  conversationId: null,
  conversation: null,
  loadingConversation: false,
  activeJob: null,
  resultJob: null,
  action: 'research',
  executionMode: 'auto',
  topK: 5,
  drafts: {},
  sending: false,
  error: null,
  notice: null,
};

let state = initialState;
const listeners = new Set<() => void>();
let initialization: Promise<void> | null = null;
let initializationGeneration = 0;
let selectionSequence = 0;
const submittedByJob = new Map<string, { text: string; draftKey: string; action: Action }>();
const refreshedTerminalJobs = new Set<string>();

function publish(next: WorkspaceState): void {
  state = next;
  for (const listener of listeners) listener();
}

function update(updateValue: Partial<WorkspaceState> | ((current: WorkspaceState) => Partial<WorkspaceState>)): void {
  const changes = typeof updateValue === 'function' ? updateValue(state) : updateValue;
  publish({ ...state, ...changes });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function orderedConversations(items: ConversationSummary[]): ConversationSummary[] {
  return [...items].sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
}

function currentDraftKey(): string {
  return state.conversationId ?? NEW_DRAFT_KEY;
}

function updateConversationList(list: ConversationSummary[]): void {
  update({ conversations: orderedConversations(list) });
}

function getLatestJobId(conversation: Conversation, summary?: ConversationSummary): string | null {
  // The summary carries the newest job (which may be a chat), while
  // result_job_id carries the job that owns the research attachments.
  if (summary?.job_id) return summary.job_id;
  if (conversation.result_job_id) return conversation.result_job_id;
  const message = [...conversation.messages].reverse().find(item => item.job_id);
  return message?.job_id ?? null;
}

function retryTextFor(conversation: Conversation, job: Job | null): string | null {
  if (!job || job.action === 'chat' || (job.status !== 'failed' && job.status !== 'cancelled')) return null;
  const userMessage = [...conversation.messages].reverse().find((message: Message) => message.role === 'user' && (!message.job_id || message.job_id === job.job_id));
  return userMessage?.content || job.idea || null;
}

function installConversation(conversation: Conversation, job: Job | null, resultJob: Job | null, sequence: number, notice?: string): void {
  if (sequence !== selectionSequence) return;
  const selectedId = conversation.conversation_id;
  const failedText = retryTextFor(conversation, job);
  let drafts = state.drafts;
  if (failedText && !drafts[selectedId]) drafts = { ...drafts, [selectedId]: failedText };
  const completedResearch = Boolean(job && job.action === 'research' && isJobFinished(job.status) && (job.result || conversation.result));
  const nextAction: Action = completedResearch || conversation.result ? 'chat' : job?.action ?? state.action;
  publish({
    ...state,
    conversationId: selectedId,
    conversation,
    activeJob: job,
    resultJob,
    loadingConversation: false,
    action: nextAction,
    drafts,
    error: null,
    notice: notice ?? null,
  });
}

async function refreshConversation(id: string, sequence = selectionSequence): Promise<void> {
  try {
    const conversation = await desktopApi().conversation(id);
    if (sequence === selectionSequence && state.conversationId === id) {
      update({ conversation, error: null });
    }
  } catch (error) {
    if (sequence === selectionSequence && state.conversationId === id) update({ error: errorMessage(error) });
  }
}

async function refreshConversations(): Promise<void> {
  try {
    updateConversationList(await desktopApi().conversations());
  } catch (error) {
    update({ error: errorMessage(error) });
  }
}

function restoreRetryDraft(job: Job): void {
  if (job.status !== 'failed' && job.status !== 'cancelled') return;
  const submitted = submittedByJob.get(job.job_id);
  const text = submitted?.text || (job.action === 'research' ? job.idea : '');
  if (!text) return;
  const key = submitted?.draftKey || state.conversationId || NEW_DRAFT_KEY;
  if (!state.drafts[key]) update({ drafts: { ...state.drafts, [key]: text } });
}

function applyJob(job: Job): void {
  if (state.activeJob?.job_id !== job.job_id) return;
  const previous = state.activeJob;
  // A GET snapshot may be older than SSE events already received by this
  // renderer. Merge by seq instead of replacing the event list wholesale.
  const events = new Map(previous.events.map(event => [event.seq, event]));
  for (const event of job.events) events.set(event.seq, event);
  const merged: Job = { ...job, events: [...events.values()].sort((left, right) => left.seq - right.seq) };
  const next: Partial<WorkspaceState> = {
    activeJob: merged,
    ...(job.action === 'research' ? { resultJob: merged } : {}),
  };
  if (job.conversation && state.conversationId === job.conversation.conversation_id) next.conversation = job.conversation;
  if (job.action === 'research' && isJobFinished(job.status) && (job.result || state.conversation?.result)) next.action = 'chat';
  if (job.status === 'failed' || job.status === 'cancelled') next.action = job.action ?? state.action;
  update(next);
  restoreRetryDraft(job);
  if (isJobFinished(job.status) && !refreshedTerminalJobs.has(job.job_id)) {
    refreshedTerminalJobs.add(job.job_id);
    const conversationId = job.conversation_id ?? state.conversationId;
    if (conversationId && conversationId === state.conversationId) void refreshConversation(conversationId);
    void refreshConversations();
  }
}

async function linkAcceptedJob(job: Job, requestedConversationId: string | null, sequence: number): Promise<void> {
  let summary: ConversationSummary | undefined;
  let conversationId = job.conversation_id ?? job.conversation?.conversation_id ?? requestedConversationId;
  try {
    const list = await desktopApi().conversations();
    updateConversationList(list);
    if (!conversationId) {
      summary = list.find(item => item.job_id === job.job_id);
      conversationId = summary?.conversation_id ?? null;
    } else {
      summary = list.find(item => item.conversation_id === conversationId);
    }
  } catch (error) {
    update({ notice: `任务已提交；刷新会话列表时出错：${errorMessage(error)}` });
  }
  if (!conversationId) {
    update({ notice: `任务 ${job.job_id} 已提交。服务尚未返回会话标识；可从最近会话刷新查看，请勿重复提交。` });
    return;
  }
  if (sequence !== selectionSequence || (requestedConversationId && state.conversationId !== requestedConversationId)) return;
  const submission = submittedByJob.get(job.job_id);
  if (submission) submittedByJob.set(job.job_id, { ...submission, draftKey: conversationId });
  let drafts = state.drafts;
  if (!requestedConversationId && drafts[NEW_DRAFT_KEY] && !drafts[conversationId]) {
    drafts = { ...drafts, [conversationId]: drafts[NEW_DRAFT_KEY] };
    delete drafts[NEW_DRAFT_KEY];
  }
  update({ conversationId, drafts, notice: null });
  if (job.conversation) {
    const currentJob = state.activeJob?.job_id === job.job_id ? state.activeJob : job;
    update({ conversation: job.conversation, activeJob: currentJob,
      ...(job.action === 'research' ? { resultJob: currentJob } : {}) });
  } else {
    try {
      const conversation = await desktopApi().conversation(conversationId);
      if (sequence === selectionSequence && state.conversationId === conversationId) {
        const currentJob = state.activeJob?.job_id === job.job_id ? state.activeJob : job;
        update({ conversation, activeJob: currentJob,
          ...(job.action === 'research' ? { resultJob: currentJob } : {}) });
      }
    } catch (error) {
      update({ notice: `任务已提交，但会话记录暂时无法读取：${errorMessage(error)}` });
    }
  }
  if (!isJobActive(job.status)) applyJob(job);
}

export const workspaceStore = {
  getSnapshot: (): WorkspaceState => state,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  initialize(): Promise<void> {
    if (state.bootStatus === 'ready') return Promise.resolve();
    if (initialization) return initialization;
    const generation = ++initializationGeneration;
    initialization = (async () => {
      update({ bootStatus: 'loading', error: null });
      const outcomes = await Promise.allSettled([
        Promise.resolve().then(() => desktopApi().info()),
        Promise.resolve().then(() => desktopApi().config()),
        Promise.resolve().then(() => desktopApi().conversations()),
      ]);
      if (generation !== initializationGeneration) return;
      const [info, config, conversations] = outcomes;
      const failures: string[] = [];
      if (info.status === 'fulfilled') update({ serviceInfo: info.value }); else failures.push(errorMessage(info.reason));
      if (config.status === 'fulfilled') {
        update({ config: config.value, topK: config.value.default_top_k, executionMode: requestedExecutionMode(config.value.default_execution_mode) });
      } else failures.push(errorMessage(config.reason));
      if (conversations.status === 'fulfilled') updateConversationList(conversations.value); else failures.push(errorMessage(conversations.reason));
      update({ bootStatus: failures.length === outcomes.length ? 'error' : 'ready', error: failures.length ? failures.join('；') : null });
      if (conversations.status === 'fulfilled' && conversations.value.length) {
        await this.selectConversation(orderedConversations(conversations.value)[0].conversation_id);
      }
    })().finally(() => {
      if (generation === initializationGeneration) initialization = null;
    });
    return initialization;
  },
  retryInitialization(): Promise<void> {
    initializationGeneration += 1;
    initialization = null;
    update({ bootStatus: 'idle' });
    return this.initialize();
  },
  setAction(action: Action): void {
    update({ action });
  },
  setExecutionMode(executionMode: ExecutionMode): void {
    update({ executionMode });
  },
  setTopK(topK: number): void {
    if (Number.isInteger(topK) && topK >= 1 && topK <= 15) update({ topK });
  },
  setDraft(value: string, key = currentDraftKey()): void {
    if (state.drafts[key] === value) return;
    update({ drafts: { ...state.drafts, [key]: value } });
  },
  clearError(): void {
    update({ error: null, notice: null });
  },
  setError(error: string): void {
    update({ error });
  },
  setNotice(notice: string | null): void {
    update({ notice });
  },
  clearNotice(): void {
    update({ notice: null });
  },
  updateConfig(config: PublicConfig): void {
    update({ config, topK: config.default_top_k, executionMode: requestedExecutionMode(config.default_execution_mode), error: null, notice: '设置已保存。保存设置不会启动研究或模型请求。' });
  },
  newConversation(): void {
    selectionSequence += 1;
    update({ conversationId: null, conversation: null, activeJob: null, resultJob: null, loadingConversation: false, action: 'research', error: null, notice: null });
  },
  async selectConversation(id: string): Promise<void> {
    if (!id || (state.conversationId === id && state.conversation && !state.loadingConversation)) return;
    const sequence = ++selectionSequence;
    update({ conversationId: id, conversation: null, activeJob: null, loadingConversation: true, error: null, notice: null });
    try {
      const conversation = await desktopApi().conversation(id);
      if (sequence !== selectionSequence) return;
      const summary = state.conversations.find(item => item.conversation_id === id);
      const jobId = getLatestJobId(conversation, summary);
      let job: Job | null = null;
      let resultJob: Job | null = null;
      let notice: string | undefined;
      if (jobId) {
        try {
          job = await desktopApi().job(jobId);
          resultJob = job;
        } catch (error) {
          notice = `会话已恢复；关联任务状态暂时无法读取：${errorMessage(error)}`;
        }
      }
      if (job?.action === 'chat' && conversation.result_job_id && conversation.result_job_id !== job.job_id) {
        try {
          resultJob = await desktopApi().job(conversation.result_job_id);
        } catch (error) {
          notice = `研究资料关联任务暂时无法读取：${errorMessage(error)}`;
        }
      }
      installConversation(conversation, job, resultJob, sequence, notice);
    } catch (error) {
      if (sequence === selectionSequence) update({ loadingConversation: false, error: errorMessage(error) });
    }
  },
  async sendMessage(text: string): Promise<Job> {
    const trimmed = text.trim();
    if (!trimmed) throw new Error('请先输入内容。');
    if (state.sending || isJobActive(state.activeJob?.status)) throw new Error('当前任务尚未结束。');
    if (isReadOnlyConversation(state.conversation)) throw new Error('旧版记录仅供阅读。请新建会话继续；旧记录内容不会带入新会话。');
    const requestedConversationId = state.conversationId;
    const sequence = selectionSequence;
    const action = state.action;
    const draftKey = currentDraftKey();
    update({ sending: true, error: null, notice: null });
    let job: Job;
    try {
      job = await desktopApi().sendMessage({
        text: trimmed,
        conversation_id: requestedConversationId,
        action,
        top_k: action === 'research' ? state.topK : undefined,
        execution_mode: action === 'research' ? state.executionMode : undefined,
        mode: 'live',
      });
    } catch (error) {
      update({ sending: false, error: errorMessage(error) });
      throw error;
    }
    const returnedConversationId = job.conversation_id ?? job.conversation?.conversation_id ?? requestedConversationId;
    const acceptedDraftKey = returnedConversationId ?? draftKey;
    submittedByJob.set(job.job_id, { text: trimmed, draftKey: acceptedDraftKey, action });
    if (submittedByJob.size > 40) submittedByJob.delete(submittedByJob.keys().next().value as string);
    if (sequence !== selectionSequence) {
      update({
        sending: false,
        error: null,
        notice: `任务 ${job.job_id} 已在后台提交；切换会话不会取消它。`,
      });
      return job;
    }
    update({
      sending: false,
      activeJob: job,
      resultJob: job.action === 'research' ? job : state.resultJob,
      conversationId: returnedConversationId,
      conversation: job.conversation ?? (state.conversationId === requestedConversationId ? state.conversation : null),
      error: null,
      notice: job.conversation ? null : '任务已提交，正在同步服务端会话记录。',
    });
    this.setDraft('', draftKey);
    if (acceptedDraftKey !== draftKey) this.setDraft('', acceptedDraftKey);
    void linkAcceptedJob(job, requestedConversationId, sequence);
    if (isJobFinished(job.status)) applyJob(job);
    return job;
  },
  async refreshJob(jobId: string): Promise<Job> {
    try {
      const job = await desktopApi().job(jobId);
      applyJob(job);
      return job;
    } catch (error) {
      update({ error: errorMessage(error) });
      throw error;
    }
  },
  onJobEvent(jobId: string, event: Job['events'][number]): void {
    if (state.activeJob?.job_id !== jobId) return;
    const events = state.activeJob.events.filter(item => item.seq !== event.seq).concat(event).sort((left, right) => left.seq - right.seq);
    update({ activeJob: { ...state.activeJob, events } });
  },
  async cancelActiveJob(): Promise<void> {
    const job = state.activeJob;
    if (!job || !isJobActive(job.status)) return;
    try {
      applyJob(await desktopApi().cancelJob(job.job_id));
    } catch (error) {
      update({ error: errorMessage(error) });
    }
  },
  async refreshConversations(): Promise<void> {
    await refreshConversations();
  },
};

export function useWorkspaceState(): WorkspaceState {
  return useSyncExternalStore(workspaceStore.subscribe, workspaceStore.getSnapshot, workspaceStore.getSnapshot);
}

export { NEW_DRAFT_KEY };
