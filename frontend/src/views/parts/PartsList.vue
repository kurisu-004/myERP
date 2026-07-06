<!--
  PartsList.vue

  零件一览表。

  - 进入页面自动查询「生产中 + 返修中」零件
  - 状态 / 加急使用 checkbox 多选，勾选后即时查询
  - 图号 + 名称共用一个搜索框，前缀匹配
  - 新增「所在位置」列：货架显示 code，工人显示姓名
  - 实际送货时间不在列表展示（详情页可见）
-->
<template>
  <div class="parts-list">
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="search.keyword"
          placeholder="图号 / 名称（前缀搜索）"
          clearable
          style="width: 260px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-popover placement="bottom-start" :width="200" trigger="click">
          <template #reference>
            <el-button :type="statusTags.length > 0 ? 'primary' : ''" plain>
              状态<el-tag
                v-if="statusTags.length > 0"
                size="small"
                round
                effect="dark"
                style="margin-left:6px"
              >{{ statusTags.length }}</el-tag>
            </el-button>
          </template>
          <el-checkbox-group v-model="search.statuses" @change="onSearch">
            <div v-for="opt in statusOptions" :key="opt.value" style="margin-bottom:6px">
              <el-checkbox :value="opt.value" :label="opt.label" />
            </div>
          </el-checkbox-group>
        </el-popover>

        <el-popover placement="bottom-start" :width="140" trigger="click">
          <template #reference>
            <el-button :type="search.isUrgent !== null ? 'primary' : ''" plain>
              加急<el-tag
                v-if="search.isUrgent !== null"
                size="small"
                round
                effect="dark"
                style="margin-left:6px"
              >1</el-tag>
            </el-button>
          </template>
          <el-checkbox-group
            :model-value="urgentCheckboxModel"
            @update:model-value="onUrgentChange"
          >
            <div style="margin-bottom:6px">
              <el-checkbox value="urgent" label="加急" />
            </div>
            <div style="margin-bottom:6px">
              <el-checkbox value="normal" label="非加急" />
            </div>
          </el-checkbox-group>
        </el-popover>

        <el-tag v-if="isCncProgrammer" type="warning" effect="plain" size="small">
          编程员视图：默认查看「编程中」零件
        </el-tag>
        <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
      </div>
    </el-card>

    <div class="sheet-wrapper">
      <el-table
        :data="items"
        v-loading="loading"
        stripe
        border
        style="width: 100%"
        size="small"
        :default-sort="{ prop: 'planned_delivery_date', order: sortDir === 'ASC' ? 'ascending' : 'descending' }"
        @sort-change="onSortChange"
        :empty-text="emptyText"
      >
        <el-table-column prop="serial_no" label="序列号" width="110" fixed="left" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ 'muted': !row.serial_no }">{{ row.serial_no || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="drawing_no" label="图号" width="130" fixed="left" show-overflow-tooltip />
        <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <router-link :to="`/parts/${row.id}`" class="name-link">
              {{ row.name }}
            </router-link>
          </template>
        </el-table-column>
        <el-table-column prop="quantity" label="数量" width="80" align="right" />
        <el-table-column prop="planned_delivery_date" label="计划交期" width="120" sortable="custom" />

        <el-table-column prop="is_urgent" label="加急" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_urgent" type="danger" effect="dark" size="small">加急</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" effect="plain" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="customer_path" label="客户" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.customer_path">{{ row.customer_path }}</span>
            <span v-else-if="row.customer_name" class="muted">{{ row.customer_name }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="所在位置" width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.current_holder_kind === 'shelf' && row.shelf_code">
              货架 {{ row.shelf_code }}
            </span>
            <span v-else-if="row.current_holder_kind === 'worker' && row.worker_name">
              {{ row.worker_name }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="下一道工序" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.next_process_id && processNameById[row.next_process_id]" type="primary" size="small" effect="plain">
              {{ processNameById[row.next_process_id] }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="装配" width="80" align="center">
          <template #default="{ row }">
            <el-tag
              v-if="row.assembly_id != null"
              type="primary"
              size="small"
              effect="plain"
              class="assembly-link"
              @click.stop="$router.push(`/parts/${row.id}/assembly`)"
            >
              <el-icon><Connection /></el-icon>
              <span>装配件</span>
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">详情</el-button>
            <el-button
              v-if="row.status === 'PENDING'"
              link
              type="success"
              size="small"
              @click="onDispatch(row as PartItem)"
            >下发</el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty :description="emptyText" />
        </template>
      </el-table>
    </div>

    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        background
        size="small"
        @current-change="fetchList"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- 下发对话框 -->
    <el-dialog
      v-model="dispatchVisible"
      :title="dispatchMode === 'cnc' ? '发送至 CNC 编程' : '下发零件'"
      width="480px"
      @closed="onDispatchClosed"
    >
      <el-form label-width="96px">
        <el-form-item label="下发方式">
          <el-radio-group v-model="dispatchMode">
            <el-radio value="direct">直接下到生产货架</el-radio>
            <el-radio value="cnc">发送至 CNC 编程</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="dispatchMode === 'direct'">
          <el-form-item label="目标货架" required>
            <el-select
              v-model="dispatchShelfId"
              placeholder="选择生产货架"
              style="width: 100%"
              filterable
            >
              <el-option
                v-for="s in shelves"
                :key="s.id"
                :label="s.name"
                :value="s.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="下一道工序" required>
            <el-select
              v-model="dispatchNextProcessId"
              placeholder="选择工序（必填）"
              style="width: 100%"
              filterable
            >
              <el-option
                v-for="p in processes"
                :key="p.id"
                :label="`${p.code} / ${p.name}`"
                :value="p.id"
              />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item v-else>
          <el-alert
            type="info"
            :closable="false"
            title="将零件发送至 CNC 编程环节，零件状态变为「编程中」。"
            description="CNC 编程员在「待编程一览」中下载图纸、上传 G 代码后，会再下发到生产货架。"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dispatchVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="dispatchSubmitting"
          :disabled="dispatchMode === 'direct' && (!dispatchShelfId || !dispatchNextProcessId)"
          @click="onDispatchConfirm"
        >
          {{ dispatchMode === 'cnc' ? '发送至 CNC 编程' : '确认下发' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, Connection } from '@element-plus/icons-vue'
import {
  listParts,
  placeOnShelf,
  sendToProgramming,
  type ListPartsParams,
  type PartItem,
} from '@/api/parts'
import { listShelves } from '@/api/shelves'
import type { Shelf } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import type { Process } from '@/types/process'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  type OrderStatus,
  type PartSortKey,
  type SortDir,
} from '@/types/parts'
import { useAuthSession } from '@/composables/useAuthSession'

// ============ 搜索条件 ============
// CNC_PROGRAMMER 默认只看编程中；其他角色保持现状（生产中 + 返修中）。
const { hasRole } = useAuthSession()
const isCncProgrammer = hasRole('CNC_PROGRAMMER')
const initialSearch = () => ({
  keyword: '',
  statuses: (isCncProgrammer
    ? ['PROGRAMMING']
    : ['IN_PROCESS', 'REPAIRING']) as OrderStatus[],
  isUrgent: null as boolean | null,
})
const search = reactive(initialSearch())

const statusTags = computed(() => search.statuses)

const urgentCheckboxModel = computed<string[]>(() => {
  const out: string[] = []
  if (search.isUrgent === true) out.push('urgent')
  if (search.isUrgent === false) out.push('normal')
  return out
})

function onUrgentChange(vals: unknown): void {
  const arr: string[] = Array.isArray(vals) ? vals.map(String) : []
  const hasUrgent = arr.includes('urgent')
  const hasNormal = arr.includes('normal')
  if (hasUrgent && hasNormal) {
    search.isUrgent = null // 两者都选 = 不限
  } else if (hasUrgent) {
    search.isUrgent = true
  } else if (hasNormal) {
    search.isUrgent = false
  } else {
    search.isUrgent = null
  }
  onSearch()
}

const statusOptions: { value: OrderStatus; label: string }[] = (
  Object.keys(ORDER_STATUS_LABEL) as OrderStatus[]
).map((v) => ({ value: v, label: ORDER_STATUS_LABEL[v] }))

// ============ 表格数据 / 分页 / 排序 ============
const items = ref<PartItem[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(20)
const sortBy = ref<PartSortKey>('PLANNED_DELIVERY_DATE')
const sortDir = ref<SortDir>('ASC')

const emptyText = computed(() => errorMsg.value ?? '暂无符合条件的零件')

function statusLabel(s: OrderStatus): string {
  return ORDER_STATUS_LABEL[s] ?? s
}
function statusTagType(s: OrderStatus): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return ORDER_STATUS_TAG_TYPE[s] ?? 'info'
}

function buildParams(): ListPartsParams {
  return {
    statuses: search.statuses.length > 0 ? search.statuses : undefined,
    is_urgent: search.isUrgent ?? undefined,
    keyword: search.keyword.trim() || undefined,
    sort_by: sortBy.value,
    sort_dir: sortDir.value,
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
  }
}

async function fetchList(): Promise<void> {
  loading.value = true
  errorMsg.value = null
  try {
    const resp = await listParts(buildParams())
    items.value = resp.items
    total.value = resp.total
  } catch (e) {
    items.value = []
    total.value = 0
    errorMsg.value = (e as Error).message ?? '查询失败'
    ElMessage.error(errorMsg.value)
  } finally {
    loading.value = false
  }
}

// 工序名查找
const processNameById = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const p of processes.value) map[p.id] = p.name
  return map
})

onMounted(() => {
  void fetchList()
  // 预拉取工序列表（用于下一道工序列显示）
  listProcesses({ limit: 200 }).then(res => { processes.value = res.items }).catch(() => {})
})

// ============ 搜索 / 排序 / 分页 ============
const onSearch = (): void => {
  page.value = 1
  void fetchList()
}
const onSortChange = ({
  prop,
  order,
}: {
  prop: string | null
  order: 'ascending' | 'descending' | null
}): void => {
  if (prop === 'planned_delivery_date') {
    sortBy.value = 'PLANNED_DELIVERY_DATE'
  }
  if (order === 'ascending') sortDir.value = 'ASC'
  else if (order === 'descending') sortDir.value = 'DESC'
  void fetchList()
}
const onPageSizeChange = (size: number): void => {
  pageSize.value = size
  page.value = 1
  void fetchList()
}

// ============ 下发 ============
// shelf_id 在前端保持字符串：雪花 ID 长度 > 2^53，`Number(s.id)` 会丢精度
// （实测 `Number("198362487928651776")` → "198362487928651780"，差 4）。
// 后端 Pydantic v2 默认 lax 模式会从 JSON string 自动 coerce 到 int。
const shelves = ref<Shelf[]>([])
const processes = ref<Process[]>([])
const dispatchVisible = ref(false)
const dispatchShelfId = ref<string | null>(null)
const dispatchNextProcessId = ref<string | null>(null)
const dispatchPartId = ref<string | null>(null)
const dispatchSubmitting = ref(false)
/** 下发方式：direct = 直接放到生产货架；cnc = 发送至 CNC 编程。 */
const dispatchMode = ref<'direct' | 'cnc'>('direct')

async function onDispatch(row: PartItem): Promise<void> {
  dispatchPartId.value = row.id
  dispatchShelfId.value = null
  dispatchNextProcessId.value = null
  dispatchMode.value = 'direct'
  // 拉取生产货架列表 + 工序列表
  try {
    const [shelfResp, procResp] = await Promise.all([
      listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 }),
      listProcesses({ limit: 200 }),
    ])
    shelves.value = shelfResp.items
    processes.value = procResp.items
  } catch {
    shelves.value = []
    processes.value = []
  }
  dispatchVisible.value = true
}

function onDispatchClosed(): void {
  dispatchPartId.value = null
  dispatchShelfId.value = null
  dispatchNextProcessId.value = null
  dispatchMode.value = 'direct'
}

async function onDispatchConfirm(): Promise<void> {
  if (!dispatchPartId.value) return
  if (dispatchMode.value === 'direct'
      && (!dispatchShelfId.value || !dispatchNextProcessId.value)) return
  dispatchSubmitting.value = true
  try {
    if (dispatchMode.value === 'cnc') {
      await sendToProgramming(dispatchPartId.value)
      ElMessage.success('已发送至 CNC 编程')
    } else {
      await placeOnShelf(
        dispatchPartId.value, dispatchShelfId.value!, dispatchNextProcessId.value!,
      )
      ElMessage.success('下发成功')
    }
    dispatchVisible.value = false
    void fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '下发失败')
  } finally {
    dispatchSubmitting.value = false
  }
}
</script>

<style lang="scss" scoped>
.parts-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.filter-card {
  :deep(.el-card__body) {
    padding: 12px 16px;
  }
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.total-hint {
  font-size: 13px;
  color: var(--text-secondary);
  margin-left: auto;
}

.sheet-wrapper {
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  padding: 4px;
  overflow-x: auto;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  padding: 0 4px;
}

.muted {
  color: var(--text-secondary);
}

.name-link {
  color: var(--primary-color);
  text-decoration: none;

  &:hover {
    text-decoration: underline;
  }
}

.assembly-link {
  cursor: pointer;
  user-select: none;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  &:hover {
    background: var(--primary-color) !important;
    color: #fff !important;
  }
}
</style>
