import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn.mjs'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import './styles/index.scss'

const app = createApp(App)

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// 2026-07-10 起：refresh token 失效 / 40102 兜底都走这个事件统一跳登录页。
// axios 响应拦截器（http.ts）会 dispatch；这里只负责导航，避免拦截器反向依赖 vue-router。
window.addEventListener('auth:logout', () => {
  // 拦截器失败分支已经清掉 localStorage；这里只负责跳转。
  router.replace('/login')
})

app.mount('#app')
