import { BookOpen, ChevronLeft, ChevronRight, Clock3, FilePlus2, History, Settings2 } from 'lucide-react';
import { useState } from 'react';
import { useWorkspaceState, workspaceStore } from '../../../stores/workspaceStore';
import { layoutStore, useLayoutState } from '../../stores/layoutStore';
import { formatDate } from '../../../utils/format';
import { statusLabel } from '../../../utils/status';

export function Sidebar({ compact }: { compact: boolean }) {
  const workspace = useWorkspaceState();
  const layout = useLayoutState();
  const [filter, setFilter] = useState('');
  const conversations = workspace.conversations.filter(item => item.title.toLocaleLowerCase().includes(filter.toLocaleLowerCase()));
  const selectedId = workspace.conversationId;
  const selectedReadOnly = (workspace.conversation as (typeof workspace.conversation & { read_only?: boolean }))?.read_only === true;
  const shortMode = compact || layout.sidebarCollapsed;

  return <aside id="app-sidebar" className={`sidebar ${shortMode ? 'sidebar-compact' : ''}`} aria-label="会话导航">
    <div className="sidebar-brand">
      <span className="brand-mark" aria-hidden="true"><BookOpen size={18}/></span>
      {!shortMode && <span className="brand-copy"><strong>Paper Agent</strong><small>研究工作台</small></span>}
      {!compact && !layout.sidebarCollapsed && <button className="icon-button collapse-sidebar" type="button" aria-label="收起侧栏" title="收起侧栏" aria-expanded="true" aria-controls="app-sidebar" onClick={() => layoutStore.toggleSidebar()}><ChevronLeft size={16}/></button>}
    </div>
    <button className="new-conversation" type="button" title="新建会话" disabled={workspace.sending} onClick={() => workspaceStore.newConversation()}>
      <FilePlus2 size={17}/>{!shortMode && <span>新建会话</span>}
    </button>
    {!shortMode && <>
      <div className="sidebar-section-label"><span>最近会话</span><History size={14}/></div>
      <label className="conversation-search"><History size={14}/><input aria-label="搜索会话" placeholder="搜索会话" value={filter} onChange={event => setFilter(event.target.value)}/></label>
    </>}
    <nav className="conversation-list" aria-label="最近会话">
      {!workspace.conversations.length && !workspace.loadingConversation && <p className="sidebar-empty">还没有保存的会话。新建会话不会提交研究。</p>}
      {conversations.map(item => {
        const readOnly = (item as typeof item & { read_only?: boolean }).read_only === true;
        return <button key={item.conversation_id} className={`conversation-row ${selectedId === item.conversation_id ? 'is-selected' : ''}`} type="button" title={`${item.title} · ${statusLabel(item.status)}${readOnly ? ' · 旧版只读' : ''}`} aria-current={selectedId === item.conversation_id ? 'page' : undefined} onClick={() => void workspaceStore.selectConversation(item.conversation_id)}>
          <span className={`conversation-status status-${item.status}`} aria-hidden="true"><i/></span>
          <span className="conversation-row-copy"><strong>{item.title}</strong>{!shortMode && <small>{readOnly ? '旧版记录 · 只读' : formatDate(item.updated_at)}</small>}</span>
          {!shortMode && item.status === 'running' && <span className="running-label">运行中</span>}
        </button>;
      })}
      {filter && conversations.length === 0 && !shortMode && <p className="sidebar-empty">没有匹配的会话。</p>}
    </nav>
    <div className="sidebar-footer">
      {selectedReadOnly && !shortMode && <p className="legacy-indicator"><Clock3 size={13}/>当前为旧版只读记录</p>}
      {!compact && layout.sidebarCollapsed && <button className="sidebar-rail-toggle" type="button" aria-label="展开侧栏" title="展开侧栏" aria-expanded="false" aria-controls="app-sidebar" onClick={() => layoutStore.toggleSidebar()}><ChevronRight size={17}/></button>}
      <button className="sidebar-settings" type="button" title="设置" onClick={() => layoutStore.openPanel('settings')}><Settings2 size={17}/>{!shortMode && <><span>设置</span><i className={`config-indicator ${workspace.config?.api_key_configured ? 'is-ready' : ''}`}/></>}</button>
      {!shortMode && <div className="service-identity"><span className="service-dot"/><span><strong>本机研究空间</strong><small>{workspace.config?.model ?? '服务状态读取中'}</small></span></div>}
    </div>
  </aside>;
}
