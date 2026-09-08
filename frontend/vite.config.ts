/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import { ServerResponse } from 'node:http'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// E2 后端默认 8000 端口;开发期用 VITE_API_PROXY_TARGET 覆盖。
// E2 未合并期间前端走 mock 模式(VITE_USE_MOCK=1),代理仅在真实联调时生效。
const proxyTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
        // 12-IA 审核 P0 根修:后端未就绪/连接竞争时 http-proxy 默认无限悬挂,
        // 客户端只见 loading;对 proxy error 显式回 502 让前端超时/错误态接管。
        configure: (proxy) => {
          proxy.on('error', (_err, _req, res) => {
            if (res instanceof ServerResponse && !res.headersSent) {
              res.writeHead(502, { 'Content-Type': 'application/json' })
              res.end(JSON.stringify({ detail: 'backend-unreachable' }))
            }
          })
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './vitest.setup.ts',
    css: false,
  },
})
