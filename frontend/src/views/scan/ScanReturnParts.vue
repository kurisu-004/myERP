<!--
  ScanReturnParts.vue

  /scan/return —— 扫码台 RETURN 新流程（2026-07-10 PR-E）
  1. onBeforeMount 调 GET /parts/by-worker/{worker_id} 列出当前 worker 持有件
  2. 工人点选一件 → 弹「下一道工序」picker（el-radio-group）
  3. 选完工序 → 弹 ShelfPickerDialog（共享 HMI 卡片网格）
  4. 提交 POST /parts/scan (event_type=RETURNED, shelf_id, next_process_id, badge_code)
  5. 成功后自动 refresh（该件从列表消失）

  与 ScanPickParts.vue 范式对齐：
  - 选件 → 选工序 → 选架 → 提交
  - 不需要扫码确认（点选即确认；旧流程「扫一批条码」已替换不保留）
-->

<template>
  <div class="scan-return">
    <!-- 顶栏 -->
    <div class="topbar">
      <div class="topbar-left">
        <el-icon :size="22" color="#fff"><Avatar /></el-icon>
        <span class="title">报工台</span>
        <el-divider direction="vertical" class="divider" />
        <span class="worker-name">{{ worker?.name ?? '—' }}</span>
        <el-tag size="default" type="info" effect="dark" class="badge-tag">
          {{ worker?.badge_code ?? '' }}
        </el-tag>
        <el-divider direction="vertical" class="divider" />
        <el-tag type="warning" effect="dark">放 回</el-tag>
      </div>
      <div class="topbar-right">
        <el-button type="info" plain @click="backToAction">
          <el-icon><Back /></el-icon>
          <span>返回操作选择</span>
        </el-button>
      </div>
    </div>

    <div class="content">
      <!-- 加载 -->
      <div v-if="loadingList" class="loading-block">
        <el-icon :size="32" class="is-loading"><Loading /></el-icon>
        <p>加载持有零件列表…</p>
      </div>

      <!-- 未识别工人 -->
      <div v-else-if="!worker?.id" class="empty-block">
        <el-icon :size="60" color="#e6a23c"><Warning /></el-icon>
        <h3>未识别工人</h3>
        <p>请重新刷工牌。</p>
        <el-button type="primary" @click="backToAction">返回</el-button>
      </div>

      <!-- 空列表 -->
      <div v-else-if="parts.length === 0" class="empty-block">
        <el-icon :size="60" color="#c0c4cc"><Box /></el-icon>
        <h3>您当前没有持有零件</h3>
        <p>请先到「取件」领取零件后再来放回。</p>
        <el-button @click="refresh" type="primary">刷新</el-button>
        <el-button @click="backToAction">返回</el-button>
      </div>

      <!-- 持有件列表 -->
      <div v-else>
        <div class="parts-header">
          <el-icon :size="24"><Box /></el-icon>
          <span class="parts-header-text">我的持有零件</span>
          <el-tag type="info" effect="plain" size="large" class="count-tag">
            共 {{ parts.length }} 件
          </el-tag>
          <el-button :icon="Refresh" circle size="small" @click="refresh" />
        </div>

        <!-- 已选确认栏 -->
        <div v-if="selectedPart" class="confirm-bar">
          <el-icon :size="20" color="#67c23a"><CircleCheckFilled /></el-icon>
          <span class="confirm-text">
            已选 <strong>{{ selectedPart.serial_no || selectedPart.drawing_no }}</strong>
            · {{ selectedPart.name }}
            · 当前所在 {{ selectedPart.shelf_code || '?' }}
            · 下一工序：{{ selectedNextProcessName || '未选' }}
            · 待选货架
          </span>
          <el-button size="small" @click="cancelSelect">取消选择</el-button>
        </div>

        <div class="parts-list">
          <el-card
            v-for="(p, idx) in parts"
            :key="p.id"
            shadow="hover"
            class="part-row"
            :class="{
              'is-selected': selectedPart?.id === p.id,
              'is-urgent': p.is_urgent,
            }"
            @click="onSelect(p)"
          >
            <div class="part-row-left">
              <span class="part-index">{{ idx + 1 }}</span>
              <div class="part-info">
                <div class="part-line-1">
                  <span class="serial-no">{{ p.serial_no || p.drawing_no }}</span>
                  <el-tag
                    v-if="p.is_urgent"
                    type="danger"
                    size="small"
                    effect="dark"
                    class="urgent-pulse"
                  >加急</el-tag>
                  <el-tag :type="deliveryUrgencyTag(p.planned_delivery_date)" size="small" effect="plain">
                    {{ formatDate(p.planned_delivery_date) }}
                  </el-tag>
                  <span
                    class="days-left"
                    :class="deliveryUrgencyClass(p.planned_delivery_date)"
                  >
                    {{ daysLeftText(p.planned_delivery_date) }}
                  </span>
                </div>
                <div class="part-line-2">
                  <span class="part-name">{{ p.name }}</span>
                  <span v-if="p.customer_path" class="customer">· {{ p.customer_path }}</span>
                </div>
                <div class="part-line-3">
                  <span class="qty">× {{ p.quantity }}</span>
                  <span class="drawing">· 图号 {{ p.drawing_no }}</span>
                  <span v-if="p.next_process_name" class="next-process">
                    · 当前下一工序：{{ p.next_process_name }}
                  </span>
                </div>
              </div>
            </div>
            <div class="part-row-right">
              <el-button type="warning" plain size="small">选 中</el-button>
            </div>
          </el-card>
        </div>
      </div>
    </div>

    <!-- 下一道工序选择对话框 -->
    <el-dialog
      v-model="showProcessDialog"
      title="选择下一道工序"
      width="420px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
    >
      <el-radio-group
        v-model="selectedNextProcessId"
        style="display: flex; flex-direction: column; gap: 8px"
      >
        <el-radio
          v-for="p in processes"
          :key="p.id"
          :value="p.id"
          border
        >
          <span style="font-family: 'SF Mono', Menlo, Consolas, monospace; font-weight: 600">{{ p.code }}</span>
          <span style="margin-left: 8px">{{ p.name }}</span>
          <el-tag
            :type="p.category === 'INHOUSE' ? 'primary' : 'warning'"
            size="small"
            style="margin-left: 8px"
          >{{ PROCESS_CATEGORY_LABEL[p.category] }}</el-tag>
        </el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="onProcessCancel">取消</el-button>
        <el-button type="primary" :disabled="!selectedNextProcessId" @click="onProcessConfirm">下一步</el-button>
      </template>
    </el-dialog>

    <!-- 共享 HMI RETURN 货架选择卡片网格 picker -->
    <ShelfPickerDialog
      v-if="showShelfPicker"
      v-model="showShelfPicker"
      :next-process-id="selectedNextProcessId || ''"
      @confirm="onShelfConfirm"
      @cancel="onShelfCancel"
    />
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
  CircleCheckFilled,
  Loading,
  Refresh,
  Warning,
} from '@element-plus/icons-vue'
import { useScanSession } from '@/composables/useScanSession'
import { listPartsHeldByWorker, scanPart, type PartItem } from '@/api/parts'
import { listProcesses } from '@/api/process'
import ShelfPickerDialog from '@/views/scan/components/ShelfPickerDialog.vue'
import { PROCESS_CATEGORY_LABEL, type Process } from '@/types/process'

const router = useRouter()
const { worker, requireWorker } = useScanSession()

const parts = ref<PartItem[]>([])
const loadingList = ref(false)
const selectedPart = ref<PartItem | null>(null)
const submitting = ref(false)

// 工序选择
const processes = ref<Process[]>([])
const loadingProcesses = ref(false)
const showProcessDialog = ref(false)
const selectedNextProcessId = ref<string>('')
const selectedNextProcessName = computed<string | null>(() => {
  if (!selectedNextProcessId.value) return null
  const p = processes.value.find(pp => pp.id === selectedNextProcessId.value)
  return p ? `${p.code} ${p.name}` : null
})

// 货架选择
const showShelfPicker = ref(false)

onBeforeMount(async () => {
  if (!requireWorker(router)) return
  await Promise.all([refresh(), loadProcesses()])
})

async function refresh(): Promise<void> {
  if (!worker.value?.id) return
  loadingList.value = true
  try {
    parts.value = await listPartsHeldByWorker(String(worker.value.id))
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载持有零件列表失败')
    parts.value = []
  } finally {
    loadingList.value = false
  }
}

async function loadProcesses(): Promise<void> {
  loadingProcesses.value = true
  try {
    const resp = await listProcesses({ limit: 200 })
    processes.value = resp.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载工序列表失败')
    processes.value = []
  } finally {
    loadingProcesses.value = false
  }
}

// --- 选件 → 工序 → 货架 → 提交 ---
function onSelect(p: PartItem): void {
  if (submitting.value) return
  selectedPart.value = p
  selectedNextProcessId.value = p.next_process_id ?? ''
  showProcessDialog.value = true
}

function onProcessConfirm(): void {
  if (!selectedNextProcessId.value) return
  showProcessDialog.value = false
  showShelfPicker.value = true
}

function onProcessCancel(): void {
  showProcessDialog.value = false
  cancelSelect()
}

async function onShelfConfirm(shelfId: string): Promise<void> {
  showShelfPicker.value = false
  if (!selectedPart.value || !selectedNextProcessId.value || !worker.value) return
  submitting.value = true
  try {
    await scanPart({
      serial_no: selectedPart.value.serial_no ?? '',
      event_type: 'RETURNED',
      shelf_id: shelfId,
      badge_code: worker.value.badge_code ?? '',
      next_process_id: selectedNextProcessId.value,
    })
    ElMessage.success(
      `已放回：${selectedPart.value.serial_no} → ${
        selectedNextProcessName.value ?? ''
      }`,
    )
    cancelSelect()
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '放回失败')
  } finally {
    submitting.value = false
  }
}

function onShelfCancel(): void {
  showShelfPicker.value = false
  cancelSelect()
}

function cancelSelect(): void {
  selectedPart.value = null
  selectedNextProcessId.value = ''
}

function backToAction(): void {
  cancelSelect()
  void router.replace('/scan/action')
}

// --- 交期辅助（与 ScanPickParts 一致） ---
function formatDate(s: string | null | undefined): string {
  if (!s) return ''
  return s.slice(5).replace(/-/g, '/')  // MM/DD
}
function daysLeftText(s: string | null | undefined): string {
  if (!s) return ''
  const d = new Date(s)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((d.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return `已逾期${Math.abs(diff)}天`
  if (diff === 0) return '今天到期'
  if (diff <= 3) return `${diff}天后到期`
  return ''
}
function deliveryUrgencyClass(s: string | null | undefined): string {
  if (!s) return ''
  const d = new Date(s)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((d.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return 'overdue'
  if (diff <= 3) return 'due-soon'
  return ''
}
function deliveryUrgencyTag(s: string | null | undefined): 'danger' | 'warning' | 'info' {
  if (!s) return 'info'
  const d = new Date(s)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((d.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return 'danger'
  if (diff <= 3) return 'warning'
  return 'info'
}
</script>

<style lang="scss" scoped>
.scan-return {
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
.topbar-left { display: flex; align-items: center; gap: 12px; font-size: 16px; }
.topbar-right { display: flex; gap: 8px; }
.title { font-size: 18px; font-weight: 700; letter-spacing: 2px; }
.divider { background: rgba(255,255,255,.3); height: 20px; }
.worker-name { font-size: 18px; font-weight: 600; }
.badge-tag { font-family: 'SF Mono', Menlo, Consolas, monospace; }

.content {
  flex: 1;
  overflow: auto;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
  padding: 24px;
}

.loading-block, .empty-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  gap: 12px;
  color: #606266;
  text-align: center;
  h3 { font-size: 20px; margin: 0; color: #303133; }
  p { color: #909399; max-width: 480px; }
}

.parts-header {
  display: flex; align-items: center; gap: 12px; margin-bottom: 16px; color: #303133;
}
.parts-header-text { font-size: 20px; font-weight: 600; }
.count-tag { font-size: 16px; padding: 6px 14px; }

.confirm-bar {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px;
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  border-radius: 8px;
  margin-bottom: 16px;
}
.confirm-text { flex: 1; color: #303133; }
.confirm-text strong {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: #67c23a; font-size: 18px; margin: 0 4px;
}

.parts-list { display: flex; flex-direction: column; gap: 10px; }

.part-row {
  display: flex !important;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px !important;
  border-left: 4px solid #e6a23c;  // 放回流程强调橙黄（与取件蓝区分）
  cursor: pointer;
  transition: border-color .15s, background .15s;
}
.part-row:hover { background: #f5f7fa; }
.part-row.is-selected { border-left-color: #67c23a; background: #f0f9eb; }

.part-row-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.part-row-left { display: flex; align-items: center; gap: 14px; flex: 1; }
.part-index {
  display: inline-flex; align-items: center; justify-content: center;
  width: 32px; height: 32px;
  background: #e6a23c; color: #fff; border-radius: 50%;
  font-weight: 600; font-size: 14px; flex-shrink: 0;
}
.part-info { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; }
.part-line-1 { display: flex; align-items: center; gap: 10px; }
.serial-no {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 18px; font-weight: 700; color: #303133;
}
.part-line-2 { display: flex; align-items: center; gap: 6px; font-size: 14px; color: #606266; }
.part-name { color: #303133; font-weight: 500; }
.customer { color: #909399; }
.part-line-3 { font-size: 13px; color: #909399; }
.qty { color: #e6a23c; font-weight: 600; margin-right: 6px; }
.drawing { font-family: 'SF Mono', Menlo, Consolas, monospace; }
.next-process { color: #67c23a; }

.is-loading { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.part-row.is-urgent {
  border-left-color: #f56c6c;
  background: #fef0f0;
}
.days-left {
  font-size: 12px;
  font-weight: 600;
  margin-left: 4px;
}
.days-left.overdue { color: #f56c6c; }
.days-left.due-soon { color: #e6a23c; }

@keyframes urgentPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}
.urgent-pulse {
  animation: urgentPulse 1.2s ease-in-out infinite;
}
</style>
