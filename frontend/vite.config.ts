import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  // 仅在 Node 配置进程使用后端地址；不将根目录 .env 注入前端代码。
  const env = loadEnv(mode, fileURLToPath(new URL('..', import.meta.url)), '')
  return {
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': {
          target: `http://${env.BACKEND_HOST || '127.0.0.1'}:${env.BACKEND_PORT || '8200'}`,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
    preview: { host: '127.0.0.1', port: 4173, strictPort: true },
  }
})
