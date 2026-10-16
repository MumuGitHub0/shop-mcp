import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 构建产物由 FastAPI 挂载在 /admin（StaticFiles html=True），故 base 必须为 /admin/
export default defineConfig({
  plugins: [vue()],
  base: '/admin/',
  server: {
    port: 5173,
    proxy: {
      '/admin/api': 'http://127.0.0.1:8000',
    },
  },
})
