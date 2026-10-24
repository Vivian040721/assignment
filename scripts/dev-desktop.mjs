import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createServer } from 'vite';
import { build } from 'esbuild';
import electron from 'electron';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(root);
const server = await createServer({ configFile: path.join(root, 'frontend/vite.config.ts'),
  root: path.join(root, 'frontend'), server: { host: '127.0.0.1', port: 5173, strictPort: true } });
await server.listen();
await build({ entryPoints: ['electron/main.ts','electron/preload.ts'], outdir:'electron/dist',
  outExtension:{'.js':'.cjs'}, bundle:true, platform:'node', format:'cjs', target:'node22', external:['electron'] });
const child = spawn(electron, ['.'], { cwd:root, stdio:'inherit',
  env:{...process.env, PAPER_AGENT_RENDERER_URL:'http://127.0.0.1:5173'} });
console.log('Vite HMR + Electron ready. GUI reloads leave Python jobs running.');
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  if (child.exitCode === null) child.kill();
  await server.close();
}
process.on('SIGINT', () => { void stop(); });
process.on('SIGTERM', () => { void stop(); });
child.on('exit', async code => { await stop(); process.exitCode = code ?? 0; });
child.on('error', async () => { await stop(); process.exitCode = 1; });
