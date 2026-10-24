import { useEffect, useState } from 'react';
import { Check, Eye, EyeOff, FolderOpen, LoaderCircle, LockKeyhole, Save, ShieldCheck, Trash2 } from 'lucide-react';
import type { ConfigUpdate, PublicConfig } from '../../../types/contracts';
import { desktopApi } from '../../../services/desktopApi';
import { useWorkspaceState, workspaceStore } from '../../../stores/workspaceStore';

export function SettingsPanel() {
  const { config, serviceInfo } = useWorkspaceState();
  const [baseUrl, setBaseUrl] = useState('https://api.deepseek.com');
  const [model, setModel] = useState('deepseek-flash');
  const [proxy, setProxy] = useState('');
  const [topK, setTopK] = useState(5);
  const [concurrency, setConcurrency] = useState(3);
  const [llmTimeout, setLlmTimeout] = useState(180);
  const [httpTimeout, setHttpTimeout] = useState(30);
  const [thinking, setThinking] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [openAlexKey, setOpenAlexKey] = useState('');
  const [clearApiKey, setClearApiKey] = useState(false);
  const [clearOpenAlexKey, setClearOpenAlexKey] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [showOpenAlexKey, setShowOpenAlexKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!config) return;
    setBaseUrl(config.base_url);
    setModel(config.model);
    setProxy(config.http_proxy);
    setTopK(config.default_top_k);
    setConcurrency(config.concurrency);
    setLlmTimeout(config.llm_timeout);
    setHttpTimeout(config.http_timeout);
    setThinking(config.thinking_enabled);
    setApiKey('');
    setOpenAlexKey('');
    setClearApiKey(false);
    setClearOpenAlexKey(false);
  }, [config]);

  const save = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!baseUrl.trim() || !model.trim()) {
      setError('请填写 API Base URL 和模型名称。');
      return;
    }
    setSaving(true);
    setError(null);
    const values: ConfigUpdate = {
      base_url: baseUrl.trim(),
      model: model.trim(),
      http_proxy: proxy.trim(),
      default_top_k: topK,
      concurrency,
      llm_timeout: llmTimeout,
      http_timeout: httpTimeout,
      thinking_enabled: thinking,
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      ...(openAlexKey.trim() ? { openalex_api_key: openAlexKey.trim() } : {}),
      ...(clearApiKey ? { clear_api_key: true } : {}),
      ...(clearOpenAlexKey ? { clear_openalex_api_key: true } : {}),
    };
    try {
      const next: PublicConfig = await desktopApi().saveConfig(values);
      workspaceStore.updateConfig(next);
      setApiKey('');
      setOpenAlexKey('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  const openDataDirectory = async () => {
    try {
      await desktopApi().openDataDirectory();
    } catch (reason) {
      workspaceStore.setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  if (!config) return <div className="panel-empty"><span><LockKeyhole size={20}/></span><strong>配置暂不可用</strong><p>桌面服务加载配置失败。请查看页面错误提示后重试。</p></div>;

  return <div className="settings-view">
    <header className="panel-intro"><div><span className="eyebrow">本机配置</span><h2>设置</h2></div><span className="settings-safe"><ShieldCheck size={14}/>密钥不回显</span></header>
    <p className="panel-subtitle">保存连接参数不会验证密钥，也不会启动研究任务。</p>
    <section className="settings-section">
      <div className="settings-section-head"><div><h3>DeepSeek</h3><p>研究与讨论使用的模型服务。</p></div><span className={`configured-pill ${config.api_key_configured ? 'is-ready' : ''}`}><i/>{config.api_key_configured ? '已配置' : '待配置'}</span></div>
      <form className="settings-form" onSubmit={event => void save(event)}>
        <label className="field"><span>API Base URL</span><input value={baseUrl} onChange={event => setBaseUrl(event.target.value)} autoComplete="url"/></label>
        <label className="field"><span>模型名称</span><input value={model} onChange={event => setModel(event.target.value)} placeholder="例如 deepseek-flash"/></label>
        <label className="field"><span>API Key <small>{config.api_key_configured ? '已保存，留空则保留' : '尚未配置'}</small></span><span className="secret-field"><input type={showApiKey ? 'text' : 'password'} value={apiKey} onChange={event => { setApiKey(event.target.value); if (event.target.value) setClearApiKey(false); }} autoComplete="new-password" placeholder="输入新密钥"/><button type="button" className="field-icon" aria-label={showApiKey ? '隐藏密钥' : '显示密钥'} onClick={() => setShowApiKey(value => !value)}>{showApiKey ? <EyeOff size={15}/> : <Eye size={15}/>}</button></span></label>
        <label className="clear-key"><input type="checkbox" checked={clearApiKey} onChange={event => { setClearApiKey(event.target.checked); if (event.target.checked) setApiKey(''); }}/><Trash2 size={14}/>明确清除已保存的 DeepSeek Key</label>
        <label className="field"><span>HTTP Proxy</span><input value={proxy} onChange={event => setProxy(event.target.value)} placeholder="留空表示不使用代理" autoComplete="url"/></label>
        <label className="toggle-field"><span><strong>思考模式</strong><small>按服务端模型配置启用。</small></span><input type="checkbox" checked={thinking} onChange={event => setThinking(event.target.checked)}/></label>
        <div className="settings-grid">
          <label className="field"><span>默认 Top-k</span><input type="number" min={1} max={15} value={topK} onChange={event => setTopK(Number(event.target.value))}/></label>
          <label className="field"><span>并发数</span><input type="number" min={1} max={8} value={concurrency} onChange={event => setConcurrency(Number(event.target.value))}/></label>
          <label className="field"><span>模型超时（秒）</span><input type="number" min={5} max={600} value={llmTimeout} onChange={event => setLlmTimeout(Number(event.target.value))}/></label>
          <label className="field"><span>HTTP 超时（秒）</span><input type="number" min={5} max={300} value={httpTimeout} onChange={event => setHttpTimeout(Number(event.target.value))}/></label>
        </div>
        {error && <div className="inline-error" role="alert">{error}</div>}
        <button className="primary-button save-settings" type="submit" disabled={saving}>{saving ? <LoaderCircle size={15} className="spin"/> : <Save size={15}/>}保存 DeepSeek 设置</button>
      </form>
    </section>
    <section className="settings-section">
      <div className="settings-section-head"><div><h3>OpenAlex</h3><p>用于学术元数据检索的独立凭据。</p></div><span className={`configured-pill ${config.openalex_api_key_configured ? 'is-ready' : ''}`}><i/>{config.openalex_api_key_configured ? '已配置' : '待配置'}</span></div>
      <form className="settings-form" onSubmit={event => void save(event)}>
        <label className="field"><span>OpenAlex API Key <small>{config.openalex_api_key_configured ? '已保存，留空则保留' : '尚未配置'}</small></span><span className="secret-field"><input type={showOpenAlexKey ? 'text' : 'password'} value={openAlexKey} onChange={event => { setOpenAlexKey(event.target.value); if (event.target.value) setClearOpenAlexKey(false); }} autoComplete="new-password" placeholder="输入新密钥"/><button type="button" className="field-icon" aria-label={showOpenAlexKey ? '隐藏密钥' : '显示密钥'} onClick={() => setShowOpenAlexKey(value => !value)}>{showOpenAlexKey ? <EyeOff size={15}/> : <Eye size={15}/>}</button></span></label>
        <label className="clear-key"><input type="checkbox" checked={clearOpenAlexKey} onChange={event => { setClearOpenAlexKey(event.target.checked); if (event.target.checked) setOpenAlexKey(''); }}/><Trash2 size={14}/>明确清除已保存的 OpenAlex Key</label>
        <button className="secondary-button" type="submit" disabled={saving}>{saving ? <LoaderCircle size={14} className="spin"/> : <Check size={14}/>}保存 OpenAlex 设置</button>
      </form>
    </section>
    <section className="settings-section settings-service">
      <div className="settings-section-head"><div><h3>桌面服务</h3><p>本机服务状态与研究数据位置。</p></div><button className="subtle-button" type="button" onClick={() => void openDataDirectory()}><FolderOpen size={14}/>打开数据目录</button></div>
      {serviceInfo ? <dl className="service-facts"><div><dt>版本</dt><dd>{serviceInfo.version} · API v{serviceInfo.api_version}</dd></div><div><dt>运行中任务</dt><dd>{serviceInfo.active_jobs}</dd></div><div><dt>数据目录</dt><dd title={serviceInfo.data_root}>{serviceInfo.data_root}</dd></div></dl> : <p className="panel-note">服务信息暂不可用。</p>}
    </section>
  </div>;
}
