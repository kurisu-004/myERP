<!--
  送货单详情（PR-G 2026-07-22 新增）

  形态对齐 frontend/src/views/parts/PartDetail.vue
  不同点：本页主操作是「添加零件」「移除零件」「提交」「撤回」「软删」
  以及「扫码领取」链接（仅 SUBMITTED 状态跳司机扫码台）。
-->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  addParts,
  getNote,
  listNoteEvents,
  printNote,
  removeParts,
  recallNote,
  softDeleteNote,
  submitNote,
  updateNote,
  type AddPartsItem,
} from '@/api/deliveryNote'
import {
  DELIVERY_NOTE_STATUS_LABEL,
  DELIVERY_NOTE_STATUS_TAG,
  type DeliveryNoteDetailOut,
  type DeliveryNoteEventOut,
  type DeliveryNoteLineItem,
  type DeliveryNoteStatus,
  formatNoteEventLabel,
} from '@/types/deliveryNote'
// 2026-07-23 R2-C：复用 PartsList 的状态显示样式 / 标签色映射
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  type OrderStatus,
} from '@/types/parts'
import {
  canAddRemoveParts,
  canRecall,
  canSoftDelete,
  canSubmit,
} from '@/utils/deliveryNotePermissions'
import { useAuthSession } from '@/composables/useAuthSession'
import PartPickerDialog from '@/components/delivery/PartPickerDialog.vue'

const route = useRoute()
const router = useRouter()
const { hasRole } = useAuthSession()

const role = computed(() => ({
  MANAGER: hasRole('MANAGER'),
  CLERK: hasRole('CLERK'),
}))

const note = ref<DeliveryNoteDetailOut | null>(null)
const events = ref<DeliveryNoteEventOut[]>([])
const loading = ref(false)
/** 详情页可编辑送货日期（DRAFT / SUBMITTED）；PICKED_UP / ARCHIVED 时控件 disabled */
const editDeliveryDate = ref<string>('')

async function fetchDetail() {
  const id = route.params.id as string
  if (!id) return
  loading.value = true
  try {
    note.value = await getNote(id)
    events.value = await listNoteEvents(id)
    editDeliveryDate.value = note.value?.delivery_date ?? ''
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(fetchDetail)
watch(() => route.params.id, fetchDetail)

async function onDeliveryDateChange(newDate: string | null) {
  if (!note.value) return
  const normalized = newDate ?? ''
  if (normalized === (note.value.delivery_date ?? '')) return
  try {
    await updateNote(note.value.id, {
      version: note.value.version,
      delivery_date: normalized,
    })
    ElMessage.success('已更新送货日期')
    await fetchDetail()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { code?: number } } }
    if (err?.response?.data?.code === 21403 /* BIZ_VERSION_CONFLICT */) {
      ElMessage.warning('该记录已被其他用户修改，请刷新后重试')
    } else {
      ElMessage.error((e as Error).message ?? '更新送货日期失败')
    }
    await fetchDetail()
  }
}

// ============================================================
// 状态机迁移操作
// ============================================================
async function onSubmit() {
  if (!note.value) return
  try {
    await ElMessageBox.confirm(
      `确认提交 ${note.value.delivery_note_no}？`,
      '提交送货单', { type: 'warning' },
    )
  } catch { return }
  try {
    await submitNote(note.value.id, { version: note.value.version })
    ElMessage.success('已提交')
    fetchDetail()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '提交失败')
  }
}

async function onRecall() {
  if (!note.value) return
  try {
    await ElMessageBox.confirm(
      `确认撤回 ${note.value.delivery_note_no}？`,
      '撤回送货单', { type: 'warning' },
    )
  } catch { return }
  try {
    await recallNote(note.value.id, { version: note.value.version })
    ElMessage.success('已撤回')
    fetchDetail()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '撤回失败')
  }
}

async function onSoftDelete() {
  if (!note.value) return
  try {
    await ElMessageBox.confirm(
      `确认删除 ${note.value.delivery_note_no}？关联零件会解除。`,
      '删除送货单', { type: 'warning' },
    )
  } catch { return }
  try {
    await softDeleteNote(note.value.id, { version: note.value.version })
    ElMessage.success('已删除')
    router.push('/delivery-notes')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

// ============================================================
// 打印下载进度（单实例 ref；按钮右侧挂 <el-progress type="circle">）
// ============================================================
const dlProgress = ref<
  { loaded: number; total: number; state: 'downloading' | 'success' | 'error' } | null
>(null)

const pct = computed(() => {
  const p = dlProgress.value
  if (!p || !p.total) return p?.loaded ? 100 : 0
  return Math.min(100, Math.round((p.loaded / p.total) * 100))
})

function triggerBrowserDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function onPrint() {
  if (!note.value || dlProgress.value?.state === 'downloading') return
  dlProgress.value = { loaded: 0, total: 0, state: 'downloading' }
  try {
    const { blob, filename } = await printNote(note.value.id, (p) => {
      dlProgress.value = { ...dlProgress.value!, ...p }
    })
    triggerBrowserDownload(blob, filename)
    dlProgress.value = { ...dlProgress.value!, state: 'success' }
    setTimeout(() => {
      dlProgress.value = null
    }, 1500)
  } catch (e) {
    dlProgress.value = { ...dlProgress.value!, state: 'error' }
    ElMessage.error((e as Error).message ?? '打印失败')
    setTimeout(() => {
      dlProgress.value = null
    }, 2000)
  }
}

// ============================================================
// 添加零件对话框（2026-07-23：PartPickerDialog 勾选 UI 替换原 serial 输入）
// ============================================================
const addDialogOpen = ref(false)
const addBusy = ref(false)

function openAdd() {
  addDialogOpen.value = true
}

async function onPickerSubmit(items: AddPartsItem[]) {
  if (!note.value) return
  if (!items.length) return
  addBusy.value = true
  try {
    await addParts(note.value.id, {
      items, version: note.value.version,
    })
    ElMessage.success(`已添加 ${items.length} 批`)
    addDialogOpen.value = false
    await fetchDetail()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '添加失败')
  } finally {
    addBusy.value = false
  }
}

/** 当前单上已有批次 id 列表（2026-07-29：line_items.id = 批次 id；用于 PartPickerDialog 高亮禁用） */
const existingBatchIdsForPicker = computed(() => {
  if (!note.value) return []
  return note.value.line_items.map((it) => it.id)
})

// 2026-07-23 R2-C：line_items 状态列复用 PartsList 的标签映射
function partStatusLabel(s: OrderStatus | string): string {
  return (ORDER_STATUS_LABEL as Record<string, string>)[s] ?? String(s)
}
function partStatusTagType(
  s: OrderStatus | string,
): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return (ORDER_STATUS_TAG_TYPE as Record<string, 'primary' | 'success' | 'warning' | 'info' | 'danger'>)[s] ?? 'info'
}

// ============================================================
// 移除选定零件
// ============================================================
const selectedItemIds = ref<string[]>([])
async function onRemoveSelected() {
  if (!note.value) return
  if (!selectedItemIds.value.length) {
    ElMessage.warning('请勾选要移除的零件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认移除选中的 ${selectedItemIds.value.length} 件零件？`,
      '移除零件', { type: 'warning' },
    )
  } catch { return }
  try {
    await removeParts(note.value.id, {
      batch_ids: selectedItemIds.value,
      version: note.value.version,
    })
    ElMessage.success('已移除')
    selectedItemIds.value = []
    fetchDetail()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '移除失败')
  }
}

// ============================================================
// 派生
// ============================================================
const canAdd = computed(() => note.value && canAddRemoveParts(note.value.status, role.value))
const canEdit = computed(() => canAdd.value)
</script>

<template>
  <div v-loading="loading" class="delivery-note-detail">
    <template v-if="note">
      <el-page-header @back="$router.push('/delivery-notes')" class="page-header">
        <template #content>
          <span class="page-title">
            {{ note.delivery_note_no }}
            <el-tag
              :type="DELIVERY_NOTE_STATUS_TAG[note.status] || 'info'"
              size="small"
              effect="plain"
            >
              {{ DELIVERY_NOTE_STATUS_LABEL[note.status] }}
            </el-tag>
          </span>
        </template>
      </el-page-header>

      <el-card shadow="never" class="info-card">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="单号">{{ note.delivery_note_no }}</el-descriptions-item>
          <el-descriptions-item label="客户">
            {{ note.customer_path ?? note.customer_name ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="零件数">{{ note.part_count }}</el-descriptions-item>
          <el-descriptions-item label="送货日期">
            <el-date-picker
              v-model="editDeliveryDate"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="未设置"
              :disabled="note.status !== 'DRAFT' && note.status !== 'SUBMITTED'"
              style="width: 160px"
              @change="onDeliveryDateChange"
            />
          </el-descriptions-item>
          <el-descriptions-item label="提交时间">
            {{ note.submitted_at ? new Date(note.submitted_at).toLocaleString() : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="领取时间">
            {{ note.picked_up_at ? new Date(note.picked_up_at).toLocaleString() : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="司机">
            {{ note.driver_worker_name ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="备注" :span="3">
            {{ note.note || '—' }}
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never" class="line-items-card">
        <template #header>
          <div class="card-header">
            <span>零件列表 ({{ note.line_items.length }})</span>
            <div class="actions">
              <el-button
                v-if="canAdd"
                type="primary"
                size="small"
                @click="openAdd"
              >
                添加零件
              </el-button>
              <el-button
                v-if="canEdit && selectedItemIds.length"
                type="danger"
                size="small"
                @click="onRemoveSelected"
              >
                移除选中 ({{ selectedItemIds.length }})
              </el-button>
            </div>
          </div>
        </template>
        <el-table
          :data="note.line_items"
          stripe
          border
          height="500"
          @selection-change="(rows: any[]) => selectedItemIds = rows.map(r => r.id)"
        >
          <el-table-column
            v-if="canEdit"
            type="selection"
            width="50"
            :selectable="() => true"
          />
          <el-table-column type="index" label="#" width="50" />
          <el-table-column prop="batch_label" label="批次" min-width="100" align="center"/>
          <el-table-column prop="serial_no" label="序列号" min-width="120" align="center"/>
          <el-table-column prop="drawing_no" label="图号" min-width="140" align="center"/>
          <el-table-column prop="name" label="名称" min-width="180" align="center"/>
          <el-table-column label="客户（二级）" min-width="160" show-overflow-tooltip align="center">
            <template #default="{ row }">
              <span>{{ row.customer_path ?? row.customer_name ?? '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="applicant_name" label="申请人" min-width="100" align="center"/>
          <el-table-column prop="quantity" label="数量" min-width="70" align="center" />
          <el-table-column label="请购日期" min-width="120" align="center">
            <template #default="{ row }">{{ row.request_date || '—' }}</template>
          </el-table-column>
          <el-table-column label="计划交期" min-width="120" align="center">
            <template #default="{ row }">{{ row.planned_delivery_date || '—' }}</template>
          </el-table-column>
          <el-table-column label="系统交期" min-width="120" align="center">
            <template #default="{ row }">{{ row.system_delivery_date || '—' }}</template>
          </el-table-column>
          <el-table-column label="订单号" min-width="120" show-overflow-tooltip align="center">
            <template #default="{ row }">{{ row.order_no || '—' }}</template>
          </el-table-column>
          <el-table-column label="备注" min-width="120" show-overflow-tooltip align="center">
            <template #default="{ row }">{{ row.note || '—' }}</template>
          </el-table-column>
          <el-table-column label="状态" min-width="120" align="center">
            <template #default="{ row }">
              <el-tag
                :type="partStatusTagType(row.status)"
                effect="plain"
                size="small"
              >
                {{ partStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never" class="actions-card">
        <template #header>
          <span>状态操作</span>
        </template>
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
          <el-space wrap>
            <el-button
              v-if="note.status === 'DRAFT' && (role.MANAGER || role.CLERK)"
              type="primary"
              @click="onSubmit"
            >
              提交
            </el-button>
            <el-button
              v-if="note.status === 'SUBMITTED' && (role.MANAGER || role.CLERK)"
              type="warning"
              @click="onRecall"
            >
              撤回
            </el-button>
            <el-button
              v-if="(role.MANAGER || role.CLERK) && note.part_count > 0"
              type="success"
              :loading="dlProgress?.state === 'downloading'"
              @click="onPrint"
            >
              打印送货单
            </el-button>
          </el-space>
          <el-button
            v-if="canSoftDelete(note.status, role)"
            type="danger"
            plain
            @click="onSoftDelete"
          >
            删除草稿
          </el-button>
        </div>
      </el-card>

      <el-card shadow="never" class="events-card">
        <template #header><span>事件流</span></template>
        <el-timeline>
          <el-timeline-item
            v-for="e in events"
            :key="e.id"
            :timestamp="e.created_at ? new Date(e.created_at).toLocaleString() : ''"
          >
            <strong>{{ formatNoteEventLabel(e.event_type) }}</strong>
            <span v-if="e.from_status && e.to_status">
              ({{ e.from_status }} → {{ e.to_status }})
            </span>
            <div v-if="e.note" class="event-note">{{ e.note }}</div>
          </el-timeline-item>
        </el-timeline>
      </el-card>
    </template>

    <!-- 添加零件对话框（2026-07-23 改 PartPickerDialog 勾选 UI） -->
    <PartPickerDialog
      v-model="addDialogOpen"
      :customer-id="String(note?.customer_id ?? '')"
      :existing-batch-ids="existingBatchIdsForPicker"
      title="选择零件添加到本单"
      @submit="onPickerSubmit"
    />

    <!-- 右上角下载进度条卡片（fixed 定位，单实例） -->
    <div v-if="dlProgress" class="dl-tray" aria-live="polite">
      <div class="dl-card">
        <div class="dl-card-header">
          <span class="dl-card-name">{{ note?.delivery_note_no ?? '' }}</span>
          <span class="dl-card-pct">{{ pct }}%</span>
        </div>
        <el-progress
          type="line"
          :percentage="pct"
          :status="dlProgress.state === 'success' ? 'success'
                  : dlProgress.state === 'error' ? 'exception'
                  : undefined"
          :show-text="false"
          :stroke-width="8"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.delivery-note-detail { padding: 16px; }
.page-header { margin-bottom: 16px; }
.page-title { display: flex; align-items: center; gap: 12px; font-size: 18px; font-weight: 600; }
.info-card, .line-items-card, .actions-card, .events-card { margin-bottom: 16px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.actions { display: flex; gap: 8px; }
.event-note { font-size: 13px; color: #666; margin-top: 4px; }
.dl-tray {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 280px;
  pointer-events: none;
}
.dl-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
  padding: 8px 12px;
  pointer-events: auto;
}
.dl-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #606266;
  margin-bottom: 6px;
}
.dl-card-name {
  font-weight: 500;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 180px;
}
.dl-card-pct {
  font-variant-numeric: tabular-nums;
}
</style>
