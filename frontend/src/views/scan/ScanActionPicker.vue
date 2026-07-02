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
      <div class="action-grid">
        <el-button
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
import { onBeforeMount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Avatar,
  Back,
  Box,
  Check,
  HomeFilled,
  Refresh,
} from '@element-plus/icons-vue'
import {
  ACTION_LABEL,
  useScanSession,
  type WorkAction,
} from '@/composables/useScanSession'

const router = useRouter()
const { worker, setAction, reset, requireWorker } = useScanSession()

onBeforeMount(() => {
  if (!requireWorker(router)) return
})

function selectAction(a: WorkAction): void {
  setAction(a)
  ElMessage.success(`已选择: ${ACTION_LABEL[a]}`)
  void router.push(`/scan/parts?action=${a.toLowerCase().replace('_', '')}`)
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

@media (max-width: 768px) {
  .action-grid {
    grid-template-columns: 1fr;
  }
  .action-btn {
    height: 160px !important;
  }
}
</style>