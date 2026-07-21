import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
      dts: 'src/auto-imports.d.ts',
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: 'src/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  // 显式 include 重依赖：避免 vite 进入 dep discover 模式 → 浏览器点新页面时不再触发
  // "[optimizer] bundling dependencies..." + full-reload。
  optimizeDeps: {
    include: [
      'vue',
      'vue-router',
      'pinia',
      'axios',
      'element-plus',
      '@element-plus/icons-vue',
      'xlsx',
    ],
  },
  css: {
    preprocessorOptions: {
      scss: {
        api: 'modern-compiler',
        // 向每个 <style lang="scss"> 注入断点 mixin（from/until），组件内可直接 @include
        additionalData: `@use "@/styles/breakpoints" as *;\n`,
      },
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
