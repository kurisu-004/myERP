// vitest 配置：运行纯函数和 WebSocket singleton 单测。
// 组件测试不在本项目范围内（项目不引入 testing-library）。
import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    include: ['src/**/*.spec.ts'],
    environment: 'node',
  },
})
