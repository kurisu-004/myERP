<!-- 报价一览页 — 外协报价 CRUD + MANAGER 审批
     (2026-07-16 新增，仿 PartsList.vue 的 filter-card + el-table 范式)
-->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveOutsourceQuote,
  createOutsourceQuote,
  listApprovedForSend,
  listOutsourceQuotes,
  rejectOutsourceQuote,
  softDeleteOutsourceQuote,
  submitOutsourceQuote,
  updateOutsourceQuote,
} from '@/api/outsource'
import { listCustomers, type Customer } from '@/api/customer'
import { listOutsourceCompanies } from '@/api/outsource'
import { listProcesses } from '@/api/process'
import { listParts } from '@/api/parts'
import type { PartListItem } from '@/types/parts'
import type { Process } from '@/types/process'
import { useAuthSession } from '@/composables/useAuthSession'
import {
  OUTSOURCE_QUOTE_STATUS_LABEL,
  OUTSOURCE_QUOTE_STATUS_TAG,
  type OutsourceQuote,
  type OutsourceQuoteStatus,
} from '@/types/outsource'
import {
  canApprove,
  canCreate,
  canEdit,
  canReject,
  canSoftDelete,
  canSubmit,
  canWithdraw,
  rolesArrayToMap,
} from '@/utils/outsourceQuotePermissions'

const { user, hasRole } = useAuthSession()
const roleMap = computed(() => rolesArrayToMap(user.value?.roles ?? []))

const quotes = ref<OutsourceQuote[]>([])
const total = ref(0)
const loading = ref(false)
const query = reactive({
  keyword: '',
  statusList: [] as OutsourceQuoteStatus[],
  customer_id: '' as string,
  limit: 20,
  offset: 0,
})
const customers = ref<Customer[]>([])
const companies = ref<{ id: string; name: string }[]>([])
const processes = ref<Process[]>([])
const parts = ref<PartListItem[]>([])

async function refresh(): Promise<void> {
  loading.value = true
  try {
    const r = await listOutsourceQuotes({
      keyword: query.keyword || undefined,
      status: query.statusList[0],
      customer_id: query.customer_id || undefined,
      limit: query.limit,
      offset: query.offset,
    })
    quotes.value = r.items
    total.value = r.total
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载报价列表失败')
  } finally {
    loading.value = false
  }
}

async function loadLookups(): Promise<void> {
  try {
    customers.value = await listCustomers()
    const cs = await listOutsourceCompanies({ limit: 200 })
    companies.value = cs.items.map((c) => ({ id: c.id, name: c.name }))
    const ps = await listProcesses({ limit: 200 })
    processes.value = ps.items.filter((p) => p.category === 'OUTSOURCE')
    const pt = await listParts({ limit: 200 })
    parts.value = pt.items
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
  query.statusList = []
  query.customer_id = ''
  query.offset = 0
  void refresh()
}

const showCreate = ref(false)
const createForm = reactive({
  part_id: '',
  outsource_company_id: '',
  process_id: '',
  price: '',
  note: '',
})
async function onCreate(): Promise<void> {
  if (!createForm.part_id || !createForm.outsource_company_id || !createForm.process_id) {
    ElMessage.warning('请填写零件 / 公司 / 工序')
    return
  }
  try {
    await createOutsourceQuote({
      part_id: createForm.part_id,
      outsource_company_id: createForm.outsource_company_id,
      process_id: createForm.process_id,
      price: createForm.price || '0',
      note: createForm.note || null,
    })
    ElMessage.success('已创建 DRAFT 报价')
    showCreate.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '创建失败')
  }
}

async function onSubmit(q: OutsourceQuote): Promise<void> {
  try {
    await submitOutsourceQuote(q.id)
    ElMessage.success('已提交审核')
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '提交失败')
  }
}

const showApprove = ref(false)
const showReject = ref(false)
const reviewNote = ref('')
const activeQuote = ref<OutsourceQuote | null>(null)
function openApprove(q: OutsourceQuote): void {
  activeQuote.value = q
  reviewNote.value = ''
  showApprove.value = true
}
function openReject(q: OutsourceQuote): void {
  activeQuote.value = q
  reviewNote.value = ''
  showReject.value = true
}
async function onApprove(): Promise<void> {
  if (!activeQuote.value) return
  try {
    await approveOutsourceQuote(activeQuote.value.id, {
      version: activeQuote.value.version,
      review_note: reviewNote.value || null,
    })
    ElMessage.success('已通过')
    showApprove.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '审批失败')
  }
}
async function onReject(): Promise<void> {
  if (!activeQuote.value || !reviewNote.value.trim()) {
    ElMessage.warning('请填写拒绝原因')
    return
  }
  try {
    await rejectOutsourceQuote(activeQuote.value.id, {
      version: activeQuote.value.version,
      review_note: reviewNote.value.trim(),
    })
    ElMessage.success('已拒绝')
    showReject.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '拒绝失败')
  }
}

async function onDelete(q: OutsourceQuote): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定要软删报价 #${q.id}（${OUTSOURCE_QUOTE_STATUS_LABEL[q.status]}）？`,
      '确认操作',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await softDeleteOutsourceQuote(q.id)
    ElMessage.success('已软删')
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}
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
          v-model="query.statusList"
          multiple
          clearable
          placeholder="状态"
          style="width: 220px"
        >
          <el-option
            v-for="opt in (Object.entries(OUTSOURCE_QUOTE_STATUS_LABEL) as [OutsourceQuoteStatus, string][])"
            :key="opt[0]"
            :label="opt[1]"
            :value="opt[0]"
          />
        </el-select>
        <el-select
          v-model="query.customer_id"
          clearable
          placeholder="客户（L1 客户展平子节点）"
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
        <el-button
          v-if="canCreate(roleMap)"
          type="success"
          @click="showCreate = true"
        >
          新建报价
        </el-button>
      </div>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="quotes" stripe border>
        <el-table-column prop="part_serial_no" label="序列号" width="100" />
        <el-table-column prop="part_drawing_no" label="图号" width="120" />
        <el-table-column prop="part_name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="outsource_company_name" label="外协公司" width="160" show-overflow-tooltip />
        <el-table-column prop="process_code" label="工序" width="100" />
        <el-table-column label="单价(元)" width="100" align="right">
          <template #default="{ row }">{{ row.price }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag
              :type="(OUTSOURCE_QUOTE_STATUS_TAG[(row as unknown as OutsourceQuote).status] || 'info') as 'info' | 'success' | 'warning' | 'danger'"
              size="small"
              effect="plain"
            >
              {{ OUTSOURCE_QUOTE_STATUS_LABEL[(row as unknown as OutsourceQuote).status] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="customer_path" label="客户" width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="320" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="canEdit((row as unknown as OutsourceQuote), roleMap)"
              size="small"
              @click="onSubmit((row as unknown as OutsourceQuote))"
            >提交审核</el-button>
            <el-button
              v-if="canSubmit((row as unknown as OutsourceQuote), roleMap)"
              size="small"
              @click="onSubmit((row as unknown as OutsourceQuote))"
            >提交</el-button>
            <el-button
              v-if="canApprove((row as unknown as OutsourceQuote), roleMap)"
              size="small"
              type="success"
              @click="openApprove((row as unknown as OutsourceQuote))"
            >通过</el-button>
            <el-button
              v-if="canReject((row as unknown as OutsourceQuote), roleMap)"
              size="small"
              type="danger"
              @click="openReject((row as unknown as OutsourceQuote))"
            >拒绝</el-button>
            <el-button
              v-if="canSoftDelete((row as unknown as OutsourceQuote), roleMap)"
              size="small"
              type="danger"
              @click="onDelete((row as unknown as OutsourceQuote))"
            >删除</el-button>
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

    <!-- 新建 -->
    <el-dialog v-model="showCreate" title="新建外协报价（DRAFT）" width="640">
      <el-form label-width="100px">
        <el-form-item label="零件">
          <el-select v-model="createForm.part_id" filterable style="width:100%">
            <el-option
              v-for="p in parts"
              :key="p.id"
              :label="`${p.serial_no ?? ''} | ${p.drawing_no ?? ''} | ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="外协公司">
          <el-select v-model="createForm.outsource_company_id" filterable style="width:100%">
            <el-option
              v-for="c in companies"
              :key="c.id"
              :label="c.name"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="工序(OUTSOURCE)">
          <el-select v-model="createForm.process_id" filterable style="width:100%">
            <el-option
              v-for="p in processes"
              :key="p.id"
              :label="`${p.code} ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="单价(元)">
          <el-input v-model="createForm.price" type="number" :precision="2" :step="0.01" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.note" type="textarea" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="onCreate">保存为 DRAFT</el-button>
      </template>
    </el-dialog>

    <!-- 通过 -->
    <el-dialog v-model="showApprove" title="审批通过" width="480">
      <el-form label-width="100px">
        <el-form-item label="审批意见">
          <el-input v-model="reviewNote" type="textarea" placeholder="可留空" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showApprove = false">取消</el-button>
        <el-button type="success" @click="onApprove">通过</el-button>
      </template>
    </el-dialog>

    <!-- 拒绝 -->
    <el-dialog v-model="showReject" title="审批拒绝（必填原因）" width="480">
      <el-form label-width="100px">
        <el-form-item label="拒绝原因" required>
          <el-input v-model="reviewNote" type="textarea" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showReject = false">取消</el-button>
        <el-button type="danger" @click="onReject">拒绝</el-button>
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
