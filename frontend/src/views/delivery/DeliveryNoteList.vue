<!--
  送货单一览（PR-G 2026-07-22 新增；替代老 DeliveryNoteNew.vue 的 XLSX 导出路径）

  - 文员 / MANAGER：filter (statuses × customer_id × keyword) → table → 操作
    (查看 / 提交 / 撤回 / 软删)
  - 顶部 + 新建草稿 按钮：弹 el-dialog 选 customer_id + 备注，创建后跳详情

  形态对齐 frontend/src/views/outsource/OutsourceQuoteList.vue
  但本页面更简单：单一 status tag、固定列、无 submit/approve 等复杂流转。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Van } from '@element-plus/icons-vue'

import {
  createNote as createNoteApi,
  listNotes,
  recallNote,
  softDeleteNote,
  submitNote,
} from '@/api/deliveryNote'
import {
  DELIVERY_NOTE_STATUS_LABEL,
  DELIVERY_NOTE_STATUS_TAG,
  type DeliveryNoteOut,
  type DeliveryNoteStatus,
} from '@/types/deliveryNote'
import {
  canRecall,
  canSoftDelete,
  canSubmit,
  defaultStatusesForRole,
} from '@/utils/deliveryNotePermissions'
import { listCustomers } from '@/api/customer'
import { useAuthSession } from '@/composables/useAuthSession'

const router = useRouter()
const { hasRole } = useAuthSession()
const role = computed(() => ({
  MANAGER: hasRole('MANAGER'),
  CLERK: hasRole('CLERK'),
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

const customers = ref<{ id: string; name: string; path: string }[]>([])
async function loadCustomers() {
  try {
    const list = await listCustomers()
    customers.value = list.map((c: any) => ({
      id: c.id,
      name: c.name,
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
  await fetchList()
})

// ============================================================
// 新建草稿对话框
// ============================================================
const createDialogOpen = ref(false)
const createCustomerId = ref<string>('')
const createNoteText = ref<string>('')
const creating = ref(false)

function openCreate() {
  createCustomerId.value = ''
  createNoteText.value = ''
  createDialogOpen.value = true
}
async function submitCreate() {
  if (!createCustomerId.value) {
    ElMessage.warning('请选择客户')
    return
  }
  creating.value = true
  try {
    const note = await createNoteApi({
      customer_id: createCustomerId.value,
      note: createNoteText.value.trim() || null,
    })
    ElMessage.success(`已创建草稿 ${note.delivery_note_no}`)
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
// ============================================================
async function onSubmit(n: DeliveryNoteOut) {
  try {
    await ElMessageBox.confirm(
      `确认提交 ${n.delivery_note_no}？提交后可被文员撤回，也可被司机领取。`,
      '提交送货单',
      { type: 'warning', confirmButtonText: '确认提交', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await submitNote(n.id, { version: n.version })
    ElMessage.success('已提交')
    fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '提交失败')
  }
}

async function onRecall(n: DeliveryNoteOut) {
  try {
    await ElMessageBox.confirm(
      `确认撤回 ${n.delivery_note_no}？撤回后回到草稿，可继续添加/移除零件。`,
      '撤回送货单',
      { type: 'warning', confirmButtonText: '确认撤回', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await recallNote(n.id, { version: n.version })
    ElMessage.success('已撤回')
    fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '撤回失败')
  }
}

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
        <el-form-item label="状态">
          <el-select
            v-model="statuses"
            multiple
            clearable
            placeholder="全部"
            style="width: 280px"
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
        <el-form-item label="单号">
          <el-input v-model="keyword" placeholder="DN-20260723-…" clearable style="width: 200px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="page = 1; fetchList()">查询</el-button>
          <el-button @click="resetFilter">重置</el-button>
          <el-button v-if="role.MANAGER || role.CLERK" type="success" @click="openCreate">
            <el-icon><Van /></el-icon>
            新建草稿
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-table
      v-loading="loading"
      :data="items"
      stripe
      border
      style="margin-top: 16px"
      :empty-text="loading ? '加载中' : '无数据'"
    >
      <el-table-column prop="delivery_note_no" label="单号" width="180" />
      <el-table-column label="客户" min-width="200">
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).customer_path
            ?? (scope.row as DeliveryNoteOut).customer_name ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
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
      <el-table-column prop="part_count" label="零件数" width="90" align="center" />
      <el-table-column label="提交时间" width="170">
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).submitted_at
            ? new Date((scope.row as DeliveryNoteOut).submitted_at!).toLocaleString() : '—' }}
        </template>
      </el-table-column>
      <el-table-column label="领取时间" width="170">
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).picked_up_at
            ? new Date((scope.row as DeliveryNoteOut).picked_up_at!).toLocaleString() : '—' }}
        </template>
      </el-table-column>
      <el-table-column prop="driver_worker_name" label="司机" width="120">
        <template #default="scope">
          {{ (scope.row as DeliveryNoteOut).driver_worker_name ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="scope">
          <el-button link type="primary" @click="$router.push(`/delivery-notes/${(scope.row as DeliveryNoteOut).id}`)">
            详情
          </el-button>
          <el-button
            v-if="canSubmit((scope.row as DeliveryNoteOut).status, role)"
            link
            type="primary"
            @click="onSubmit(scope.row as DeliveryNoteOut)"
          >
            提交
          </el-button>
          <el-button
            v-if="canRecall((scope.row as DeliveryNoteOut).status, role)"
            link
            type="warning"
            @click="onRecall(scope.row as DeliveryNoteOut)"
          >
            撤回
          </el-button>
          <el-button
            v-if="canSoftDelete((scope.row as DeliveryNoteOut).status, role)"
            link
            type="danger"
            @click="onSoftDelete(scope.row as DeliveryNoteOut)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

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

    <!-- 新建草稿对话框 -->
    <el-dialog v-model="createDialogOpen" title="新建送货单草稿" width="500px">
      <el-form label-width="80px">
        <el-form-item label="客户" required>
          <el-select
            v-model="createCustomerId"
            filterable
            placeholder="选择二级叶子客户"
            style="width: 100%"
          >
            <el-option
              v-for="c in customers"
              :key="c.id"
              :label="c.path"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="createNoteText"
            type="textarea"
            :rows="3"
            maxlength="500"
            show-word-limit
            placeholder="可选备注"
          />
        </el-form-item>
      </el-form>
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
</style>
