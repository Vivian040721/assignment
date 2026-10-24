import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { randomBytes } from 'node:crypto';

export default defineConfig(({ command }) => {
  const nonce = command === 'serve' ? randomBytes(18).toString('base64') : undefined;
  return {
    base: './',
    html: { cspNonce: nonce },
    plugins: [react(), {
      name: 'desktop-development-csp',
      transformIndexHtml: {
        order: 'pre',
        handler: html => nonce
          ? html.replace("script-src 'self';", `script-src 'self' 'nonce-${nonce}';`)
          : html,
      },
    }],
  };
});
