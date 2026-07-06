<!--
  ScanPickParts.vue

  /scan/pick —— 扫码台 PICK_UP 新流程
  1. 拉取 worker.work_type_id 映射下、当前货架上的零件列表
  2. 工人点选一个零件 → 进入「等待扫码」状态
  3. 扫码枪输入 serial_no；前端校验必须等于选中零件.serial_no；不等则拒绝
  4. 通过则调 POST /parts/pick-up；成功后自动回到列表（可选再选下一件）
-->

<template>
  <div class="scan-pick">
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
        <el-tag type="primary" effect="dark">取 件</el-tag>
      </div>
      <div class="topbar-right">
        <el-button type="info" plain @click="backToAction">
          <el-icon><Back /></el-icon>
          <span>返回操作选择</span>
        </el-button>
      </div>
    </div>

    <div class="content">
      <div v-if="loadingList" class="loading-block">
        <el-icon :size="32" class="is-loading"><Loading /></el-icon>
        <p>加载可领件列表…</p>
      </div>

      <div v-else-if="!worker?.work_type_id" class="empty-block">
        <el-icon :size="60" color="#e6a23c"><Warning /></el-icon>
        <h3>未分配工种</h3>
        <p>请联系管理员在「权限管理 → 工人一览」中为本工牌指派工种。</p>
        <el-button type="primary" @click="backToAction">返回</el-button>
      </div>

      <div v-else-if="parts.length === 0" class="empty-block">
        <el-icon :size="60" color="#c0c4cc"><Box /></el-icon>
        <h3>当前货架无可领件</h3>
        <p>该工种在生产货架 {{ activeShelfCode || shelfId || '?' }} 上没有匹配「下一道工序」的零件。</p>
        <el-button @click="refresh" type="primary">刷新</el-button>
        <el-button @click="backToAction">返回</el-button>
      </div>

      <div v-else>
        <div class="parts-header">
          <el-icon :size="24"><Box /></el-icon>
          <span class="parts-header-text">可领件列表</span>
          <el-tag type="info" effect="plain" size="large" class="count-tag">
            共 {{ parts.length }} 件
          </el-tag>
          <el-button :icon="Refresh" circle size="small" @click="refresh" />
        </div>

        <div v-if="selectedPart" class="confirm-bar">
          <el-icon :size="20" color="#409eff" class="is-loading"><Aim /></el-icon>
          <span class="confirm-text">
            已选 <strong>{{ selectedPart.serial_no || selectedPart.drawing_no }}</strong>
            · {{ selectedPart.name }}
            · 请扫描该零件的序列号条码确认
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
                  <el-tag v-if="p.is_urgent" type="danger" size="small" effect="dark" class="urgent-pulse">加急</el-tag>
                  <el-tag :type="deliveryUrgencyTag(p.planned_delivery_date)" size="small" effect="plain">
                    {{ formatDate(p.planned_delivery_date) }}
                  </el-tag>
                  <span class="days-left" :class="deliveryUrgencyClass(p.planned_delivery_date)">
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
                  <span v-if="p.worker_name" class="holder">· 当前持有: {{ p.worker_name }}</span>
                </div>
              </div>
            </div>
            <div class="part-row-right">
              <el-button text size="small" type="info" @click.stop="onPreview(p)">
                <el-icon><View /></el-icon>预览
              </el-button>
              <el-button type="primary" plain size="small">选 中</el-button>
            </div>
          </el-card>
        </div>
      </div>
    </div>

    <!-- 图纸预览抽屉 -->
    <el-drawer
      v-model="showPreview"
      :title="previewPart ? `预览 · ${previewPart.drawing_no}` : '图纸预览'"
      size="480px"
    >
      <div v-if="previewPart" class="preview-content">
        <div class="barcode-section">
          <Barcode
            :value="previewPart.serial_no || previewPart.drawing_no"
            format="CODE39"
            :width="2"
            :height="60"
            display-value
          />
        </div>
        <el-descriptions :column="1" border size="small" class="preview-meta">
          <el-descriptions-item label="序列号">{{ previewPart.serial_no || '—' }}</el-descriptions-item>
          <el-descriptions-item label="图号">{{ previewPart.drawing_no }}</el-descriptions-item>
          <el-descriptions-item label="名称">{{ previewPart.name }}</el-descriptions-item>
          <el-descriptions-item label="数量">× {{ previewPart.quantity }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ previewPart.customer_path || '—' }}</el-descriptions-item>
          <el-descriptions-item label="计划交期">{{ previewPart.planned_delivery_date }}</el-descriptions-item>
          <el-descriptions-item label="加急">
            <el-tag v-if="previewPart.is_urgent" type="danger" size="small">是</el-tag>
            <span v-else>否</span>
          </el-descriptions-item>
        </el-descriptions>
        <div class="file-section">
          <p class="file-section-title">图纸文件</p>
          <el-link type="primary" :href="`/parts/${previewPart.id}`" target="_blank">
            <el-icon><Link /></el-icon> 查看详情 / 图纸
          </el-link>
          <p style="margin-top: 8px; font-size: 12px; color: #909399;">
            在零件详情页可浏览 PDF / STEP 等图纸文件
          </p>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onBeforeMount, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Aim,
  Avatar,
  Back,
  Box,
  Link,
  Loading,
  Refresh,
  View,
  Warning,
} from '@element-plus/icons-vue'
import Barcode from '@/components/Barcode.vue'
import { useScanSession } from '@/composables/useScanSession'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import { useAuthSession } from '@/composables/useAuthSession'
import { listPartsByWorkType, pickUpPart, type PartItem } from '@/api/parts'

const router = useRouter()
const { worker, requireWorker } = useScanSession()
const { onScan } = useBarcodeScanner()
const { activeShelfId } = useAuthSession()

const shelfId = ref<string>('')
const activeShelfCode = ref<string>('')
const parts = ref<PartItem[]>([])
const loadingList = ref(false)
const selectedPart = ref<PartItem | null>(null)
const submitting = ref(false)

// --- 预览 ---
const showPreview = ref(false)
const previewPart = ref<PartItem | null>(null)
function onPreview(p: PartItem): void {
  previewPart.value = p
  showPreview.value = true
}

// --- 交期辅助 ---
function formatDate(s: string): string {
  if (!s) return ''
  return s.slice(5).replace(/-/g, '/')  // MM/DD
}
function daysLeftText(s: string): string {
  if (!s) return ''
  const d = new Date(s)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((d.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return `已逾期${Math.abs(diff)}天`
  if (diff === 0) return '今天到期'
  if (diff <= 3) return `${diff}天后到期`
  return ''
}
function deliveryUrgencyClass(s: string): string {
  if (!s) return ''
  const d = new Date(s)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((d.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return 'overdue'
  if (diff <= 3) return 'due-soon'
  return ''
}
function deliveryUrgencyTag(s: string): 'danger' | 'warning' | 'info' {
  if (!s) return 'info'
  const d = new Date(s)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.ceil((d.getTime() - today.getTime()) / 86400000)
  if (diff < 0) return 'danger'
  if (diff <= 3) return 'warning'
  return 'info'
}

onBeforeMount(async () => {
  if (!requireWorker(router)) return
  const sid = activeShelfId()
  if (!sid) {
    ElMessage.error('未找到当前货架信息')
    await router.replace('/scan/action')
    return
  }
  shelfId.value = sid
  await refresh()
})

async function refresh(): Promise<void> {
  if (!worker.value?.work_type_id || !shelfId.value) return
  loadingList.value = true
  try {
    parts.value = await listPartsByWorkType(worker.value.work_type_id, shelfId.value)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载列表失败')
  } finally {
    loadingList.value = false
  }
}

function onSelect(p: PartItem): void {
  if (submitting.value) return
  selectedPart.value = p
  ElMessage.info(`已选中 ${p.serial_no || p.drawing_no}，请扫码确认`)
}

function cancelSelect(): void {
  selectedPart.value = null
}

const unsub = onScan((code) => { void onScanCode(code) })

onBeforeUnmount(() => { unsub() })

async function onScanCode(rawCode: string): Promise<void> {
  const code = rawCode.trim()
  if (!code) return
  if (!selectedPart.value) {
    // 没选中件时,扫码直接忽略（避免误扫）
    return
  }
  if (submitting.value) return
  if (code !== (selectedPart.value.serial_no || selectedPart.value.drawing_no)) {
    ElMessage.error(`扫码与选中件不匹配 (期望 ${selectedPart.value.serial_no}, 扫到 ${code})`)
    return
  }
  if (!worker.value || !shelfId.value) return
  submitting.value = true
  try {
    await pickUpPart({
      serial_no: code,
      shelf_id: shelfId.value,
      badge_code: worker.value.badge_code,
    })
    ElMessage.success(`已领取: ${code}`)
    selectedPart.value = null
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '领取失败')
  } finally {
    submitting.value = false
  }
}

function backToAction(): void {
  selectedPart.value = null
  void router.replace('/scan/action')
}
</script>

<style lang="scss" scoped>
.scan-pick {
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
  background: #ecf5ff;
  border: 1px solid #d9ecff;
  border-radius: 8px;
  margin-bottom: 16px;
}
.confirm-text { flex: 1; color: #303133; }
.confirm-text strong {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: #409eff; font-size: 18px; margin: 0 4px;
}

.parts-list { display: flex; flex-direction: column; gap: 10px; }
.part-row {
  display: flex !important;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px !important;
  border-left: 4px solid #409eff;
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
  background: #409eff; color: #fff; border-radius: 50%;
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
.qty { color: #409eff; font-weight: 600; margin-right: 6px; }
.drawing { font-family: 'SF Mono', Menlo, Consolas, monospace; }
.holder { color: #e6a23c; }

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

.preview-content {
  display: flex; flex-direction: column; gap: 16px;
}
.barcode-section {
  display: flex; justify-content: center;
  padding: 16px; background: #fff; border-radius: 8px; border: 1px solid #ebeef5;
}
.file-section {
  padding: 12px; background: #f5f7fa; border-radius: 8px;
}
.file-section-title {
  font-weight: 600; font-size: 14px; margin: 0 0 8px; color: #303133;
}
</style>