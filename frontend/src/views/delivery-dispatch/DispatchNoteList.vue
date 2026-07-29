<!--
  DispatchNoteList.vue

  /delivery-dispatch/notes：司机送货 —— 待送货单选择 + 逐件扫描 + 确认送货。
  - 顶栏对齐报工台（渐变蓝）：司机名 + 工牌 + 送货 tag + 重新扫工牌。
  - 列出所有 SUBMITTED（待送货）送货单，卡片可点击展开。
  - 展开后加载该单零件（getNote → line_items + scanned_serials），司机逐件扫条码；
    每扫中一件 → pickupScan → 该件即时切「已扫描」态（绿底 + ✓）。
  - 全部扫齐（ready）→ ElMessageBox 确认 → pickup（driver_worker_id）→ 单据「已送货」、
    零件 DELIVERED；从列表移除该卡片。
  - 仅本页订阅 useBarcodeScanner；scan 仅在有展开卡片且非提交中时消费。

  依赖后端：GET /delivery-notes/pickup-pending、GET /delivery-notes/{id}、
  POST /delivery-notes/{id}/pickup-scan、POST /delivery-notes/{id}/pickup。
-->

<template>
  <div class="dispatch-notes">
    <div class="topbar">
      <div class="topbar-left">
        <el-icon :size="22" color="#fff"><Van /></el-icon>
        <span class="title">送货台</span>
        <el-divider direction="vertical" class="divider" />
        <span class="worker-name">{{ worker?.name ?? '—' }}</span>
        <el-tag size="default" type="danger" effect="dark" class="badge-tag">
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

    <div class="content" v-loading="loading">
      <h2 class="state-title">待送货单（{{ notes.length }}）</h2>
      <p class="scan-hint">点击送货单展开零件，逐件扫描条码；扫齐后确认送货。</p>

      <el-empty v-if="!loading && notes.length === 0" description="暂无待送货单" />

      <div v-else class="note-list">
        <el-card
          v-for="n in notes"
          :key="n.id"
          shadow="hover"
          class="note-card"
          :class="{ 'is-expanded': expandedId === n.id }"
        >
          <div class="note-head" @click="toggleExpand(n.id)">
            <div class="note-head-main">
              <span class="note-no">{{ n.delivery_note_no }}</span>
              <span class="note-customer">{{ n.customer_path ?? n.customer_name ?? '—' }}</span>
            </div>
            <div class="note-head-right">
              <span
                v-if="states[n.id]?.detail"
                class="scan-progress"
                :class="{ 'is-ready': states[n.id]?.ready }"
              >
                已扫 {{ states[n.id]?.scannedCount ?? 0 }} / {{ states[n.id]?.expectedCount ?? n.part_count }}
              </span>
              <el-tag v-else type="warning" size="small" effect="plain">
                {{ n.part_count }} 件
              </el-tag>
              <el-icon class="expand-caret" :class="{ open: expandedId === n.id }">
                <ArrowDown />
              </el-icon>
            </div>
          </div>

          <div v-if="expandedId === n.id" class="note-body" v-loading="states[n.id]?.loading">
            <div
              v-for="item in states[n.id]?.detail?.line_items ?? []"
              :key="item.id"
              class="line-item"
              :class="{ 'is-scanned': isScanned(n.id, item) }"
            >
              <el-icon class="scan-mark"><CircleCheck v-if="isScanned(n.id, item)" /><Clock v-else /></el-icon>
              <div class="line-main">
                <div class="line-top">
                  <span class="line-serial">{{ item.serial_no || item.drawing_no || item.id }}</span>
                  <el-tag v-if="item.batch_no" type="info" size="small" effect="plain">
                    批次{{ item.batch_no }}
                  </el-tag>
                  <span class="line-name">{{ item.name }}</span>
                </div>
                <div class="line-sub">
                  <span>× {{ item.quantity }}</span>
                  <span v-if="item.customer_path">· {{ item.customer_path }}</span>
                </div>
              </div>
              <el-tag
                :type="isScanned(n.id, item) ? 'success' : 'info'"
                size="small"
                :effect="isScanned(n.id, item) ? 'dark' : 'plain'"
              >
                {{ isScanned(n.id, item) ? '已扫描' : '待扫描' }}
              </el-tag>
            </div>

            <div class="note-body-footer">
              <span class="footer-hint">
                {{ states[n.id]?.ready ? '已扫齐，可确认送货' : '请继续扫描剩余零件条码' }}
              </span>
              <el-button
                type="danger"
                :disabled="!states[n.id]?.ready"
                :loading="states[n.id]?.finalizing"
                @click="confirmDelivery(n.id)"
              >
                <el-icon><Van /></el-icon>
                确认送货
              </el-button>
            </div>
          </div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowDown,
  CircleCheck,
  Clock,
  Refresh,
  Van,
} from '@element-plus/icons-vue'
import { getNote, listPickupPending, pickup, pickupScan } from '@/api/deliveryNote'
import type {
  DeliveryNoteDetailOut,
  DeliveryNoteLineItem,
  DeliveryNoteOut,
} from '@/types/deliveryNote'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import { useScanSession } from '@/composables/useScanSession'

interface NoteState {
  detail: DeliveryNoteDetailOut | null
  loading: boolean
  finalizing: boolean
  scanned: Set<string>
  scannedCount: number
  expectedCount: number
  ready: boolean
}

const router = useRouter()
const { onScan } = useBarcodeScanner()
const { worker, reset } = useScanSession()

const notes = ref<DeliveryNoteOut[]>([])
const loading = ref(false)
const expandedId = ref<string | null>(null)
const states = reactive<Record<string, NoteState>>({})

/** worker 缺失（直接输 URL / 刷新丢失）→ 回工牌页。 */
function requireWorker(): boolean {
  if (worker.value) return true
  void router.replace('/delivery-dispatch/badge')
  return false
}

async function fetchNotes(): Promise<void> {
  loading.value = true
  try {
    notes.value = await listPickupPending()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载待送货单失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (!requireWorker()) return
  void fetchNotes()
})

/** serial（后端以 serial_no 或 str(id) 作为扫描去重键）→ 是否已扫。 */
function scanKey(item: DeliveryNoteLineItem): string {
  return item.serial_no || item.id
}
function isScanned(noteId: string, item: DeliveryNoteLineItem): boolean {
  return states[noteId]?.scanned.has(scanKey(item)) ?? false
}

/** 2026-07-29 批次级：进度按行计——同一 serial 的多个批次行一次扫描全部勾掉。 */
function scannedLineCount(detail: DeliveryNoteDetailOut, scanned: Set<string>): number {
  return detail.line_items.filter((it) => scanned.has(scanKey(it))).length
}

function applyScanState(noteId: string, detail: DeliveryNoteDetailOut): void {
  const scanned = new Set(detail.scanned_serials)
  const expected = detail.line_items.length
  const count = scannedLineCount(detail, scanned)
  states[noteId] = {
    detail,
    loading: false,
    finalizing: states[noteId]?.finalizing ?? false,
    scanned,
    scannedCount: count,
    expectedCount: expected,
    ready: expected > 0 && count >= expected,
  }
}

async function toggleExpand(noteId: string): Promise<void> {
  if (expandedId.value === noteId) {
    expandedId.value = null
    return
  }
  expandedId.value = noteId
  if (states[noteId]?.detail) return
  states[noteId] = {
    detail: null,
    loading: true,
    finalizing: false,
    scanned: new Set(),
    scannedCount: 0,
    expectedCount: 0,
    ready: false,
  }
  try {
    const detail = await getNote(noteId)
    applyScanState(noteId, detail)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载零件失败')
    if (states[noteId]) states[noteId].loading = false
  }
}

const unsubscribe = onScan(async (code) => {
  const noteId = expandedId.value
  if (!noteId) return
  const st = states[noteId]
  if (!st?.detail || st.loading || st.finalizing) return
  const serial = code.trim()
  if (!serial) return
  if (st.scanned.has(serial)) {
    ElMessage.warning(`已扫过: ${serial}`)
    return
  }
  try {
    // 2026-07-23 Bug 4：后端 pickup-scan 只做硬校验（SUBMITTED + serial 属于本单），
    // 不再维护扫码进度（响应里 scanned_serials 恒空 / ready 恒 false）。
    // 进度由前端本地 Set 驱动：成功就 add，ready 基于本地 Set.size 判断。
    await pickupScan(noteId, {
      part_serial: serial,
      badge_code: worker.value?.badge_code ?? null,
    })
    st.scanned.add(serial)
    // 批次级：按行计数（同 serial 多批次行视为全部勾掉）
    st.scannedCount = scannedLineCount(st.detail, st.scanned)
    st.ready = st.expectedCount > 0 && st.scannedCount >= st.expectedCount
    ElMessage.success(`已扫: ${serial}`)
    if (st.ready) void confirmDelivery(noteId)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '扫码失败：该条码不属于本单')
  }
})

async function confirmDelivery(noteId: string): Promise<void> {
  const st = states[noteId]
  if (!st?.detail || !st.ready || st.finalizing) return
  try {
    await ElMessageBox.confirm(
      `已扫齐 ${st.expectedCount} 件，确认送货 ${st.detail.delivery_note_no}？`,
      '确认送货',
      { type: 'success', confirmButtonText: '确认送货', cancelButtonText: '再等等' },
    )
  } catch {
    return
  }
  st.finalizing = true
  try {
    await pickup(noteId, {
      driver_worker_id: worker.value!.id,
      version: st.detail.version,
      badge_code: worker.value?.badge_code ?? null,
    })
    ElMessage.success('已送货')
    notes.value = notes.value.filter((n) => n.id !== noteId)
    delete states[noteId]
    if (expandedId.value === noteId) expandedId.value = null
  } catch (e) {
    st.finalizing = false
    ElMessage.error((e as Error).message ?? '送货失败')
  }
}

function rescanBadge(): void {
  reset()
  void router.replace('/delivery-dispatch/badge')
}

onBeforeUnmount(() => {
  unsubscribe()
})
</script>

<style lang="scss" scoped>
.dispatch-notes {
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
.title { font-size: 18px; font-weight: 700; letter-spacing: 2px; }
.divider { background: rgba(255, 255, 255, 0.3); height: 20px; }
.worker-name { font-size: 18px; font-weight: 600; }
.badge-tag { font-family: 'SF Mono', Menlo, Consolas, monospace; }

.content {
  flex: 1;
  overflow: auto;
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
  padding: 24px;
}
.state-title { text-align: center; font-size: 24px; font-weight: 600; color: #303133; margin: 0 0 8px; }
.scan-hint { text-align: center; font-size: 14px; color: #909399; margin: 0 0 24px; }

.note-list { display: flex; flex-direction: column; gap: 16px; }

.note-card {
  border-radius: 10px;
  border-left: 4px solid #f56c6c;
  transition: box-shadow .15s;
}
.note-card.is-expanded { box-shadow: 0 4px 16px rgba(245, 108, 108, .18); }
:deep(.note-card .el-card__body) { padding: 0; }

.note-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px;
  cursor: pointer;
}
.note-head-main { display: flex; flex-direction: column; gap: 4px; }
.note-no { font-size: 20px; font-weight: 700; font-family: 'SF Mono', Menlo, Consolas, monospace; color: #303133; }
.note-customer { font-size: 14px; color: #606266; }
.note-head-right { display: flex; align-items: center; gap: 12px; }
.scan-progress { font-size: 15px; font-weight: 600; color: #e6a23c; }
.scan-progress.is-ready { color: #67c23a; }
.expand-caret { transition: transform .2s; color: #909399; }
.expand-caret.open { transform: rotate(180deg); }

.note-body {
  border-top: 1px solid #ebeef5;
  padding: 12px 18px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.line-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
  transition: background .15s, border-color .15s;
}
.line-item.is-scanned {
  background: #f0f9eb;
  border-color: #67c23a;
}
.scan-mark { font-size: 22px; color: #c0c4cc; flex-shrink: 0; }
.line-item.is-scanned .scan-mark { color: #67c23a; }
.line-main { flex: 1; min-width: 0; }
.line-top { display: flex; align-items: baseline; gap: 10px; }
.line-serial { font-size: 17px; font-weight: 600; font-family: 'SF Mono', Menlo, Consolas, monospace; }
.line-name { font-size: 14px; color: #606266; }
.line-sub { font-size: 13px; color: #909399; margin-top: 2px; display: flex; gap: 8px; }

.note-body-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
}
.footer-hint { font-size: 14px; color: #909399; }
</style>
