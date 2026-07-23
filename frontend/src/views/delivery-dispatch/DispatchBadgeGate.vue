<!--
  DispatchBadgeGate.vue

  /delivery-dispatch/badge：司机送货扫码台入口（MANAGER/INSPECTOR 菜单「送货」进入）。
  - 全屏，MainLayout 之外；样式对齐 views/scan/ScanBadgeGate.vue。
  - 扫工牌 → findWorkerByBadge → 解析工种码：
    * 工种 = 送货司机 → 存 worker，进入 /delivery-dispatch/notes。
    * 其它工种 / 未分配 → 提示「不是送货司机」，留在本页。
  - 仅本页订阅 useBarcodeScanner，离开立即取消。
-->

<template>
  <div class="badge-gate">
    <div class="card">
      <el-icon :size="64" color="#409eff" class="pulse-icon"><Van /></el-icon>
      <h1 class="title">送货扫码台</h1>
      <p class="hint">请送货司机扫描本人工牌以开始</p>
      <p class="sub-hint">仅「送货司机」工种可进入</p>
      <div class="footer-links">
        <el-button link type="info" @click="goHome">返回首页</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Van } from '@element-plus/icons-vue'
import { findWorkerByBadge } from '@/api/worker'
import { listWorkTypes } from '@/api/workType'
import type { WorkType } from '@/types/workType'
import { useAuthSession } from '@/composables/useAuthSession'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import { useScanSession } from '@/composables/useScanSession'

const DRIVER_WORK_TYPE_CODE = '送货司机'

const router = useRouter()
const { onScan } = useBarcodeScanner()
const { setWorker } = useScanSession()
const { isAuthenticated, refreshOrLogout } = useAuthSession()

onMounted(async () => {
  if (!isAuthenticated()) {
    await refreshOrLogout(router)
  }
})

/** 解析工人工种码（verify-badge 只回 work_type_id，需再查工种表）。 */
async function resolveWorkTypeCode(workTypeId: string | null): Promise<string | null> {
  if (!workTypeId) return null
  try {
    const resp = await listWorkTypes({ limit: 200 })
    const wt = (resp.items as WorkType[]).find(
      (w) => String(w.id) === String(workTypeId),
    )
    return wt?.code ?? null
  } catch {
    return null
  }
}

const unsubscribe = onScan(async (code) => {
  try {
    const worker = await findWorkerByBadge(code)
    if (!worker) {
      ElMessage.warning(`未识别工牌: ${code}`)
      return
    }
    const wtCode = await resolveWorkTypeCode(worker.work_type_id)
    if (wtCode !== DRIVER_WORK_TYPE_CODE) {
      ElMessage.error(`${worker.name} 不是送货司机，无法送货`)
      return
    }
    setWorker(worker)
    void router.push('/delivery-dispatch/notes')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '工牌查询失败')
  }
})

onBeforeUnmount(() => {
  unsubscribe()
})

function goHome(): void {
  void router.push('/dashboard')
}
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
