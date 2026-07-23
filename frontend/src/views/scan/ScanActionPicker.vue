<!--
  ScanActionPicker.vue

  /scan/action：选择报工操作（取件 / 放回 / 送检 / 送货）。
  入口守卫：worker 缺失则跳回 /scan/badge。

  按钮按「绑定架 zone 并集」显示（HMI 账号在账号管理页绑定的货架决定）：
    * 绑了任意 PRODUCTION 架 → PICK_UP + RETURN
    * 绑了任意 INSPECTION 架 → INSPECT
    * 两种 zone 都绑了 → 三个按钮全显示

  注：HMI 账号已不再与货架一一对应，故不再显示「当前货架」选择器；
  具体作业货架由下游各流程的 ShelfPickerDialog / 零件持有者决定。
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
      </div>
    </div>

    <div class="content">
      <h2 class="state-title">请选择报工操作</h2>
      <div v-if="shelfLoading" style="text-align:center;padding:40px 0;color:#909399">加载货架信息...</div>
      <div v-else class="action-grid" :class="{ 'action-grid--two': !showInspect }">
        <el-button
          v-if="showPickUp"
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
          v-if="showReturn"
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
          v-if="showInspect"
          type="success"
          size="large"
          class="action-btn"
          @click="selectAction('INSPECT')"
        >
          <el-icon :size="48"><Check /></el-icon>
          <span class="action-label">送 检</span>
          <span class="action-desc">全部工序完成，送到品检区</span>
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeMount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Avatar,
  Back,
  Box,
  Check,
  Refresh,
} from '@element-plus/icons-vue'
import {
  ACTION_LABEL,
  useScanSession,
  type WorkAction,
} from '@/composables/useScanSession'
import { useActiveShelfSelection } from '@/composables/useActiveShelfSelection'

const router = useRouter()
const { worker, setAction, reset, requireWorker } = useScanSession()
const shelfSel = useActiveShelfSelection()

const shelfLoading = ref(true)

// 2026-07-13：boundZones = 绑定架 zone 的并集，决定按钮显隐
// - 含 PRODUCTION → PICK_UP + RETURN
// - 含 INSPECTION → INSPECT
const boundZones = computed<Set<string>>(() => {
  const s = new Set<string>()
  for (const o of shelfSel.options.value) {
    if (o.zone === 'PRODUCTION' || o.zone === 'INSPECTION') {
      s.add(o.zone)
    }
  }
  return s
})
const showPickUp = computed<boolean>(() => boundZones.value.has('PRODUCTION'))
const showReturn = computed<boolean>(() => boundZones.value.has('PRODUCTION'))
const showInspect = computed<boolean>(() => boundZones.value.has('INSPECTION'))

onBeforeMount(async () => {
  if (!requireWorker(router)) return
  // 拉候选架（绑定架详情；wildcard → 空；多架 → 等用户选）
  await shelfSel.initShelves()
  shelfLoading.value = false
})

function selectAction(a: WorkAction): void {
  setAction(a)
  ElMessage.success(`已选择: ${ACTION_LABEL[a]}`)
  // PICK_UP 走「按工种选件」新流程 → /scan/pick
  // RETURN 走「按工人列持有件 → 选件 → 选工序 → 选架」新流程 → /scan/return
  // INSPECT 走「按工人列持有件 → 选件 → 扫码确认 → 选品检架」新流程 → /scan/inspect
  // 送货入口已移到 MANAGER/INSPECTOR 的「送货」菜单（/delivery-dispatch）。
  if (a === 'PICK_UP') {
    void router.push('/scan/pick')
  } else if (a === 'RETURN') {
    void router.push('/scan/return')
  } else if (a === 'INSPECT') {
    void router.push('/scan/inspect')
  }
}

function rescanBadge(): void {
  // 不再需要清客户端缓存：findWorkerByBadge 直接打后端，结果强一致。
  reset()
  void router.replace('/scan/badge')
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