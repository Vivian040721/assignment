import { useLayoutEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, BookOpen, CircleAlert, Clock3, FileText, LoaderCircle, PanelRightClose, PanelRightOpen, Search, ShieldCheck, Square, Sparkles } from 'lucide-react';
import type { Action, ExecutionMode, Message } from '../../../types/contracts';
import { isReadOnlyConversation } from '../../../types/contracts';
import { useWorkspaceState, workspaceStore } from '../../../stores/workspaceStore';
import { isJobActive, statusLabel } from '../../../utils/status';
import { formatDate } from '../../../utils/format';
import { kindLabel, stageLabel } from '../../../utils/labels';
import { isTerminalResult } from '../../../utils/papers';
import { publicEventMessage } from '../../../utils/activity';
import { deliveryStatusFor, reportArtifactFor } from '../../../utils/researchBrief';
import { layoutStore, useLayoutState } from '../../stores/layoutStore';
import { MarkdownContent } from '../common/MarkdownContent';
import { ResearchBriefView } from '../common/ResearchBriefView';

const suggestions = [
  '长期记忆如何降低 Agent 的推理成本？',
  '比较稀疏检索与稠密检索在 RAG 中的适用场景。',
  '总结多智能体协作中的任务分解与协调方法。',
];

export function Conversation() {
  const state = useWorkspaceState();
  const layout = useLayoutState();
  const readOnly = isReadOnlyConversation(state.conversation);
  const jobActive = isJobActive(state.activeJob?.status);
  const draftKey = state.conversationId ?? '__new_conversation__';
  const draft = state.drafts[draftKey] ?? '';
  const messages = state.conversation?.messages ?? [];
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const resultAnchorRef = useRef<HTMLDivElement | null>(null);
  const lastTerminalAnchor = useRef('');
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const isNearBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const title = state.conversation?.title ?? (state.conversationId ? '载入会话…' : '新研究');
  const charCount = Array.from(draft).length;
  const canSubmit = draft.trim().length >= (state.action === 'research' ? 3 : 1) && !state.loadingConversation && !readOnly && !jobActive && !state.sending;
  const lastEvent = state.activeJob?.events.at(-1);
  const liveEvents = (state.activeJob?.events ?? [])
    .filter(event => event.kind !== 'error' && event.stage !== 'error')
    .slice(-4);
  const resultOwner = state.resultJob ?? state.activeJob;
  const result = resultOwner?.result ?? state.conversation?.result;
  const terminalKey = result && !jobActive && isTerminalResult(result)
    ? `${resultOwner?.job_id ?? 'conversation'}:${result.run_id ?? 'result'}`
    : '';
  const visibleMessages = terminalKey ? messages.filter(message => message.role !== 'status') : messages;

  useLayoutEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const updatePreference = () => setReducedMotion(query.matches);
    updatePreference();
    query.addEventListener('change', updatePreference);
    return () => query.removeEventListener('change', updatePreference);
  }, []);

  useLayoutEffect(() => {
    const textarea = composerRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    const next = Math.min(190, Math.max(72, textarea.scrollHeight));
    textarea.style.height = `${next}px`;
    textarea.style.overflowY = textarea.scrollHeight > 190 ? 'auto' : 'hidden';
  }, [draft]);

  useLayoutEffect(() => {
    const timeline = timelineRef.current;
    if (!timeline) return;
    if (terminalKey) {
      if (lastTerminalAnchor.current !== terminalKey) {
        lastTerminalAnchor.current = terminalKey;
        isNearBottom.current = false;
        setShowJump(false);
        requestAnimationFrame(() => {
          const anchor = resultAnchorRef.current;
          if (!anchor) return;
          const offset = anchor.getBoundingClientRect().top - timeline.getBoundingClientRect().top + timeline.scrollTop - 10;
          timeline.scrollTo({ top: Math.max(0, offset) });
        });
      }
      return;
    }
    lastTerminalAnchor.current = '';
    if (messages.length === 0 && !state.activeJob) {
      // The welcome surface fills the viewport; scrolling it to the bottom
      // hides its heading and suggestions.
      timeline.scrollTo({ top: 0 });
      setShowJump(false);
      return;
    }
    if (isNearBottom.current) {
      timeline.scrollTo({ top: timeline.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' });
      setShowJump(false);
    } else {
      setShowJump(true);
    }
  }, [state.conversationId, messages.length, messages.at(-1)?.content.length, state.activeJob?.updated_at, lastEvent?.seq, reducedMotion, terminalKey]);

  const submit = async () => {
    if (!canSubmit) return;
    if (!state.config?.api_key_configured) {
      layoutStore.openPanel('settings');
      workspaceStore.setNotice('请在设置中配置 DeepSeek Key。当前输入会保留，打开设置不会提交任务。');
      return;
    }
    try {
      await workspaceStore.sendMessage(draft);
      isNearBottom.current = true;
    } catch {
      // The store retains the draft and surfaces the service error.
    }
  };

  const chooseAction = (action: Action) => workspaceStore.setAction(action);
  const keyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.repeat || event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    event.preventDefault();
    void submit();
  };

  return <main className="conversation-shell" aria-label="研究对话">
    <header className="conversation-header">
      <div className="conversation-heading"><span className="header-kicker">研究空间</span><h1 title={title}>{title}</h1></div>
      <div className="header-controls">
        <button className="icon-button dock-toggle" type="button" aria-label={layout.dockOpen ? '收起资料 Dock' : '打开资料 Dock'} title={layout.dockOpen ? '收起资料 Dock' : '打开资料 Dock'} onClick={() => layoutStore.toggleDock()}>{layout.dockOpen ? <PanelRightClose size={17}/> : <PanelRightOpen size={17}/>}</button>
      </div>
    </header>
    {state.bootStatus === 'error' && <div className="connection-banner" role="alert"><CircleAlert size={16}/><span>{state.error ?? '桌面服务连接失败。'}</span><button type="button" onClick={() => void workspaceStore.retryInitialization()}>重试连接</button></div>}
    {state.conversation && readOnly && <div className="legacy-banner" role="note"><ShieldCheck size={16}/><span>旧版记录仅供阅读，可新建会话继续。旧消息、任务和上下文不会带入新会话。</span><button type="button" onClick={() => workspaceStore.newConversation()}>新建会话</button></div>}
    {state.notice && <div className="notice-banner" role="status"><span>{state.notice}</span><button type="button" aria-label="关闭提示" onClick={() => workspaceStore.clearNotice()}>×</button></div>}

    <div className="timeline-scroll" ref={timelineRef} onScroll={event => {
      const element = event.currentTarget;
      isNearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
      if (isNearBottom.current) setShowJump(false);
    }}>
      {state.loadingConversation && <div className="timeline-loading" aria-live="polite"><span className="skeleton-line wide"/><span className="skeleton-line"/><span className="skeleton-line medium"/></div>}
      {!state.loadingConversation && messages.length === 0 && !state.activeJob && <Welcome onChoose={value => workspaceStore.setDraft(value)}/>}
      <div className="message-thread">
        {!state.loadingConversation && visibleMessages.map((message, index) => <MessageRow key={message.id || `${message.role}:${index}`} message={message}/>) }
        {state.activeJob && jobActive && <div className="job-state-card"><div className={`job-state-icon status-${state.activeJob.status}`}><LoaderCircle size={16} className="spin"/></div><div><strong>{state.activeJob.action === 'chat' ? '讨论任务' : '研究任务'} · {statusLabel(state.activeJob.status)}</strong><p>{state.activeJob.idea}</p></div></div>}
        {state.activeJob && jobActive && <section className="live-progress" aria-label="任务实时进度">
          <div className="live-progress-head"><span className="live-dot is-live"/><strong>实时进度</strong><span>{liveEvents.length ? `最近 ${liveEvents.length} 条` : '等待事件'}</span></div>
          {liveEvents.length ? <ol role="log" aria-live="polite" aria-relevant="additions text">{liveEvents.map(event => <li key={`${event.seq}:${event.kind}`}>
            <span className="live-stage">{event.stage ? stageLabel(event.stage) : kindLabel(event.kind)}</span>
            <span className="live-message">{publicEventMessage(event) || (event.kind === 'trace' ? '阶段记录' : kindLabel(event.kind))}</span>
            <time>{formatDate(event.timestamp)}</time>
          </li>)}</ol> : <p>正在等待服务端记录进度…</p>}
        </section>}
        {result && <div className="reading-anchor" ref={resultAnchorRef} aria-hidden="true"/>}
        {result && <ResearchBriefView result={result} job={resultOwner}/>}
        {result && !result.brief && !deliveryStatusFor(result, resultOwner) && (reportArtifactFor(result, resultOwner?.artifacts ?? []) || result.report?.markdown) && <button className="result-link" type="button" onClick={() => layoutStore.openPanel('report')}><FileText size={16}/><span>阅读旧记录中的完整研究报告</span><span className="result-paper-count">{result.selected_papers.length} 篇精选论文</span><ArrowUp size={14}/></button>}
        {state.activeJob?.error && !result && <div className="job-error" role="alert"><CircleAlert size={16}/><span>任务未能完成，已有资料和执行记录已保留。</span></div>}
      </div>
    </div>
    {showJump && <button className="jump-latest" type="button" onClick={() => { isNearBottom.current = true; timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' }); setShowJump(false); }}><ArrowDown size={14}/>跳到最新</button>}

    <footer className="composer-area">
      {!readOnly && <div className="mode-switch" role="tablist" aria-label="消息类型">
        <button type="button" role="tab" aria-selected={state.action === 'research'} className={state.action === 'research' ? 'is-active' : ''} disabled={jobActive || state.sending} onClick={() => chooseAction('research')}><Search size={14}/>研究</button>
        <button type="button" role="tab" aria-selected={state.action === 'chat'} className={state.action === 'chat' ? 'is-active' : ''} disabled={jobActive || state.sending} onClick={() => chooseAction('chat')}><Sparkles size={14}/>讨论</button>
      </div>}
      <form className={`composer ${readOnly ? 'is-disabled' : ''}`} aria-busy={state.sending || jobActive} onSubmit={event => { event.preventDefault(); void submit(); }}>
        <textarea ref={composerRef} aria-label={readOnly ? '旧版记录只读' : state.action === 'research' ? '研究问题' : '讨论消息'} aria-describedby="composer-hint composer-count" enterKeyHint="send" maxLength={12000} value={draft} onChange={event => workspaceStore.setDraft(event.target.value)} onKeyDown={keyDown} placeholder={readOnly ? '旧版记录仅供阅读' : jobActive ? '任务进行中；可以先输入下一条，完成后再发送。' : state.action === 'research' ? '描述你想研究的问题…' : '继续讨论当前研究…'} rows={3} disabled={state.loadingConversation || readOnly}/>
        <div className="composer-controls">
          <div className="composer-options">{state.action === 'research' && <>
            <label>精选论文 <select aria-label="精选论文数量" value={state.topK} disabled={jobActive || state.sending} onChange={event => workspaceStore.setTopK(Number(event.target.value))}>{![3, 5, 8].includes(state.topK) && <option value={state.topK}>其他 {state.topK} 篇</option>}<option value={3}>3 篇</option><option value={5}>5 篇</option><option value={8}>8 篇</option></select></label>
            <details className="advanced-research-settings">
              <summary>高级设置</summary>
              <div className="advanced-settings-popover">
                <label>执行方式 <select aria-label="研究执行方式" value={state.executionMode} disabled={jobActive || state.sending} onChange={event => workspaceStore.setExecutionMode(event.target.value as ExecutionMode)}><option value="auto">自动</option><option value="parallel">并行</option><option value="sequential">顺序</option></select></label>
                <small>自动由服务端按真实配置选择；前端不假定实际执行方式。</small>
                <label>其他精选数量 <input aria-label="其他精选论文数量" type="number" min={1} max={15} value={state.topK} disabled={jobActive || state.sending} onChange={event => workspaceStore.setTopK(Number(event.target.value))}/></label>
                <small>推荐使用 3、5 或 8 篇；旧记录中的其他合法数量仍可保留。</small>
              </div>
            </details>
          </>}
          {!state.config?.api_key_configured && <span className="key-needed"><ShieldCheck size={13}/>需配置密钥</span>}</div>
          <div className="send-actions">
            {jobActive && <button className="cancel-job" type="button" onClick={() => void workspaceStore.cancelActiveJob()}><Square size={12} fill="currentColor"/>取消任务</button>}
            <button className="send-message" type="submit" disabled={!canSubmit} aria-label={state.action === 'research' ? '开始研究' : '发送消息'} title={state.action === 'research' ? (draft.trim().length < 3 ? '研究问题至少需要 3 个字符' : '开始研究') : '发送消息'}>{state.sending ? <LoaderCircle size={17} className="spin"/> : <ArrowUp size={18}/>}</button>
          </div>
        </div>
      </form>
      <div className="composer-caption"><span id="composer-hint">{readOnly ? '这条旧会话不会被修改。' : state.sending ? '正在提交…' : jobActive ? '任务运行中，进度会显示在对话中。' : 'Enter 发送 · Shift+Enter 换行'}</span><span id="composer-count" className={charCount > 10800 ? 'is-near-limit' : ''}>{charCount}/12000</span></div>
      {readOnly && <button className="read-only-new" type="button" onClick={() => workspaceStore.newConversation()}>新建会话继续</button>}
    </footer>
  </main>;
}

function Welcome({ onChoose }: { onChoose: (value: string) => void }) {
  return <section className="welcome-state"><div className="welcome-mark"><BookOpen size={19}/></div><h2>从一个研究问题开始</h2><p>检索论文、保留全文与替补来源，再把结果整理成可继续讨论的研究记录。</p><div className="suggestion-list">{suggestions.map((item, index) => <button className="suggestion" type="button" key={item} onClick={() => onChoose(item)}><span>{index === 0 ? <BookOpen size={16}/> : index === 1 ? <Search size={16}/> : <FileText size={16}/>}</span><span>{item}</span><ArrowUp size={14}/></button>)}</div><small>点击示例只会填入输入框，不会自动提交任务。</small></section>;
}

function MessageRow({ message }: { message: Message }) {
  if (message.role === 'user') return <article className="message-row message-user"><div className="message-content user-message-content">{message.content}</div>{message.created_at && <time>{formatDate(message.created_at)}</time>}</article>;
  if (message.role === 'assistant') return <article className="message-row message-assistant"><div className="assistant-mark"><Sparkles size={15}/></div><div className="message-content"><MarkdownContent text={message.content}/></div>{message.created_at && <time>{formatDate(message.created_at)}</time>}</article>;
  return <article className="message-row message-status"><span className="status-message-mark"><Clock3 size={13}/></span><div className="message-content"><span className="message-kind">{kindLabel(message.kind)}</span>{message.content}</div>{message.status && <span className={`status-chip status-${message.status}`}>{statusLabel(message.status)}</span>}</article>;
}
