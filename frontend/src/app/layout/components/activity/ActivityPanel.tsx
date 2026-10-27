import { Clock3 } from 'lucide-react';
import { useWorkspaceState } from '../../../stores/workspaceStore';
import { redactSensitiveData } from '../../../utils/redactSensitive';
import { formatDate, formatDuration } from '../../../utils/format';
import { statusLabel } from '../../../utils/status';
import { errorHint, errorLabel, kindLabel, stageLabel } from '../../../utils/labels';
import { eventMessage, groupEvents, groupToolCalls } from '../../../utils/activity';

export function ActivityPanel() {
  const { activeJob, conversation } = useWorkspaceState();
  const job = activeJob;
  const result = job?.result ?? conversation?.result;
  const events = job?.events ?? [];
  const toolCalls = result?.tool_calls ?? [];
  const eventGroups = groupEvents(events);
  const toolGroups = groupToolCalls(toolCalls);
  const empty = !job && !result;
  if (empty) return <div className="panel-empty"><span><Clock3 size={20}/></span><strong>还没有任务活动</strong><p>发起研究后，服务端记录的阶段、错误与工具调用会显示在这里。</p></div>;
  return <div className="activity-view">
    <header className="panel-intro"><div><span className="eyebrow">任务记录</span><h2>活动</h2></div>{job && <span className={`status-chip status-${job.status}`}>{statusLabel(job.status)}</span>}</header>
    {job && <div className="job-facts"><span><small>任务 ID</small><code>{job.job_id}</code></span><span><small>更新时间</small>{formatDate(job.updated_at)}</span>{result?.run_id && <span><small>研究 run</small><code>{result.run_id}</code></span>}</div>}
    {job?.error && <div className="inline-error" role="alert">{job.error}</div>}
    {result?.errors?.length ? <section className="event-errors"><h3>错误与部分结果 <span>{result.errors.length}</span></h3>{result.errors.map((item, index) => <details className="activity-card activity-error" key={`${item.code ?? 'error'}:${index}`}>
      <summary><strong>{errorLabel(item)}</strong><span>{item.stage ? stageLabel(item.stage) : '任务'}</span></summary>
      <div className="activity-card-body">
        <p>{item.message}</p>
        <small>错误代码：{item.code ?? 'unknown'}</small>
        {item.code && <p className="activity-error-hint">{errorHint(item.code)}</p>}
        {item.details && Object.keys(item.details).length > 0 && <details className="activity-error-details"><summary>查看安全诊断定位</summary><pre>{JSON.stringify(redactSensitiveData(item.details), null, 2)}</pre></details>}
      </div>
    </details>)}</section> : null}
    {toolGroups.length ? <section className="event-section"><h3>Agent 工具调用 <span>{toolCalls.length}</span></h3>{toolGroups.map(group => <details className={`activity-card ${group.hasError ? 'is-error' : ''}`} key={group.tool} open={group.hasError || undefined}>
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
    {eventGroups.length ? <section className="event-section"><h3>服务端事件 <span>{events.length}</span></h3>{eventGroups.map(group => <details className={`activity-card ${group.hasError ? 'is-error' : ''}`} key={group.key} open={group.hasError || undefined}>
      <summary><strong>{group.label}</strong><span>{group.items.length} 条</span></summary>
      <div className="activity-card-body">
        <ol className="event-timeline">{group.items.map(event => <li key={`${job?.job_id ?? 'conversation'}:${event.seq}`} className={`event-${event.kind}`}>
          <span className="event-dot"/>
          <div className="event-copy"><div className="event-heading">{event.stage && <strong>{stageLabel(event.stage)}</strong>}<span>{eventMessage(event) || kindLabel(event.kind)}</span></div><time>{formatDate(event.timestamp)}</time>{event.data && Object.keys(event.data).length > 0 && <details><summary>查看事件数据</summary><pre>{JSON.stringify(redactSensitiveData(event.data), null, 2)}</pre></details>}</div>
        </li>)}</ol>
      </div>
    </details>)}</section> : null}
    {result?.timings?.length ? <section className="event-section"><h3>阶段计时 <span>{result.timings.length}</span></h3><details className="activity-card"><summary><strong>耗时明细</strong><span>{result.timings.length} 个阶段</span></summary><div className="activity-card-body"><ul className="timing-list">{result.timings.map((timing, index) => <li key={`${timing.stage}:${index}`}><span>{stageLabel(timing.stage)}</span><strong>{formatDuration(timing.duration_seconds ?? timing.wall_seconds)}</strong></li>)}</ul></div></details></section> : null}
    {!toolGroups.length && !eventGroups.length && !result?.timings?.length && <div className="panel-note">正在等待服务端记录任务事件…</div>}
  </div>;
}
