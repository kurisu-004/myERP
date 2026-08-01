<!--
  ApplicantList.vue

  /applicants — 客户管理 → 申请人一览。

  表格：# / 姓名 / 所属一级客户 / 创建时间 / 操作。
  筛选：按所属一级客户 + 姓名模糊。
  新增/编辑 dialog：姓名 + 一级客户下拉（仅 root）。
  模板沿用 WorkTypeList.vue 的简洁风格。
-->
<template>
  <div class="applicant-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline>
        <el-form-item label="一级客户">
          <el-select
            v-model="search.customerId"
            placeholder="全部"
            clearable
            filterable
            style="width: 200px"
          >
            <el-option
              v-for="r in rootOptions"
              :key="r.id"
              :label="r.name"
              :value="r.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="姓名">
          <el-input
            v-model="search.nameLike"
            placeholder="模糊匹配"
            clearable
            style="width: 200px"
            @keyup.enter="fetchList"
            @clear="fetchList"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="fetchList">
            <el-icon><Search /></el-icon><span>查询</span>
          </el-button>
          <el-button @click="onReset">
            <el-icon><RefreshLeft /></el-icon><span>重置</span>
          </el-button>
          <el-button type="success" @click="onNew">
            <el-icon><Plus /></el-icon><span>新增申请人</span>
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <ResponsiveList
        :items="rows"
        :loading="loading"
        row-key="id"
        empty-text="暂无申请人"
        stripe
        border
        size="small"
      >
        <template #toolbar>
          <ColumnVisibilityPopover
            :defs="columnDefs"
            :model-value="columnVisibility.currentMap" @update:model-value="columnVisibility.update"
            @reset="columnVisibility.showAll"
          />
        </template>

        <el-table-column type="index" label="#" width="50" />
        <el-table-column
          v-if="columnVisibility.isVisible('name')"
          prop="name" label="姓名" min-width="160" align="center"
        />
        <el-table-column
          v-if="columnVisibility.isVisible('customer_name')"
          label="所属一级客户" min-width="180" align="center"
        >
          <template #default="{ row }">
            {{ row.customer_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column
          v-if="columnVisibility.isVisible('created_at')"
          label="创建时间" min-width="180" align="center"
        >
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="180" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onEdit(row as Applicant)">编辑</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row as Applicant)">删除</el-button>
          </template>
        </el-table-column>

        <!-- 手机卡片 -->
        <template #card="{ row, index }">
          <div class="rl-card-head">
            <span class="rl-card-title">#{{ index + 1 }} · {{ row.name }}</span>
          </div>
          <div class="rl-card-sub">{{ row.customer_name || '—' }}</div>
          <div class="rl-kv">
            <div class="rl-kv__item rl-kv__item--full">
              <span class="rl-kv__key">创建时间</span>
              <span class="rl-kv__val">{{ formatDate(row.created_at) || '—' }}</span>
            </div>
          </div>
          <div class="rl-card-actions">
            <el-button link type="primary" size="small" @click="onEdit(row as Applicant)">编辑</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row as Applicant)">删除</el-button>
          </div>
        </template>
      </ResponsiveList>
    </el-card>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑申请人' : '新增申请人'"
      :width="applicantDlg.width.value"
      :top="applicantDlg.top.value"
      :fullscreen="applicantDlg.fullscreen.value"
      :close-on-click-modal="false"
      @closed="onDialogClosed"
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="100px"
        label-position="right"
      >
        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" placeholder="例如：林雪强" maxlength="50" show-word-limit />
        </el-form-item>
        <el-form-item label="一级客户" prop="customerId">
          <el-select
            v-model="form.customerId"
            placeholder="选择一级客户"
            filterable
            style="width: 100%"
          >
            <el-option
              v-for="r in rootOptions"
              :key="r.id"
              :label="r.name"
              :value="r.id"
            />
          </el-select>
          <p class="form-hint">申请人只能挂在一级客户下。</p>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { Plus, RefreshLeft, Search } from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useDialogSize } from '@/composables/useDialogSize'
import { useListStatePersist } from '@/composables/useListFilterPersist'
import { listCustomers, type Customer } from '@/api/customer'
import {
  createApplicant,
  listApplicants,
  softDeleteApplicant,
  updateApplicant,
} from '@/api/applicant'
import type { Applicant } from '@/types/applicant'

const { isMobile } = useBreakpoint()

// ============ 列可见性 ============
// 「#」和「操作」列不放进 defs → 始终可见,且不出现在列设置弹窗
const columnDefs = [
  { key: 'name', label: '姓名' },
  { key: 'customer_name', label: '所属一级客户' },
  { key: 'created_at', label: '创建时间' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'applicant_list' })

const loading = ref(false)
const saving = ref(false)
const rows = ref<Applicant[]>([])
const customers = ref<Customer[]>([])
const search = reactive({ customerId: '' as string | '', nameLike: '' })

// ============ 筛选状态持久化 ============
const { restore: restoreApplicantFilter, clear: clearApplicantFilter } = useListStatePersist(
  'applicant_list',
  { search },
)

const rootOptions = computed(() =>
  customers.value
    .filter((c) => c.parent_id === null)
    .map((c) => ({ id: c.id, name: c.name })),
)

function formatDate(s: string): string {
  if (!s) return ''
  return s.replace('T', ' ').slice(0, 19)
}

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    const res = await listApplicants({
      customer_id: search.customerId || undefined,
      name_like: search.nameLike || undefined,
      limit: 200,
    })
    rows.value = res.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadCustomers(): Promise<void> {
  try {
    customers.value = await listCustomers()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '客户列表加载失败')
  }
}

function onReset(): void {
  search.customerId = ''
  search.nameLike = ''
  fetchList()
}

// ===== Dialog =====
interface FormState {
  name: string
  customerId: string
}
const formRef = ref<FormInstance>()
const applicantDlg = useDialogSize({ desktopWidth: 480 })
const dialogVisible = ref(false)
const editing = ref<Applicant | null>(null)
const form = reactive<FormState>({ name: '', customerId: '' })

const rules: FormRules = {
  name: [{ required: true, message: '请输入申请人姓名', trigger: 'blur' }],
  customerId: [{ required: true, message: '请选择一级客户', trigger: 'change' }],
}

function onNew(): void {
  editing.value = null
  form.name = ''
  form.customerId = rootOptions.value[0]?.id ?? ''
  dialogVisible.value = true
}

function onEdit(row: Applicant): void {
  editing.value = row
  form.name = row.name
  form.customerId = row.customer_id
  dialogVisible.value = true
}

async function onSave(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      customer_id: form.customerId,
    }
    if (editing.value) {
      await updateApplicant(editing.value.id, payload)
      ElMessage.success('已保存')
    } else {
      await createApplicant(payload)
      ElMessage.success('已新增')
    }
    dialogVisible.value = false
    await fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '保存失败')
  } finally {
    saving.value = false
  }
}

function onDialogClosed(): void {
  editing.value = null
  form.name = ''
  form.customerId = ''
}

async function onDelete(row: Applicant): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认软删除申请人「${row.name}」？被未软删零件引用时拒绝。`,
      '删除申请人',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await softDeleteApplicant(row.id)
    ElMessage.success('已删除')
    await fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

onMounted(async () => {
  // 先尝试恢复 localStorage 中的搜索条件
  const persisted = restoreApplicantFilter()
  if (persisted) {
    Object.assign(search, persisted.search)
  }
  await loadCustomers()
  await fetchList()
})
</script>

<style lang="scss" scoped>
.applicant-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.filter-card :deep(.el-card__body) {
  padding: 16px 20px;
}
.form-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>