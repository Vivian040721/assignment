import { build } from 'esbuild';

await build({
  entryPoints: ['electron/main.ts', 'electron/preload.ts'],
  outdir: 'electron/dist', outExtension: { '.js': '.cjs' },
  bundle: true, platform: 'node', format: 'cjs', target: 'node22',
  external: ['electron'], sourcemap: false,
});
