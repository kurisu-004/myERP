<!-- 外协接收列表页（2026-07-16 新增）

列出 status='OUTSOURCE' 的零件，接收 dialog 提供 2 个分支：
- 「生产货架」：OUTSOURCE → IN_PROCESS（生产分支）走原有 receiveFromOutsource
- 「品检货架」：OUTSOURCE → INSPECTION（新增分支）走 receiveFromOutsourceToInspection
  - 「自动通过品检」checkbox：勾选后连发 pass_inspection 一次性推到 READY_TO_SHIP
    （实现「送货流程」: OUTSOURCE → INSPECTION → READY_TO_SHIP 两步压缩）
-->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listCustomers, type Customer } from '@/api/customer'
import { listShelves } from '@/api/shelves'
import type { Shelf as ShelfItem } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import type { Process } from '@/types/process'
import {
  receiveFromOutsource,
  receiveFromOutsourceToInspection,
} from '@/api/parts'
// 临时：复用现有 TPart list 接口查 OUTSOURCE 状态（在 commit 6 完成之后
// 也可以换成专用 list_outsource_receivable，但目前 t_part.status='OUTSOURCE'
// 过滤已满足前端需求）。
import { listParts } from '@/api/parts'
import type { PartListItem } from '@/types/parts'

const loading = ref(false)
const items = ref<PartListItem[]>([])
const total = ref(0)
const query = reactive({
  keyword: '',
  customer_id: '',
  limit: 50,
  offset: 0,
})
const customers = ref<Customer[]>([])
const shelves = ref<ShelfItem[]>([])
const processes = ref<Process[]>([])

async function refresh(): Promise<void> {
  loading.value = true
  try {
    const r = await listParts({
      statuses: ['OUTSOURCE'],
      keyword: query.keyword || undefined,
      customer_id: query.customer_id || undefined,
      limit: query.limit,
      offset: query.offset,
    })
    items.value = r.items
    total.value = r.total
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载列表失败')
  } finally {
    loading.value = false
  }
}

async function loadLookups(): Promise<void> {
  try {
    customers.value = await listCustomers()
    const s = await listShelves({ is_active: true })
    shelves.value = s.items
    const p = await listProcesses({ limit: 200 })
    processes.value = p.items.filter((x) => x.category === 'INHOUSE')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '下拉数据加载失败')
  }
}

onMounted(async () => {
  await loadLookups()
  await refresh()
})

function onSearch(): void {
  query.offset = 0
  void refresh()
}
function onReset(): void {
  query.keyword = ''
  query.customer_id = ''
  query.offset = 0
  void refresh()
}

const dialogOpen = ref(false)
const target = ref<PartListItem | null>(null)
type Branch = 'production' | 'inspection'
const branch = ref<Branch>('production')
const selectedShelf = ref('')
const selectedProcess = ref('')
const autoPass = ref(false)
const submitting = ref(false)

function openReceive(row: PartListItem): void {
  target.value = row
  branch.value = 'production'
  selectedShelf.value = ''
  selectedProcess.value = ''
  autoPass.value = false
  dialogOpen.value = true
}

// 过滤货架：production 分支看 PRODUCTION 区，inspection 看 INSPECTION 区
const productionShelves = computed(() =>
  shelves.value.filter((s) => s.zone === 'PRODUCTION' && s.is_active),
)
const inspectionShelves = computed(() =>
  shelves.value.filter((s) => s.zone === 'INSPECTION' && s.is_active),
)

async function onConfirm(): Promise<void> {
  if (!target.value) return
  if (!selectedShelf.value) {
    ElMessage.warning('请选择货架')
    return
  }
  if (branch.value === 'production' && !selectedProcess.value) {
    ElMessage.warning('生产分支请选择下一道 INHOUSE 工序')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认接收「${target.value.drawing_no}」（${branchLabel}）？`,
      '接收外协件',
      { type: 'warning' },
    )
  } catch {
    return
  }
  submitting.value = true
  try {
    if (branch.value === 'production') {
      await receiveFromOutsource(target.value.id, {
        shelf_id: selectedShelf.value,
        next_process_id: selectedProcess.value,
      })
      ElMessage.success('已下发到生产货架')
    } else {
      await receiveFromOutsourceToInspection(target.value.id, {
        shelf_id: selectedShelf.value,
        auto_pass_inspection: autoPass.value,
      })
      ElMessage.success(
        autoPass.value
          ? '已送检并自动通过品检 → 待送货'
          : '已送检，等待品检',
      )
    }
    dialogOpen.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '操作失败')
  } finally {
    submitting.value = false
  }
}

const branchLabel = computed(() =>
  branch.value === 'production'
    ? '进入生产货架继续加工'
    : autoPass.value
      ? '品检 → 通过品检 → 进入待送货'
      : '品检',
)
</script>

<template>
  <div class="page">
    <el-card class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="query.keyword"
          placeholder="图号 / 名称 / 序列号"
          clearable
          style="width: 280px"
          @keyup.enter="onSearch"
        />
        <el-select
          v-model="query.customer_id"
          clearable
          placeholder="客户（L1）"
          style="width: 220px"
        >
          <el-option
            v-for="c in customers.filter((x) => x.parent_id === null)"
            :key="c.id"
            :label="c.name"
            :value="c.id"
          />
        </el-select>
        <el-button type="primary" @click="onSearch">查询</el-button>
        <el-button @click="onReset">重置</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="items" stripe border>
        <el-table-column prop="serial_no" label="序列号" width="100" />
        <el-table-column prop="drawing_no" label="图号" width="120" />
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column label="当前外协公司" width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag v-if="row.outsource_company_name" type="warning" size="small">
              {{ row.outsource_company_name }}
            </el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="下一道工序" width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.next_process_name || '—' }}</template>
        </el-table-column>
        <el-table-column prop="customer_path" label="客户" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              @click="openReceive(row as unknown as PartListItem)"
            >接收</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="query.offset"
        :total="total"
        :page-size="query.limit"
        layout="total, prev, pager, next, jumper"
        @current-change="refresh"
      />
    </el-card>

    <el-dialog v-model="dialogOpen" title="接收外协件" width="560">
      <el-form label-width="120px">
        <el-form-item label="接收分支">
          <el-radio-group v-model="branch">
            <el-radio value="production">进入生产货架</el-radio>
            <el-radio value="inspection">进入品检货架</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="branch === 'production' ? '生产货架' : '品检货架'" required>
          <el-select
            v-model="selectedShelf"
            :placeholder="branch === 'production' ? '选 PRODUCTION 区' : '选 INSPECTION 区'"
            filterable
            style="width: 100%"
          >
            <el-option
              v-for="s in (branch === 'production' ? productionShelves : inspectionShelves)"
              :key="s.id"
              :label="`${s.code} — ${s.name}`"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="branch === 'production'" label="下一道 INHOUSE" required>
          <el-select v-model="selectedProcess" filterable style="width: 100%">
            <el-option
              v-for="p in processes"
              :key="p.id"
              :label="`${p.code} — ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="branch === 'inspection'">
          <el-checkbox v-model="autoPass">
            通过品检后自动进入待送货
            <small>（勾选后连发 pass_inspection：OUTSOURCE → INSPECTION → READY_TO_SHIP）</small>
          </el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="onConfirm">
          确认接收（{{ branchLabel }}）
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
</style>
