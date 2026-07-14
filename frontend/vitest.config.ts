// vitest 配置：仅跑 frontend/src/utils/__tests__/ 下的纯函数单测。
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
    include: ['src/utils/__tests__/**/*.spec.ts'],
    environment: 'node',
  },
})
