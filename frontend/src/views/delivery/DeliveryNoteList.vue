<!--
  送货单一览（PR-G 2026-07-22 新增；2026-07-23 增强）

  - 文员 / MANAGER：filter (statuses × customer_id × keyword) → table → 操作
    (详情 / 提交 / 撤回 / 删除 / 打印)
  - 顶部「新建草稿」按钮：弹 el-dialog 选一级客户 + 送货日期 + 备注，
    勾选零件后原子创建 + 入件，跳详情页
  - 配送日期列、送货日期列
  - 打印按钮（list）：GET /delivery-notes/{id}/print → Axios blob + onDownloadProgress →
    按钮右侧 <el-progress type="circle"> 实时显示百分比

  形态对齐 frontend/src/views/outsource/OutsourceQuoteList.vue
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Van } from '@element-plus/icons-vue'

import {
  createNote as createNoteApi,
  listNotes,
  softDeleteNote,
  type AddPartsItem,
} from '@/api/deliveryNote'
import {
  DELIVERY_NOTE_STATUS_LABEL,
  DELIVERY_NOTE_STATUS_TAG,
  type DeliveryNoteOut,
  type DeliveryNoteStatus,
} from '@/types/deliveryNote'
import {
  canSoftDelete,
  defaultStatusesForRole,
  hasManageNoteRole,
} from '@/utils/deliveryNotePermissions'
import { listCustomers } from '@/api/customer'
import { useAuthSession } from '@/composables/useAuthSession'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useListStatePersist } from '@/composables/useListFilterPersist'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
import PartPickerDialog from '@/components/delivery/PartPickerDialog.vue'

const router = useRouter()
const route = useRoute()
const { hasRole } = useAuthSession()
const role = computed(() => ({
  MANAGER: hasRole('MANAGER'),
  CLERK: hasRole('CLERK'),
  INSPECTOR: hasRole('INSPECTOR'),
}))

// ============================================================
// 一览过滤
// ============================================================
const allStatuses: DeliveryNoteStatus[] = ['DRAFT', 'SUBMITTED', 'PICKED_UP', 'ARCHIVED']
const statuses = ref<DeliveryNoteStatus[]>(defaultStatusesForRole(role.value))
const customerId = ref<string>('')
const keyword = ref('')
const items = ref<DeliveryNoteOut[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(50)

// ============ 筛选状态持久化（2026-07-30 commit 4B）============
// 把 4 个离散 ref 包成一个对象传给 useListStatePersist；restore 后逐个 .value 写回。
// page 排除。优先级：URL ?statuses= > restore 快照 > 角色默认
const { restore: restoreNoteListFilter } = useListStatePersist(
  'delivery_note_list',
  { statuses, customerId, keyword, pageSize },
  { exclude: new Set(['page']) },
)

// ============ 列可见性 ============
// 「操作」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'delivery_note_no', label: '单号' },
  { key: 'delivery_date', label: '送货日期' },
  { key: 'customer', label: '客户' },
  { key: 'status', label: '状态' },
  { key: 'part_count', label: '零件数' },
  { key: 'submitted_at', label: '提交时间' },
  { key: 'picked_up_at', label: '领取时间' },
  { key: 'driver_worker_name', label: '司机' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'delivery_note_list' })

const customers = ref<{ id: string; name: string; path: string; parent_id: string | null }[]>([])

/** 一级客户视图：新建草稿弹框专用；list-filter 处仍用全集 */
const rootCustomers = computed(() =>
  customers.value.filter((c) => c.parent_id === null),
)

async function loadCustomers() {
  try {
    const list = await listCustomers()
    customers.value = list.map((c: any) => ({
      id: c.id,
      name: c.name,
      parent_id: c.parent_id ?? null,
      path: c.parent_name ? `${c.parent_name} / ${c.name}` : c.name,
    }))
  } catch (e) {
    // ignore
  }
}

async function fetchList() {
  loading.value = true
  try {
    const resp = await listNotes({
      statuses: statuses.value.length ? statuses.value : undefined,
      customer_id: customerId.value || undefined,
      keyword: keyword.value.trim() || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    items.value = resp.items
    total.value = resp.total
  } catch (e) {
    ElMessage.error((e as Error).message ?? '查询失败')
  } finally {
    loading.value = false
  }
}

function resetFilter() {
  statuses.value = defaultStatusesForRole(role.value)
  customerId.value = ''
  keyword.value = ''
  page.value = 1
  fetchList()
}

onMounted(async () => {
  await loadCustomers()
  // 2026-07-30 commit 4B：筛选项恢复（与 OutsourceQuoteList 同优先级）
  //   1) URL ?statuses=  → 最高优先
  //   2) restore() 快照里 statuses / customerId / keyword / pageSize
  //   3) 角色默认（已在 ref initializer 注入到 statuses.value；restore 不覆盖现有值）
  const urlStatusesRaw = route.query.statuses
  const urlStatuses: DeliveryNoteStatus[] = typeof urlStatusesRaw === 'string'
    ? urlStatusesRaw.split(',').filter((s): s is DeliveryNoteStatus =>
        allStatuses.includes(s as DeliveryNoteStatus))
    : []
  if (urlStatuses.length > 0) {
    statuses.value = [...urlStatuses]
  } else {
    const persisted = restoreNoteListFilter()
    if (persisted) {
      if (Array.isArray(persisted.statuses)) statuses.value = [...(persisted.statuses as DeliveryNoteStatus[])]
      if (typeof persisted.customerId === 'string') customerId.value = persisted.customerId as string
      if (typeof persisted.keyword === 'string') keyword.value = persisted.keyword as string
      if (typeof persisted.pageSize === 'number') pageSize.value = persisted.pageSize as number
    }
  }
  await fetchList()
})

// ============================================================
// 新建草稿对话框（2026-07-23 重写：送日期、零件勾选）
// ============================================================
const createDialogOpen = ref(false)
const createCustomerId = ref<string>('')
// 默认送货日期 = 今天 (YYYY-MM-DD 格式)
/** @type {import('vue').Ref<string>} */
const createDeliveryDate = ref<string>(formatToday())
const createNoteText = ref<string>('')
const creating = ref(false)
/** 候选弹框选出的 part id 列表（弹框 emit submit 时合并） */
const selectedItems = ref<AddPartsItem[]>([])
/** 候选弹框自身的可见性（PartPickerDialog 的 v-model） */
const pickerDialogOpen = ref(false)

function formatToday(): string {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function openCreate() {
  createCustomerId.value = ''
  createDeliveryDate.value = formatToday()
  createNoteText.value = ''
  selectedItems.value = []
  createDialogOpen.value = true
}

/** 当用户在弹框里勾完零件，按下「加入 (N)」时回传（2026-07-29 批次条目） */
function onPickerSubmit(items: AddPartsItem[]) {
  selectedItems.value = items
}

async function submitCreate() {
  if (!createCustomerId.value) {
    ElMessage.warning('请选择一级客户')
    return
  }
  if (!createDeliveryDate.value) {
    ElMessage.warning('请选择送货日期')
    return
  }
  creating.value = true
  try {
    const note = await createNoteApi({
      customer_id: createCustomerId.value,
      delivery_date: createDeliveryDate.value,
      items: selectedItems.value,
      note: createNoteText.value.trim() || null,
    })
    ElMessage.success(
      `已创建草稿 ${note.delivery_note_no}（含 ${selectedItems.value.length} 批）`,
    )
    createDialogOpen.value = false
    router.push(`/delivery-notes/${note.id}`)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '创建失败')
  } finally {
    creating.value = false
  }
}

// ============================================================
// 行操作
// 2026-08-07：操作栏瘦身——提交 / 撤回 / 打印（送货单 + 标签）按钮全部移除，
// 全部操作统一在详情页（DeliveryNoteDetail.vue）里完成。本页只保留：
//   · 详情（跳详情页）
//   · 删除（仅 DRAFT；CLERK / MANAGER）
// ============================================================
async function onSoftDelete(n: DeliveryNoteOut) {
  try {
    await ElMessageBox.confirm(
      `确认删除 ${n.delivery_note_no}（草稿）？关联零件会解除。`,
      '删除送货单',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await softDeleteNote(n.id, { version: n.version })
    ElMessage.success('已删除')
    fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}
</script>

<template>
  <div class="delivery-note-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline class="filter-form">
        <div style="display: flex; margin-bottom: 15px;">
          <el-form-item label="状态">
            <el-select
              v-model="statuses"
              multiple
              clearable
              placeholder="全部"
              style="width: 380px"
            >
              <el-option v-for="s in allStatuses" :key="s" :label="DELIVERY_NOTE_STATUS_LABEL[s]" :value="s" />
            </el-select>
          </el-form-item>
          <el-form-item label="客户">
            <el-select
              v-model="customerId"
              clearable
              filterable
              placeholder="全部"
              style="width: 200px"
            >
              <el-option v-for="c in customers" :key="c.id" :label="c.path" :value="c.id" />
            </el-select>
          </el-form-item>
        </div>

  
        <div style="display: flex; align-items: center; justify-content: space-between;">
          <div>
            <el-form-item label="单号">
              <el-input v-model="keyword" placeholder="DN-20260723-…" clearable style="width: 200px" />
            </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="page = 1; fetchList()">查询</el-button>
                <el-button @click="resetFilter">重置</el-button>
              </el-form-item>
          </div>
          
          <div>
            <el-button v-if="hasManageNoteRole(role)" type="success" @click="openCreate">
              <el-icon><Van /></el-icon>
              新建草稿
            </el-button>
          </div>


        </div>

      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div class="dnl-card-header">
          <ColumnVisibilityPopover
            :defs="columnDefs"
            :model-value="columnVisibility.currentMap" @update:model-value="columnVisibility.update"
            @reset="columnVisibility.showAll"
          />
        </div>
      </template>
    <el-table
      v-loading="loading"
      :data="items"
      :row-key="(r: DeliveryNoteOut) => r.id"
      :max-height="'calc(100vh - 360px)'"
      highlight-current-row
      stripe
      border
      :empty-text="loading ? '加载中' : '无数据'"
    >
      <el-table-column
        v-if="columnVisibility.isVisible('delivery_note_no')"
        prop="delivery_note_no" label="单号" min-width="180" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('delivery_date')"
        label="送货日期" min-width="120" align="center"
      >
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).delivery_date ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('customer')"
        label="客户" min-width="130" align="center"
      >
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).customer_path
            ?? (scope.row as DeliveryNoteOut).customer_name ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('status')"
        label="状态" min-width="80" align="center"
      >
        <template #default="scope">
          <el-tag
            :type="DELIVERY_NOTE_STATUS_TAG[(scope.row as DeliveryNoteOut).status] || 'info'"
            size="small"
            effect="plain"
          >
            {{ DELIVERY_NOTE_STATUS_LABEL[(scope.row as DeliveryNoteOut).status] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('part_count')"
        prop="part_count" label="零件数" min-width="70" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('submitted_at')"
        label="提交时间" min-width="170" align="center"
      >
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).submitted_at
            ? new Date((scope.row as DeliveryNoteOut).submitted_at!).toLocaleString() : '—' }}
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('picked_up_at')"
        label="领取时间" min-width="170" align="center"
      >
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).picked_up_at
            ? new Date((scope.row as DeliveryNoteOut).picked_up_at!).toLocaleString() : '—' }}
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('driver_worker_name')"
        prop="driver_worker_name" label="司机" min-width="80" align="center"
      >
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).driver_worker_name ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" min-width="120" fixed="right" align="center">
        <template #default="scope">
          <div style="display: flex; align-items: center; gap: 0px;">
            <el-button link type="primary" @click="$router.push(`/delivery-notes/${(scope.row as DeliveryNoteOut).id}`)">
              详情
            </el-button>
            <el-button
              v-if="canSoftDelete((scope.row as DeliveryNoteOut).status, role)"
              link
              type="danger"
              @click="onSoftDelete(scope.row as DeliveryNoteOut)"
            >
              删除
            </el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>
    </el-card>

    <el-pagination
      class="pager"
      :total="total"
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :page-sizes="[20, 50, 100]"
      layout="total, sizes, prev, pager, next"
      @current-change="fetchList"
      @size-change="fetchList"
    />

    <!-- 新建草稿对话框（2026-07-23 重写） -->
    <el-dialog v-model="createDialogOpen" title="新建送货单草稿" width="640px">
      <el-form label-width="90px">
        <el-form-item label="一级客户" required>
          <el-select
            v-model="createCustomerId"
            filterable
            placeholder="选择一级客户（L1 root）"
            style="width: 100%"
          >
            <el-option
              v-for="c in rootCustomers"
              :key="c.id"
              :label="c.name"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="送货日期" required>
          <el-date-picker
            v-model="createDeliveryDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择送货日期"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="createNoteText"
            type="textarea"
            :rows="2"
            maxlength="500"
            show-word-limit
            placeholder="可选备注"
          />
        </el-form-item>
        <el-form-item label="预选零件">
          <div class="picker-summary">
            <el-tag v-if="!createCustomerId" type="info" effect="plain">请先选客户</el-tag>
            <template v-else>
              <el-tag v-if="!selectedItems.length" type="warning" effect="plain">
                暂未勾选（可在弹出框里勾选 INSPECTION / READY_TO_SHIP 批次）
              </el-tag>
              <el-tag v-else type="success" effect="plain">
                已勾 {{ selectedItems.length }} 批
              </el-tag>
              <el-button
                size="small"
                type="primary"
                style="margin-left: 8px"
                @click="pickerDialogOpen = true"
              >
                {{ selectedItems.length ? '重新选择' : '选择零件' }}
              </el-button>
            </template>
          </div>
        </el-form-item>
      </el-form>

      <PartPickerDialog
        v-model="pickerDialogOpen"
        :customer-id="createCustomerId"
        @submit="onPickerSubmit"
      />

      <template #footer>
        <el-button @click="createDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">
          创建
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.delivery-note-list { padding: 16px; }
.filter-card :deep(.el-form-item) { margin-bottom: 0; }
.pager { margin-top: 16px; justify-content: flex-end; }
.dnl-card-header {
  display: flex;
  justify-content: flex-end;
  align-items: center;
}
.picker-summary {
  display: flex;
  align-items: center;
}
</style>
