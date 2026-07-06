<!--
  ScanPartsWork.vue

  /scan/parts?action=pickup|return|inspect
  - 扫描状态机：scanning -> submitting -> done
  - 每扫一个 drawing code 立即查 GET /parts/by-serial/{code}，结果填回当前行
  - "完成"按钮进入 submitting 状态，顺序调 pickUpPart / scanPart 提交
  - done 状态显示成功 / 失败统计 + 再来一组 / 退至操作选择
  - 守卫：缺 worker 跳 /scan/badge；缺/非法 action 跳 /scan/action
-->

<template>
  <div class="parts-work">
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
        <template v-if="actionLabel">
          <el-divider direction="vertical" class="divider" />
          <el-tag size="default" :type="actionTagType" effect="dark">
            {{ actionLabel }}
          </el-tag>
        </template>
      </div>
      <div class="topbar-right">
        <span v-if="state === 'scanning'" class="hint">
          <el-icon><Aim /></el-icon>
          等待扫码...
        </span>
        <el-button type="info" plain @click="backToAction">
          <el-icon><Back /></el-icon>
          <span>返回操作选择</span>
        </el-button>
      </div>
    </div>

    <!-- SCANNING -->
    <div v-if="state === 'scanning'" class="content">
      <div class="parts-header">
        <el-icon :size="24"><Aim /></el-icon>
        <span class="parts-header-text">请扫描图纸条码</span>
        <el-tag type="info" effect="plain" size="large" class="count-tag">
          已扫 {{ total }} 件
        </el-tag>
      </div>

      <div v-if="parts.length === 0" class="empty-hint">
        <el-icon :size="60" color="#c0c4cc"><DocumentAdd /></el-icon>
        <p>暂无图纸，请扫码</p>
      </div>

      <div v-else class="parts-list">
        <el-card
          v-for="(entry, idx) in parts"
          :key="entry.uid"
          shadow="hover"
          class="part-row"
          :class="{
            'is-success': entry.phase === 'success',
            'is-error': entry.phase === 'error',
            'is-loading': entry.phase === 'loading',
          }"
        >
          <div class="part-row-left">
            <span class="part-index">{{ idx + 1 }}</span>
            <div class="part-info">
              <div class="part-line-1">
                <span class="serial-no">{{ entry.serialNo }}</span>
                <el-tag
                  v-if="entry.part"
                  :type="statusToTagType(entry.part.status)"
                  size="default"
                  effect="plain"
                  class="status-tag"
                >
                  {{ statusLabel(entry.part.status) }}
                </el-tag>
                <el-tag
                  v-else-if="entry.phase === 'loading'"
                  type="info"
                  size="default"
                  effect="plain"
                >
                  查询中...
                </el-tag>
              </div>
              <div v-if="entry.part" class="part-line-2">
                <span class="part-name">{{ entry.part.name }}</span>
                <span v-if="entry.part.customer_path" class="customer">
                  · {{ entry.part.customer_path }}
                </span>
              </div>
              <div v-if="entry.part" class="part-line-3">
                <span class="qty">× {{ entry.part.quantity }}</span>
                <span v-if="entry.part.serial_no" class="serial">
                  · 序列 {{ entry.part.serial_no }}
                </span>
              </div>
              <div v-else-if="entry.phase === 'error'" class="part-line-2 error">
                <el-icon color="#f56c6c"><CircleCloseFilled /></el-icon>
                <span>{{ entry.error ?? '未找到零件' }}</span>
              </div>
              <div v-else-if="entry.phase === 'loading'" class="part-line-2 muted">
                正在查询零件信息...
              </div>
            </div>
          </div>
          <el-button
            v-if="entry.phase !== 'loading'"
            link
            type="danger"
            size="large"
            :icon="Delete"
            circle
            @click="onRemove(entry.uid)"
          />
        </el-card>
      </div>

      <div class="footer-bar">
        <el-button size="large" @click="backToAction">取消</el-button>
        <el-button
          type="primary"
          size="large"
          :disabled="!canSubmit"
          class="submit-btn"
          @click="onSubmit"
        >
          <el-icon><Select /></el-icon>
          <span>完成</span>
        </el-button>
      </div>
    </div>

    <!-- SUBMITTING -->
    <div v-else-if="state === 'submitting'" class="content">
      <div class="progress-summary">
        <el-icon :size="48" color="#409eff" class="is-loading"><Loading /></el-icon>
        <h2 class="state-title">正在提交报工...</h2>
        <p class="progress-text">进度 {{ submittedCount }} / {{ total }}</p>
      </div>
      <div class="parts-list">
        <el-card
          v-for="(entry, idx) in parts"
          :key="entry.uid"
          shadow="never"
          class="part-row progress-row"
          :class="{
            'is-success': entry.phase === 'success',
            'is-error': entry.phase === 'error',
          }"
        >
          <div class="part-row-left">
            <span class="part-index">{{ idx + 1 }}</span>
            <div class="part-info">
              <div class="part-line-1">
                <span class="serial-no">{{ entry.serialNo }}</span>
              </div>
              <div v-if="entry.phase === 'success' && entry.part" class="part-line-2 success">
                <el-icon color="#67c23a"><CircleCheckFilled /></el-icon>
                <span>成功 · 状态: {{ statusLabel(entry.part.status) }}</span>
              </div>
              <div v-else-if="entry.phase === 'error'" class="part-line-2 error">
                <el-icon color="#f56c6c"><CircleCloseFilled /></el-icon>
                <span>失败 · {{ entry.error }}</span>
              </div>
              <div v-else class="part-line-2 pending">
                <el-icon class="is-loading"><Loading /></el-icon>
                <span>等待中...</span>
              </div>
            </div>
          </div>
        </el-card>
      </div>
    </div>

    <!-- DONE -->
    <div v-else class="content">
      <div class="result-summary">
        <h2 class="state-title">报工完成</h2>
        <div class="result-tags">
          <el-tag type="success" effect="dark" size="large" round>
            成功 {{ successCount }}
          </el-tag>
          <el-tag
            v-if="failCount > 0"
            type="danger"
            effect="dark"
            size="large"
            round
          >
            失败 {{ failCount }}
          </el-tag>
        </div>
      </div>
      <div class="parts-list">
        <el-card
          v-for="(entry, idx) in parts"
          :key="entry.uid"
          shadow="never"
          class="part-row progress-row"
          :class="{
            'is-success': entry.phase === 'success',
            'is-error': entry.phase === 'error',
          }"
        >
          <div class="part-row-left">
            <span class="part-index">{{ idx + 1 }}</span>
            <div class="part-info">
              <div class="part-line-1">
                <span class="serial-no">{{ entry.serialNo }}</span>
                <span v-if="entry.part" class="part-name-inline">
                  · {{ entry.part.name }}
                </span>
              </div>
              <div v-if="entry.phase === 'success' && entry.part" class="part-line-2 success">
                <el-icon color="#67c23a"><CircleCheckFilled /></el-icon>
                <span>成功 · 状态: {{ statusLabel(entry.part.status) }}</span>
              </div>
              <div v-else-if="entry.phase === 'error'" class="part-line-2 error">
                <el-icon color="#f56c6c"><CircleCloseFilled /></el-icon>
                <span>错误: {{ entry.error }}</span>
              </div>
            </div>
          </div>
        </el-card>
      </div>
      <div class="footer-bar">
        <el-button size="large" @click="backToAction">退至操作选择</el-button>
        <el-button type="primary" size="large" @click="onAgain">再来一组</el-button>
      </div>
    </div>
    <!-- 品检货架选择对话框 -->
    <el-dialog v-model="showInspDialog" title="选择目标品检货架" width="360px" :close-on-click-modal="false">
      <el-radio-group v-model="targetInspectionShelfId">
        <el-radio v-for="s in inspShelves" :key="s.id" :value="Number(s.id)" style="display:block;margin-bottom:8px">
          {{ s.code }} — {{ s.name }}
        </el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="showInspDialog = false">取消</el-button>
        <el-button type="primary" @click="inspectorConfirm">确认送检</el-button>
      </template>
    </el-dialog>

    <!-- 下一道工序选择对话框（RETURN 时）-->
    <el-dialog v-model="showNextProcessDialog" title="选择下一道工序" width="420px" :close-on-click-modal="false">
      <el-radio-group v-model="selectedNextProcessId" style="display: flex; flex-direction: column; gap: 8px">
        <el-radio v-for="p in processes" :key="p.id" :value="p.id" border>
          <span style="font-family: 'SF Mono', Menlo, Consolas, monospace; font-weight: 600">{{ p.code }}</span>
          <span style="margin-left: 8px">{{ p.name }}</span>
          <el-tag
            :type="p.category === 'INHOUSE' ? 'primary' : 'warning'"
            size="small" style="margin-left: 8px"
          >{{ PROCESS_CATEGORY_LABEL[p.category] }}</el-tag>
        </el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="showNextProcessDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmNextProcess">确认放回</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeMount, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Aim,
  Avatar,
  Back,
  CircleCheckFilled,
  CircleCloseFilled,
  Delete,
  DocumentAdd,
  Loading,
  Select,
} from '@element-plus/icons-vue'
import { ORDER_STATUS_LABEL, ORDER_STATUS_TAG_TYPE, type OrderStatus } from '@/types/parts'
import { ACTION_LABEL, ACTION_TAG_TYPE, useScanSession } from '@/composables/useScanSession'
import { usePartsScanQueue } from '@/composables/usePartsScanQueue'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import { useAuthSession } from '@/composables/useAuthSession'
import { listShelves } from '@/api/shelves'
import type { Shelf } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import type { Process } from '@/types/process'
import { PROCESS_CATEGORY_LABEL } from '@/types/process'

type PageState = 'scanning' | 'submitting' | 'done'

const route = useRoute()
const router = useRouter()
const { worker, action, setAction, requireWorkerAndAction, slugToAction } = useScanSession()
const queue = usePartsScanQueue()
const { onScan } = useBarcodeScanner()
const { isAuthenticated, refreshOrLogout, user, activeShelfId } = useAuthSession()

const state = ref<PageState>('scanning')
// shelf_id 在前端保持字符串：雪花 ID 长度 > 2^53，Number() 会丢精度。
// 后端 Pydantic v2 默认接受 JSON string → int。
const shelfId = ref<string>('')
const showInspDialog = ref(false)
const inspShelves = ref<Shelf[]>([])
const targetInspectionShelfId = ref<string>()
// RETURN 时的「下一道工序」选择
const showNextProcessDialog = ref(false)
const processes = ref<Process[]>([])
const selectedNextProcessId = ref<string>()

const actionLabel = computed(() => (action.value ? ACTION_LABEL[action.value] : ''))
const actionTagType = computed(() => (action.value ? ACTION_TAG_TYPE[action.value] : 'info'))

const { parts, total, submittedCount, successCount, failCount, addOrIgnore, remove, reset, submit } = queue

const canSubmit = computed(() => parts.value.length > 0 && parts.value.every((p) => p.phase !== 'loading'))

function statusLabel(s: OrderStatus): string { return ORDER_STATUS_LABEL[s] ?? s }
function statusToTagType(s: OrderStatus): 'primary' | 'success' | 'warning' | 'info' | 'danger' { return ORDER_STATUS_TAG_TYPE[s] ?? 'info' }

onBeforeMount(async () => {
  if (!isAuthenticated()) { const ok = await refreshOrLogout(router); if (!ok) return }
  if (!requireWorkerAndAction(router)) return

  const a = slugToAction(route.query.action)
  if (a && a !== action.value) { setAction(a) } else if (!a) { void router.replace('/scan/action') }

  // 取当前货架（activeShelfId 现在直接返回 string，不再 Number()）
  const sid = activeShelfId()
  if (sid) shelfId.value = sid
})

const unsubscribe = onScan((code) => { if (state.value !== 'scanning') return; void addOrIgnore(code) })

onBeforeUnmount(() => { unsubscribe() })

function onRemove(uid: string): void { remove(uid) }

async function onSubmit(): Promise<void> {
  if (!worker.value || !action.value) return
  if (parts.value.length === 0) return
  if (!shelfId.value) { ElMessage.warning('未找到当前货架信息，请重新登录'); return }

  // INSPECT 需要先弹窗选目标品检货架
  if (action.value === 'INSPECT') {
    inspShelves.value = (await listShelves({ zone: 'INSPECTION', is_active: true })).items
    targetInspectionShelfId.value = undefined
    showInspDialog.value = true
    return
  }

  // RETURN 需要选下一道工序
  if (action.value === 'RETURN') {
    processes.value = (await listProcesses({ limit: 200 })).items
    selectedNextProcessId.value = undefined
    showNextProcessDialog.value = true
    return
  }

  await doSubmit(undefined)
}

async function doSubmit(nextProcessId?: string | null): Promise<void> {
  state.value = 'submitting'
  await submit(shelfId.value, worker.value!.badge_code, action.value!, targetInspectionShelfId.value, nextProcessId)
  state.value = 'done'
}

function onAgain(): void { reset(); state.value = 'scanning'; ElMessage.info('请继续扫码') }
function backToAction(): void { void router.replace('/scan/action') }
function inspectorConfirm(): void {
  if (!targetInspectionShelfId.value) { ElMessage.warning('请选择目标品检货架'); return }
  showInspDialog.value = false
  void doSubmit(undefined)
}
function confirmNextProcess(): void {
  if (!selectedNextProcessId.value) { ElMessage.warning('请选择下一道工序'); return }
  const nextProcessId = selectedNextProcessId.value
  showNextProcessDialog.value = false
  void doSubmit(nextProcessId)
}
</script>

<style lang="scss" scoped>
.parts-work {
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
  align-items: center;
  gap: 12px;
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
.header-right .hint,
.topbar-right .hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.85);
  background: rgba(255, 255, 255, 0.1);
  padding: 4px 12px;
  border-radius: 16px;
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.content {
  flex: 1;
  overflow: auto;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
  padding: 24px;
}

.parts-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  color: #303133;
}
.parts-header-text {
  font-size: 20px;
  font-weight: 600;
}
.count-tag {
  font-size: 16px;
  padding: 6px 14px;
}
.empty-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  color: #909399;
  p { margin-top: 12px; font-size: 16px; }
}
.parts-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.part-row {
  display: flex !important;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px !important;
  border-left: 4px solid #409eff;
}
.part-row.is-success { border-left-color: #67c23a; background: #f0f9eb; }
.part-row.is-error { border-left-color: #f56c6c; background: #fef0f0; }
.part-row.is-loading { border-left-color: #e6a23c; }
.part-row.progress-row { background: #fafafa; }

.part-row-left {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: 1;
}
.part-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: #409eff;
  color: #fff;
  border-radius: 50%;
  font-weight: 600;
  font-size: 14px;
  flex-shrink: 0;
}
.part-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 0;
}
.part-line-1 {
  display: flex;
  align-items: center;
  gap: 10px;
}
.serial-no {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 18px;
  font-weight: 700;
  color: #303133;
}
.status-tag { font-size: 12px; }
.part-name-inline { color: #606266; font-size: 15px; }
.part-line-2 {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #606266;
}
.part-line-2.error { color: #f56c6c; font-weight: 500; }
.part-line-2.success { color: #67c23a; font-weight: 500; }
.part-line-2.pending { color: #e6a23c; font-weight: 500; }
.part-line-2.muted { color: #c0c4cc; font-size: 13px; }
.part-line-3 {
  font-size: 13px;
  color: #909399;
  .qty { color: #409eff; font-weight: 600; margin-right: 6px; }
  .serial { font-family: 'SF Mono', Menlo, Consolas, monospace; }
}
.part-name { color: #303133; font-weight: 500; }
.customer { color: #909399; }
.is-loading { animation: spin 1s linear infinite; }
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.footer-bar {
  position: sticky;
  bottom: 0;
  margin-top: 24px;
  padding: 16px 0;
  background: #fff;
  border-top: 1px solid #ebeef5;
  display: flex;
  justify-content: center;
  gap: 16px;
}
.submit-btn {
  min-width: 160px;
  font-size: 16px;
  font-weight: 600;
}

.progress-summary {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 32px;
}
.progress-text {
  margin: 12px 0 0;
  font-size: 18px;
  color: #409eff;
  font-weight: 600;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}

.result-summary {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}
.state-title {
  text-align: center;
  font-size: 26px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 32px;
}
.result-tags {
  display: flex;
  gap: 16px;
}
</style>