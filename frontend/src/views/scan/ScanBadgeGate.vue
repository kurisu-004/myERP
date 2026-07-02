<!--
  ScanBadgeGate.vue

  /scan/badge：工位扫码台入口。等待工人扫工牌；识别成功后跳到 /scan/action。
  - 仅本页订阅 useBarcodeScanner，离开本页立即取消订阅。
  - 失败时给一个 ElMessage.warning，留在原页面。
-->

<template>
  <div class="badge-gate">
    <div class="card">
      <el-icon :size="64" color="#409eff" class="pulse-icon"><Aim /></el-icon>
      <h1 class="title">工位扫码台</h1>
      <p class="hint">请扫描工牌条码以开始</p>
      <p class="sub-hint">扫描后请等待系统识别...</p>
      <div v-if="user" class="shelf-info">
        <span>当前账号: {{ user.full_name }}</span>
        <el-button link type="warning" @click="switchAccount">切换账号</el-button>
      </div>
      <div class="footer-links">
        <el-button link type="info" @click="goHome">管理员入口 · 返回首页</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Aim } from '@element-plus/icons-vue'
import { findWorkerByBadge } from '@/api/worker'
import { useAuthSession } from '@/composables/useAuthSession'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import { useScanSession } from '@/composables/useScanSession'

const router = useRouter()
const { onScan } = useBarcodeScanner()
const { setWorker } = useScanSession()
const { isAuthenticated, refreshOrLogout, user } = useAuthSession()

onMounted(async () => {
  if (!isAuthenticated()) {
    const ok = await refreshOrLogout(router)
    if (!ok) return
  }
  // 不再预热 worker 缓存：findWorkerByBadge 改为后端单点 query（POST /workers/verify-badge）。
  // 扫描时直接打到后端，结果强一致、无 500 条硬上限、无 TTL 失效问题。
})

const unsubscribe = onScan(async (code) => {
  try {
    const worker = await findWorkerByBadge(code)
    if (!worker) {
      ElMessage.warning(`未识别工牌: ${code}`)
      return
    }
    setWorker(worker)
    void router.push('/scan/action')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '工牌查询失败')
  }
})

onBeforeUnmount(() => {
  unsubscribe()
})

const { logout } = useAuthSession()
async function switchAccount(): Promise<void> { await logout(); router.replace('/login') }
function goHome(): void { void router.push('/dashboard') }
</script>

<style lang="scss" scoped>
.badge-gate {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0f1d3a 0%, #1e4d8b 100%);
  padding: 24px;
}

.card {
  width: 100%;
  max-width: 560px;
  padding: 64px 48px;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.pulse-icon {
  animation: pulse-scale 1.8s ease-in-out infinite;
}
@keyframes pulse-scale {
  0%, 100% { transform: scale(1); opacity: 1; }
  50%      { transform: scale(1.15); opacity: 0.7; }
}

.title {
  font-size: 32px;
  font-weight: 700;
  color: #142d54;
  margin: 24px 0 8px;
  letter-spacing: 4px;
}

.hint {
  font-size: 20px;
  color: #303133;
  margin: 0;
  font-weight: 500;
}

.sub-hint {
  font-size: 14px;
  color: #909399;
  margin: 8px 0 0;
}

.footer-links {
  margin-top: 48px;
}
</style>