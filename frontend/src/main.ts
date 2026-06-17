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

// 启动时如果有 token, 先拉一次 me (失败则清掉)
const { useAuthStore } = await import('./store/auth')
const auth = useAuthStore()

if (import.meta.env.VITE_USE_DUMMY_AUTH === 'true') {
  // 开发调试模式: 跳过登录页与后端,直接注入 dev 会话
  auth.initDevSession()
  router.push('/')
} else if (auth.token) {
  try {
    await auth.loadMe()
  } catch {
    auth.logout()
  }
}

app.mount('#app')
