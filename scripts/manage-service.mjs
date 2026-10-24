import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const action = process.argv[2] || 'status';
if (!['start','status','stop'].includes(action)) throw new Error('Expected start, status or stop');
const python = process.env.PAPER_AGENT_PYTHON || path.join(root, '.venv-v2', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const worker = path.join(root,'data/release/backend/PaperAgent-service',process.platform === 'win32' ? 'PaperAgent-service.exe' : 'PaperAgent-service');
const result = existsSync(python)
  ? spawnSync(python,['-X','utf8','-m','paper_agent.service_control',action],{cwd:root,stdio:'inherit',windowsHide:true})
  : spawnSync(worker,['--service-control',action],{cwd:root,stdio:'inherit',windowsHide:true});
process.exitCode = result.status ?? 1;
