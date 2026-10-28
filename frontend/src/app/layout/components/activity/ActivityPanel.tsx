import { Clock3 } from 'lucide-react';
import { useWorkspaceState } from '../../../stores/workspaceStore';
import { redactSensitiveData } from '../../../utils/redactSensitive';
import { formatDate, formatDuration } from '../../../utils/format';
import { statusLabel } from '../../../utils/status';
import { errorHint, errorLabel, kindLabel, stageLabel } from '../../../utils/labels';
import { eventMessage, groupEvents, groupToolCalls } from '../../../utils/activity';
import { collectExecutionErrors, groupExecutionErrors } from '../../../utils/executionLog';
import { PartialResultSummary, RankingSummary, TargetCoverageSummary } from '../common/ResultInsights';

export function ActivityPanel() {
  const { activeJob, resultJob, conversation, config } = useWorkspaceState();
  const job = resultJob ?? activeJob;
  const result = job?.result ?? conversation?.result;
  const events = job?.events ?? [];
  const toolCalls = result?.tool_calls ?? [];
  const eventGroups = groupEvents(events);
  const toolGroups = groupToolCalls(toolCalls);
  const executionErrors = collectExecutionErrors(result);
  if (job?.error) executionErrors.push({ code: 'job_failed', stage: 'job', message: job.error });
  const errorGroups = groupExecutionErrors(executionErrors);
  const replacementAttempts = Array.isArray(result?.metrics?.replacement_attempts)
    ? result.metrics.replacement_attempts
    : [];
  const selectedPapers = result?.selected_papers ?? [];
  const empty = !job && !result;
  if (empty) return <div className="panel-empty"><span><Clock3 size={20}/></span><strong>还没有执行记录</strong><p>发起研究后，服务端记录的阶段、原始错误与工具调用会显示在这里。</p></div>;
  return <div className="activity-view">
    <header className="panel-intro"><div><span className="eyebrow">按需查看诊断</span><h2>执行记录</h2></div>{job && <span className={`status-chip status-${job.status}`}>{statusLabel(job.status)}</span>}</header>
    <p className="panel-subtitle">这里保留原始错误码、任务状态、排序、候选与审计信息。默认折叠，不影响前方的研究正文。</p>
    {job && <div className="job-facts"><span><small>任务 ID</small><code>{job.job_id}</code></span><span><small>更新时间</small>{formatDate(job.updated_at)}</span>{config?.model && <span><small>当前模型</small>{config.model}</span>}{result?.run_id && <span><small>研究 run</small><code>{result.run_id}</code></span>}</div>}
    <details className="execution-log">
      <summary>展开执行记录 <span>{errorGroups.length} 类错误 · {events.length} 条事件 · {toolCalls.length} 次工具调用</span></summary>
      <div className="execution-log-body">
        {result && <section className="event-section execution-result-audit"><h3>结果范围与排序</h3><RankingSummary result={result}/><TargetCoverageSummary result={result}/><PartialResultSummary result={result}/></section>}
        {result && (selectedPapers.length > 0 || replacementAttempts.length > 0) && <section className="event-section">
          <h3>原始选择与全文替补</h3>
          {selectedPapers.length > 0 && <details className="activity-card">
            <summary><strong>原始 LLM 精选</strong><span>{selectedPapers.length} 篇</span></summary>
            <div className="activity-card-body"><ul className="execution-paper-list">{selectedPapers.map(paper => <li key={paper.paper_id}><span>{paper.title}</span><small>名次 #{paper.rank ?? '—'}{typeof paper.final_score === 'number' ? ` · 终评分 ${paper.final_score.toFixed(3)}` : ''}</small></li>)}</ul></div>
          </details>}
          {replacementAttempts.length > 0 && <details className="activity-card">
            <summary><strong>全文替补轨迹</strong><span>{replacementAttempts.length} 次</span></summary>
            <div className="activity-card-body"><ul className="execution-paper-list">{replacementAttempts.map((attempt, index) => <li key={`${String(attempt.replacement_paper_id)}:${index}`}><span>{String(attempt.replacement_paper_id)}</span><small>{String(attempt.status ?? 'unknown')}{attempt.failure_code ? ` · ${String(attempt.failure_code)}` : ''}{attempt.summary_scope ? ` · ${String(attempt.summary_scope)}` : ''}</small></li>)}</ul></div>
          </details>}
        </section>}
        {errorGroups.length ? <section className="event-errors"><h3>原始错误 <span>{executionErrors.length} 条 / {errorGroups.length} 类</span></h3>{errorGroups.map(group => <details className="activity-card activity-error" key={group.key}>
          <summary><strong>{errorLabel(group.error)}</strong><span>{group.count > 1 ? `相同错误 × ${group.count}` : group.error.stage ? stageLabel(group.error.stage) : '任务'}</span></summary>
          <div className="activity-card-body">
            {group.items.map((item, index) => <div className="execution-error-item" key={`${item.code ?? 'error'}:${index}`}>
              {group.count > 1 && <small>第 {index + 1} 次记录</small>}
              <p>{item.message}</p>
              <small>错误代码：{item.code ?? 'unknown'}{item.stage ? ` · 阶段：${item.stage}` : ''}{item.paper_id ? ` · 论文：${item.paper_id}` : ''}</small>
              {item.code && <p className="activity-error-hint">{errorHint(item.code)}</p>}
              {item.details && Object.keys(item.details).length > 0 && <details className="activity-error-details"><summary>查看安全诊断定位</summary><pre>{JSON.stringify(redactSensitiveData(item.details), null, 2)}</pre></details>}
            </div>)}
          </div>
        </details>)}</section> : null}
        {toolGroups.length ? <section className="event-section"><h3>Agent 工具调用 <span>{toolCalls.length}</span></h3>{toolGroups.map(group => <details className={`activity-card ${group.hasError ? 'is-error' : ''}`} key={group.tool}>
          <summary><strong>{group.label}</strong><span>{group.items.length} 次 · {formatDuration(group.duration)}</span></summary>
          <div className="activity-card-body">
            {group.items.map((call, index) => <details className="activity-call" key={`${call.tool}:${index}`}>
              <summary><span>{statusLabel(call.status)}</span><small>{call.controller === 'scripted_demo' ? '演示脚本控制' : '模型工具调用'} · {formatDuration(call.duration_seconds)}</small></summary>
              <div className="activity-call-body">
                {call.error && <p className="activity-call-error">{call.error}</p>}
                {call.arguments && Object.keys(call.arguments).length > 0 && <details><summary>查看工具参数</summary><pre>{JSON.stringify(redactSensitiveData(call.arguments), null, 2)}</pre></details>}
              </div>
            </details>)}
          </div>
        </details>)}</section> : null}
        {eventGroups.length ? <section className="event-section"><h3>服务端事件 <span>{events.length}</span></h3>{eventGroups.map(group => <details className={`activity-card ${group.hasError ? 'is-error' : ''}`} key={group.key}>
          <summary><strong>{group.label}</strong><span>{group.items.length} 条</span></summary>
          <div className="activity-card-body">
            <ol className="event-timeline">{group.items.map(event => <li key={`${job?.job_id ?? 'conversation'}:${event.seq}`} className={`event-${event.kind}`}>
              <span className="event-dot"/>
              <div className="event-copy"><div className="event-heading">{event.stage && <strong>{stageLabel(event.stage)}</strong>}<span>{eventMessage(event) || kindLabel(event.kind)}</span></div><time>{formatDate(event.timestamp)}</time>{event.data && Object.keys(event.data).length > 0 && <details><summary>查看事件数据</summary><pre>{JSON.stringify(redactSensitiveData(event.data), null, 2)}</pre></details>}</div>
            </li>)}</ol>
          </div>
        </details>)}</section> : null}
        {result?.timings?.length ? <section className="event-section"><h3>阶段计时 <span>{result.timings.length}</span></h3><details className="activity-card"><summary><strong>耗时明细</strong><span>{result.timings.length} 个阶段</span></summary><div className="activity-card-body"><ul className="timing-list">{result.timings.map((timing, index) => <li key={`${timing.stage}:${index}`}><span>{stageLabel(timing.stage)}</span><strong>{formatDuration(timing.duration_seconds ?? timing.wall_seconds)}</strong></li>)}</ul></div></details></section> : null}
        {!errorGroups.length && !toolGroups.length && !eventGroups.length && !result?.timings?.length && <div className="panel-note">正在等待服务端记录任务事件…</div>}
      </div>
    </details>
  </div>;
}
