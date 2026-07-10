<!--
  ScanActionPicker.vue

  /scan/action：选择报工操作（取件 / 放回 / 送检）。
  入口守卫：worker 缺失则跳回 /scan/badge。
-->

<template>
  <div class="action-picker">
    <div class="topbar">
      <div class="topbar-left">
        <el-icon :size="22" color="#fff"><Avatar /></el-icon>
        <span class="title">报工台</span>
        <el-divider direction="vertical" class="divider" />
        <span class="worker-name">{{ worker?.name ?? '—' }}</span>
        <el-tag size="default" type="info" effect="dark" class="badge-tag">
          {{ worker?.badge_code ?? '' }}
        </el-tag>
      </div>
      <div class="topbar-right">
        <el-button type="warning" plain @click="rescanBadge">
          <el-icon><Refresh /></el-icon>
          <span>重新扫工牌</span>
        </el-button>
        <el-button type="info" plain @click="goHome">
          <el-icon><HomeFilled /></el-icon>
          <span>返回首页</span>
        </el-button>
      </div>
    </div>

    <div class="content">
      <h2 class="state-title">请选择报工操作</h2>
      <div v-if="shelfLoading" style="text-align:center;padding:40px 0;color:#909399">加载货架信息...</div>
      <div v-else class="action-grid" :class="{ 'action-grid--two': shelfZone === 'PRODUCTION' }">
        <el-button
          v-if="shelfZone === 'PRODUCTION'"
          type="primary"
          size="large"
          class="action-btn"
          @click="selectAction('PICK_UP')"
        >
          <el-icon :size="48"><Box /></el-icon>
          <span class="action-label">取 件</span>
          <span class="action-desc">扫码领取，开始加工</span>
        </el-button>
        <el-button
          v-if="shelfZone === 'PRODUCTION'"
          type="warning"
          size="large"
          class="action-btn"
          @click="selectAction('RETURN')"
        >
          <el-icon :size="48"><Back /></el-icon>
          <span class="action-label">放 回</span>
          <span class="action-desc">加工完一道工序放回待加工区</span>
        </el-button>
        <el-button
          v-if="shelfZone === 'INSPECTION'"
          type="success"
          size="large"
          class="action-btn"
          @click="selectAction('INSPECT')"
        >
          <el-icon :size="48"><Check /></el-icon>
          <span class="action-label">送 检</span>
          <span class="action-desc">全部工序完成，送到品检区</span>
        </el-button>

        <!-- 送货（PR-C 2026-07-10）：工种=送货司机时不依赖货架，单独显示。 -->
        <el-button
          v-if="workerWorkTypeCode === '送货司机'"
          type="danger"
          size="large"
          class="action-btn"
          @click="selectAction('DELIVER')"
        >
          <el-icon :size="48"><Van /></el-icon>
          <span class="action-label">送 货</span>
          <span class="action-desc">按客户分批，扫零件确认发货</span>
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeMount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Avatar,
  Back,
  Box,
  Check,
  HomeFilled,
  Refresh,
  Van,
} from '@element-plus/icons-vue'
import {
  ACTION_LABEL,
  useScanSession,
  type WorkAction,
} from '@/composables/useScanSession'
import { useAuthSession } from '@/composables/useAuthSession'
import { listShelves } from '@/api/shelves'
import type { Shelf } from '@/types/shelf'
import { listWorkTypes } from '@/api/workType'
import type { WorkType } from '@/types/workType'

const router = useRouter()
const { worker, setAction, reset, requireWorker } = useScanSession()
const { activeShelfId } = useAuthSession()

const shelfZone = ref<string | null>(null) // 'PRODUCTION' | 'INSPECTION' | null
const workerWorkTypeCode = ref<string | null>(null) // '送货司机' 等
const shelfLoading = ref(true)

onBeforeMount(async () => {
  if (!requireWorker(router)) return
  // 获取当前货架区域，决定可用操作
  // 三种场景的 shelfZone 派生：
  //   1) scoped HMI（shelf_ids 非空）  → 查 /shelves 拿该架的 zone
  //   2) wildcard 共享 HMI（shelf_ids 空）→ 默认 PRODUCTION（共享工控机典型装在生产区）
  //   3) scoped HMI 但 shelf 找不到 / 403 → 同样默认 PRODUCTION，保证按钮可点
  const sid = activeShelfId()
  if (sid) {
    try {
      const items = (await listShelves({ limit: 200 })).items
      const shelf = items.find((s: Shelf) => String(s.id) === sid)
      shelfZone.value = shelf?.zone ?? 'PRODUCTION'
    } catch {
      shelfZone.value = 'PRODUCTION'
    }
  } else {
    // wildcard 共享 HMI：不绑死单架，按生产区工控机处理
    shelfZone.value = 'PRODUCTION'
  }
  // 工种决定 DELIVER 入口（PR-C 2026-07-10）
  if (worker.value?.work_type_id) {
    try {
      const wtResp = await listWorkTypes({ limit: 200 })
      const wt = (wtResp.items as WorkType[]).find(
        (w) => String(w.id) === String(worker.value!.work_type_id),
      )
      workerWorkTypeCode.value = wt?.code ?? null
    } catch {
      workerWorkTypeCode.value = null
    }
  }
  shelfLoading.value = false
})

function selectAction(a: WorkAction): void {
  setAction(a)
  ElMessage.success(`已选择: ${ACTION_LABEL[a]}`)
  // PICK_UP 走「按工种选件」新流程 → /scan/pick
  // RETURN 走「按工人列持有件 → 选件 → 选工序 → 选架」新流程 → /scan/return
  // INSPECT 沿用旧扫码流程 → /scan/parts
  // DELIVER（PR-C）走司机专属送货单流程 → /scan/deliver
  if (a === 'PICK_UP') {
    void router.push('/scan/pick')
  } else if (a === 'RETURN') {
    void router.push('/scan/return')
  } else if (a === 'DELIVER') {
    void router.push('/scan/deliver')
  } else {
    void router.push(`/scan/parts?action=${a.toLowerCase().replace('_', '')}`)
  }
}

function rescanBadge(): void {
  // 不再需要清客户端缓存：findWorkerByBadge 直接打后端，结果强一致。
  reset()
  void router.replace('/scan/badge')
}

function goHome(): void {
  void router.push('/dashboard')
}
</script>

<style lang="scss" scoped>
.action-picker {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(90deg, #142d54 0%, #1e4d8b 100%);
  color: #fff;
  padding: 12px 24px;
  height: 60px;
  flex-shrink: 0;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 16px;
}

.topbar-right {
  display: flex;
  gap: 8px;
}

.title {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 2px;
}
.divider {
  background: rgba(255, 255, 255, 0.3);
  height: 20px;
}
.worker-name {
  font-size: 18px;
  font-weight: 600;
}
.badge-tag {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}

.content {
  flex: 1;
  overflow: auto;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
  padding: 32px 24px;
}

.state-title {
  text-align: center;
  font-size: 26px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 32px;
}

.action-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
}

.action-btn {
  height: 240px !important;
  display: flex !important;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  border-radius: 12px !important;
  font-size: 18px;
}

.action-label {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: 4px;
  line-height: 1;
}

.action-desc {
  font-size: 13px;
  font-weight: 400;
  opacity: 0.85;
  margin-top: 4px;
}

.action-grid--two {
  grid-template-columns: repeat(2, 1fr);
}

@media (max-width: 768px) {
  .action-grid {
    grid-template-columns: 1fr;
  }
  .action-btn {
    height: 160px !important;
  }
}
</style>