import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';
import type { DesktopAPI, JobEvent } from '../shared/contracts';

const JOB_EVENT_CHANNEL = 'jobs:event';

function makeSubscriptionId(): string {
  const bytes = new Uint8Array(16);
  try { globalThis.crypto.getRandomValues(bytes); }
  catch { for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256); }
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
}

const api: DesktopAPI = {
  info: () => ipcRenderer.invoke('service:info'),
  config: () => ipcRenderer.invoke('config:get'),
  saveConfig: (values) => ipcRenderer.invoke('config:put', values),
  conversations: () => ipcRenderer.invoke('conversations:list'),
  conversation: (id) => ipcRenderer.invoke('conversations:get', id),
  sendMessage: (values) => ipcRenderer.invoke('conversations:send', values),
  job: (id) => ipcRenderer.invoke('jobs:get', id),
  cancelJob: (id) => ipcRenderer.invoke('jobs:cancel', id),
  subscribeJob: (id, after, callback) => {
    if (typeof callback !== 'function') throw new TypeError('任务订阅需要事件回调。');
    const subscriptionId = makeSubscriptionId();
    let active = true;
    const listener = (_event: IpcRendererEvent, payload: unknown) => {
      if (!active || !payload || typeof payload !== 'object' || Array.isArray(payload)) return;
      const message = payload as { subscription_id?: unknown; value?: unknown };
      if (message.subscription_id !== subscriptionId || !message.value ||
          typeof message.value !== 'object' || Array.isArray(message.value)) return;
      try { callback(message.value as JobEvent); } catch { /* Renderer callbacks must not affect the bridge. */ }
    };
    ipcRenderer.on(JOB_EVENT_CHANNEL, listener);
    void ipcRenderer.invoke('jobs:subscribe', id, after, subscriptionId).catch(() => {
      if (!active) return;
      active = false;
      ipcRenderer.removeListener(JOB_EVENT_CHANNEL, listener);
      try { callback({ job_id: id, error: '任务进度订阅失败。' }); } catch { /* Ignore callback errors. */ }
    });
    return () => {
      if (!active) return;
      active = false;
      ipcRenderer.removeListener(JOB_EVENT_CHANNEL, listener);
      void ipcRenderer.invoke('jobs:unsubscribe', subscriptionId).catch(() => undefined);
    };
  },
  readArtifact: (jobId, artifactId) => ipcRenderer.invoke('artifacts:read', jobId, artifactId),
  openArtifact: (jobId, artifactId) => ipcRenderer.invoke('artifacts:open', jobId, artifactId),
  exportArtifact: (jobId, artifactId) => ipcRenderer.invoke('artifacts:export', jobId, artifactId),
  openExternal: (url) => ipcRenderer.invoke('external:open', url),
  openDataDirectory: () => ipcRenderer.invoke('data:open'),
  copyText: (text) => ipcRenderer.invoke('clipboard:write', text),
  loadBenchmark: () => ipcRenderer.invoke('benchmark:load'),
};

contextBridge.exposeInMainWorld('paperAgent', Object.freeze(api));
