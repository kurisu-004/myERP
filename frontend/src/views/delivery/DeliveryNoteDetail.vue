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
  canPrint,
  canRecall,
  canSoftDelete,
  canSubmit,
  hasManageNoteRole,
} from '@/utils/deliveryNotePermissions'
import { useAuthSession } from '@/composables/useAuthSession'
import { useColumnVisibility } from '@/composables/useColumnVisibility'  // 2026-08-02
import PartPickerDialog from '@/components/delivery/PartPickerDialog.vue'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'  // 2026-08-02
import PrintPreviewDialog from '@/components/delivery/PrintPreviewDialog.vue'  // 2026-08-02

const route = useRoute()
const router = useRouter()
const { hasRole } = useAuthSession()

const role = computed(() => ({
  MANAGER: hasRole('MANAGER'),
  CLERK: hasRole('CLERK'),
  INSPECTOR: hasRole('INSPECTOR'),
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
// 2026-08-02：打印改为「预览 → 拖动 → 确认导出」两段式。
// 真实下载触发挪到 PrintPreviewDialog.onConfirm。
// 2026-08-07：拆为「打印送货单」「打印标签」两个按钮，共用同一个 dialog，
// 靠 previewMode 切换行为。下载触发仍在 dialog.onConfirm。
// ============================================================
const previewVisible = ref(false)
const previewMode = ref<'note' | 'label'>('note')

function onPrint() {
  previewMode.value = 'note'
  previewVisible.value = true
}

function onPrintLabels() {
  previewMode.value = 'label'
  previewVisible.value = true
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

function deliveryLineRowClassName({ row }: { row: any }): string {
  // 虚拟装配件父行的 urgent 取任一子件加急（与子件红底联动）
  if (row.is_asm_row) {
    return row.is_urgent ? 'row-urgent' : ''
  }
  return row.is_urgent ? 'row-urgent' : ''
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

// 2026-08-01：客户端排序（详情一次性返回所有 line_items；null 强制末尾）。
// 用 @sort-change 而不是 sortable="custom"，因为所有数据都在内存中；
// sort-change 默认行为已含 null 兜底（null 排在末尾），但这里用显式
// 排序保持与后端一致：null 永远在末尾。
function onLineItemSort({
  prop,
  order,
}: {
  prop: string | null
  order: 'ascending' | 'descending' | null
}): void {
  if (!note.value || !prop || !order) return
  const dir = order === 'ascending' ? 1 : -1
  note.value.line_items.sort((a: any, b: any) => {
    const av = a[prop]
    const bv = b[prop]
    if (av == null && bv == null) return 0
    if (av == null) return 1
    if (bv == null) return -1
    if (av < bv) return -1 * dir
    if (av > bv) return 1 * dir
    return 0
  })
}

// 2026-08-02：零件列表列显隐（selection/index 始终可见不放进 defs）
const columnDefs = [
  { key: 'batch_label', label: '批次' },
  { key: 'serial_no', label: '序列号' },
  { key: 'drawing_no', label: '图号' },
  { key: 'order_no', label: '订单号' },
  { key: 'name', label: '名称' },
  { key: 'customer', label: '客户（二级）' },
  { key: 'applicant_name', label: '申请人' },
  { key: 'quantity', label: '数量' },
  { key: 'request_date', label: '请购日期' },
  { key: 'planned_delivery_date', label: '计划交期' },
  { key: 'system_delivery_date', label: '系统交期' },
  { key: 'note', label: '备注' },
  { key: 'status', label: '状态' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, {
  listKey: 'delivery_note_detail_line_items',
})

// ============================================================
// 2026-08-04：装配件父行 + 可折叠子件行（参照 PartsList L271-278 / L1378-1399 模式，
// 但走本地分组而非懒加载——详情已一次性返回全量 line_items，避免批次量语义不一致）
// ============================================================
interface AssemblyTreeRow extends DeliveryNoteLineItem {
  is_asm_row?: boolean
  has_children?: boolean
  children?: DeliveryNoteLineItem[]
  unit?: string
}
const treeLineItems = computed<AssemblyTreeRow[]>(() => {
  if (!note.value) return []
  const flat = note.value.line_items
  const asmGroups = new Map<string, DeliveryTreeNode[]>()
  const loose: DeliveryNoteLineItem[] = []
  flat.forEach((li) => {
    if (li.assembly_id) {
      const arr = asmGroups.get(li.assembly_id) ?? []
      arr.push(li)
      asmGroups.set(li.assembly_id, arr)
    } else {
      loose.push(li)
    }
  })
  const result: AssemblyTreeRow[] = []
  const insertedAsm = new Set<string>()
  flat.forEach((li) => {
    if (!li.assembly_id) {
      result.push(li as AssemblyTreeRow)
      return
    }
    if (insertedAsm.has(li.assembly_id)) return
    const children = asmGroups.get(li.assembly_id) ?? []
    result.push({
      id: `ASM_${li.assembly_id}`,
      is_asm_row: true,
      has_children: true,
      assembly_id: li.assembly_id,
      assembly_serial_no: li.assembly_serial_no,
      assembly_drawing_no: li.assembly_drawing_no,
      assembly_name: li.assembly_name,
      assembly_order_no: li.assembly_order_no,
      // 父行各列展示值（沿用 line_item 列字段，让 el-table 排序/模板不分支）
      serial_no: li.assembly_serial_no ?? '',
      drawing_no: li.assembly_drawing_no ?? '',
      order_no: li.assembly_order_no ?? '',
      name: li.assembly_name ?? '',
      applicant_name: children[0]?.applicant_name ?? '',
      customer_name: children[0]?.customer_name ?? '',
      customer_path: children[0]?.customer_path ?? '',
      quantity: 1,
      unit: '套',
      note: '',
      is_urgent: children.some((c) => c.is_urgent),
      status: 'INSPECTION', // 仅占位（父行不展示 status 列）
      batch_label: null,
      batch_no: null,
      part_id: '',
      request_date: null,
      planned_delivery_date: children[0]?.planned_delivery_date ?? null,
      system_delivery_date: null,
      is_scanned: false,
      scanned: false,
      parent_customer_name: children[0]?.parent_customer_name ?? null,
      children,
    })
    insertedAsm.add(li.assembly_id)
  })
  return result
})

// (alias for ts strict mode)
type DeliveryTreeNode = DeliveryNoteLineItem
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
              <!-- 2026-08-02：列显隐控制 -->
              <ColumnVisibilityPopover
                :defs="columnDefs"
                :model-value="columnVisibility.currentMap"
                @update:model-value="columnVisibility.update"
                @reset="columnVisibility.showAll"
              />
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
          :data="treeLineItems"
          row-key="id"
          :row-class-name="deliveryLineRowClassName"
          :tree-props="{ children: 'children', hasChildren: 'has_children' }"
          stripe
          border
          height="500"
          highlight-current-row
          @selection-change="(rows: any[]) => selectedItemIds = rows.map(r => r.id)"
          @sort-change="onLineItemSort"
        >
          <!-- selection / index 始终可见，不放 defs -->
          <el-table-column
            v-if="canEdit"
            type="selection"
            width="50"
            :selectable="(row: any) => !row.is_asm_row"
          />
          <el-table-column type="index" label="#" width="50" />
          <!-- 2026-08-02：每列加 v-if；订单号搬到图号/名称之间 -->
          <el-table-column
            v-if="columnVisibility.isVisible('batch_label')"
            prop="batch_label" label="批次" min-width="100" sortable align="center">
            <template #default="{ row }">
              <template v-if="row.is_asm_row">—</template>
              <template v-else>{{ row.batch_label || '—' }}</template>
            </template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('serial_no')"
            prop="serial_no" label="序列号" min-width="120" sortable align="center">
            <template #default="{ row }">
              <span :class="{ muted: !row.serial_no }">{{ row.serial_no || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('drawing_no')"
            prop="drawing_no" label="图号" min-width="140" sortable align="center"/>
          <el-table-column
            v-if="columnVisibility.isVisible('order_no')"
            prop="order_no" label="订单号" min-width="120" show-overflow-tooltip sortable align="center">
            <template #default="{ row }">{{ row.order_no || '—' }}</template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('name')"
            label="名称" min-width="200" sortable align="center">
            <template #default="{ row }">
              <template v-if="row.is_asm_row">
                <el-tag type="warning" size="small" class="asm-tag">装配件</el-tag>
                <router-link
                  :to="`/assemblies/${row.assembly_id}`"
                  class="assembly-link"
                >
                  {{ row.name }}
                </router-link>
              </template>
              <template v-else>{{ row.name }}</template>
            </template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('customer')"
            prop="customer_name"
            label="客户（二级）" min-width="160" show-overflow-tooltip sortable align="center">
            <template #default="{ row }">
              <span>{{ row.customer_path ?? row.customer_name ?? '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('applicant_name')"
            prop="applicant_name" label="申请人" min-width="100" sortable align="center">
            <template #default="{ row }">{{ row.applicant_name || '—' }}</template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('quantity')"
            label="数量" min-width="80" sortable align="center">
            <template #default="{ row }">
              <template v-if="row.is_asm_row">
                <strong>1</strong> <span class="muted">套</span>
              </template>
              <template v-else>{{ row.quantity }}</template>
            </template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('request_date')"
            label="请购日期" min-width="120" align="center">
            <template #default="{ row }">{{ row.request_date || '—' }}</template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('planned_delivery_date')"
            prop="planned_delivery_date"
            label="计划交期" min-width="120" sortable align="center">
            <template #default="{ row }">{{ row.planned_delivery_date || '—' }}</template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('system_delivery_date')"
            prop="system_delivery_date"
            label="系统交期" min-width="120" sortable align="center">
            <template #default="{ row }">{{ row.system_delivery_date || '—' }}</template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('note')"
            label="备注" min-width="120" show-overflow-tooltip align="center">
            <template #default="{ row }">{{ row.note || '—' }}</template>
          </el-table-column>
          <el-table-column
            v-if="columnVisibility.isVisible('status')"
            label="状态" min-width="120" align="center">
            <template #default="{ row }">
              <template v-if="row.is_asm_row">—</template>
              <el-tag
                v-else
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
              v-if="canSubmit(note.status, role)"
              type="primary"
              @click="onSubmit"
            >
              提交
            </el-button>
            <el-button
              v-if="canRecall(note.status, role)"
              type="warning"
              @click="onRecall"
            >
              撤回
            </el-button>
            <el-button
              v-if="canPrint(role, note.part_count)"
              type="success"
              @click="onPrint"
            >
              打印送货单
            </el-button>
            <el-button
              v-if="canPrint(role, note.part_count)"
              type="success"
              plain
              @click="onPrintLabels"
            >
              打印标签
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

    <!-- 2026-08-02：打印预览对话框（拖动行可调整顺序，确认后导出 XLSX）
         2026-08-07：mode 决定「只导送货单」/「只导标签（可勾选）」 -->
    <PrintPreviewDialog
      v-model="previewVisible"
      :note="note"
      :mode="previewMode"
    />
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

.asm-tag { margin-right: 6px; }
.assembly-link {
  color: var(--primary-color);
  text-decoration: none;
}
.assembly-link:hover { text-decoration: underline; }
.muted { color: var(--text-secondary); }

:deep(.el-table__row.row-urgent) > td.el-table__cell {
  background-color: #fde2e2 !important;
}
:deep(.el-table__row.row-urgent:hover > td.el-table__cell) {
  background-color: #fbcaca !important;
}

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
</style>
