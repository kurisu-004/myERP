<template>
  <div class="outsource-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline>
        <el-form-item label="公司名">
          <el-input v-model="search.name_like" clearable style="width: 200px" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="search.is_active" clearable style="width: 120px">
            <el-option label="启用" :value="true" />
            <el-option label="停用" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="fetchList">
            <el-icon><Search /></el-icon><span>查询</span>
          </el-button>
          <el-button @click="onReset">
            <el-icon><RefreshLeft /></el-icon><span>重置</span>
          </el-button>
          <el-button type="success" @click="onNew">
            <el-icon><Plus /></el-icon><span>新增外协公司</span>
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <ResponsiveList
        :items="rows"
        :loading="loading"
        row-key="id"
        empty-text="暂无外协公司"
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
          prop="name" label="公司名" min-width="160" align="center"
        />
        <el-table-column
          v-if="columnVisibility.isVisible('contact_name')"
          prop="contact_name" label="联系人" min-width="100" align="center"
        >
          <template #default="{ row }">
            {{ (row as OutsourceCompany).contact_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column
          v-if="columnVisibility.isVisible('contact_phone')"
          prop="contact_phone" label="联系电话" min-width="120" align="center"
        >
          <template #default="{ row }">
            {{ (row as OutsourceCompany).contact_phone || '—' }}
          </template>
        </el-table-column>
        <el-table-column
          v-if="columnVisibility.isVisible('address')"
          prop="address" label="地址" min-width="200" show-overflow-tooltip align="center"
        >
          <template #default="{ row }">
            {{ (row as OutsourceCompany).address || '—' }}
          </template>
        </el-table-column>
        <el-table-column
          v-if="columnVisibility.isVisible('is_active')"
          label="状态" min-width="80" align="center"
        >
          <template #default="{ row }">
            <el-tag :type="(row as OutsourceCompany).is_active ? 'success' : 'info'" size="small">
              {{ (row as OutsourceCompany).is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="280" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onEdit(row as OutsourceCompany)">编辑</el-button>
            <el-button link type="warning" size="small" @click="onManageProcesses(row as OutsourceCompany)">维护工序</el-button>
            <el-button link type="success" size="small" @click="onBilling(row as OutsourceCompany)">对账</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row as OutsourceCompany)">删除</el-button>
          </template>
        </el-table-column>

        <template #card="{ row }">
          <div class="rl-card-head">
            <span class="rl-card-title">{{ (row as OutsourceCompany).name }}</span>
            <el-tag :type="(row as OutsourceCompany).is_active ? 'success' : 'info'" size="small">
              {{ (row as OutsourceCompany).is_active ? '启用' : '停用' }}
            </el-tag>
          </div>
          <div class="rl-card-sub">
            {{ (row as OutsourceCompany).contact_name || '暂无联系人' }}
          </div>
          <div class="rl-kv">
            <div class="rl-kv__item">
              <span class="rl-kv__key">联系电话</span>
              <span class="rl-kv__val">{{ (row as OutsourceCompany).contact_phone || '—' }}</span>
            </div>
            <div class="rl-kv__item rl-kv__item--full">
              <span class="rl-kv__key">地址</span>
              <span class="rl-kv__val">{{ (row as OutsourceCompany).address || '—' }}</span>
            </div>
          </div>
          <div class="rl-card-actions">
            <el-button link type="primary" size="small" @click="onEdit(row as OutsourceCompany)">编辑</el-button>
            <el-button link type="warning" size="small" @click="onManageProcesses(row as OutsourceCompany)">维护工序</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row as OutsourceCompany)">删除</el-button>
          </div>
        </template>
      </ResponsiveList>
      <div class="pagination">
        <el-pagination
          v-model:current-page="search.offset"
          v-model:page-size="search.limit"
          :total="total"
          :page-sizes="[50, 100, 200]"
          :layout="paginationLayout"
          :pager-count="isMobile ? 5 : 7"
          @size-change="fetchList"
          @current-change="fetchList"
        />
      </div>
    </el-card>

    <!-- CRUD 对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑外协公司' : '新增外协公司'"
      :width="companyDlg.width.value"
      :top="companyDlg.top.value"
      :close-on-click-modal="false"
      @closed="onDialogClosed"
    >
      <el-form :model="form" label-width="100px">
        <el-form-item label="公司名" required>
          <el-input v-model="form.name" :disabled="!!editing" placeholder="如 福州精工外协" />
        </el-form-item>
        <el-form-item label="联系人">
          <el-input v-model="form.contact_name" />
        </el-form-item>
        <el-form-item label="联系电话">
          <el-input v-model="form.contact_phone" />
        </el-form-item>
        <el-form-item label="地址">
          <el-input v-model="form.address" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_active" />
        </el-form-item>
        <el-form-item v-if="!editing" label="工序能力（创建时）">
          <el-checkbox-group v-model="form.process_ids" class="process-check-group">
            <el-checkbox
              v-for="p in outsourceProcesses"
              :key="p.id"
              :value="p.id"
            >
              {{ p.code }} — {{ p.name }}
            </el-checkbox>
            <span v-if="outsourceProcesses.length === 0" class="muted">
              没有 OUTSOURCE 工序，请先在「设置 → 工序管理」中新增
            </span>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 维护工序能力对话框 -->
    <el-dialog
      v-model="manageDialogVisible"
      :title="managing ? `维护「${managing.name}」的工序能力` : ''"
      :width="companyDlg.width.value"
      :top="companyDlg.top.value"
      :close-on-click-modal="false"
      @closed="onManageDialogClosed"
    >
      <el-form label-width="80px">
        <el-form-item label="可执行外协工序">
          <el-checkbox-group v-model="manageForm.process_ids" class="process-check-group">
            <el-checkbox
              v-for="p in outsourceProcesses"
              :key="p.id"
              :value="p.id"
            >
              {{ p.code }} — {{ p.name }}
            </el-checkbox>
            <span v-if="outsourceProcesses.length === 0" class="muted">
              没有 OUTSOURCE 工序，请先在「设置 → 工序管理」中新增
            </span>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manageDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSaveProcesses">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, RefreshLeft, Plus } from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useDialogSize } from '@/composables/useDialogSize'
import { useListStatePersist } from '@/composables/useListFilterPersist'
import {
  createOutsourceCompany,
  getOutsourceCompany,
  listOutsourceCompanies,
  setOutsourceCompanyProcesses,
  softDeleteOutsourceCompany,
  updateOutsourceCompany,
} from '@/api/outsource'
import type { OutsourceCompany } from '@/types/outsource'
import { listProcesses } from '@/api/process'
import type { Process } from '@/types/process'

const router = useRouter()
const { isMobile } = useBreakpoint()
const companyDlg = useDialogSize({ desktopWidth: 520 })
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

const loading = ref(false)
const saving = ref(false)
const rows = ref<OutsourceCompany[]>([])
const total = ref(0)
const search = reactive<{ name_like: string; is_active: boolean | undefined; limit: number; offset: number }>({
  name_like: '',
  is_active: undefined,
  limit: 100,
  offset: 1,
})

// ============ 筛选状态持久化（2026-07-30 commit 4B）============
// 持久化整个 search（含 limit/offset），restore 时强制把 offset 置 1（避免拉到不存在数据的页）
const { restore: restoreOutsourceCompanyFilter } = useListStatePersist(
  'outsource_company_list',
  { search },
)

// ============ 列可见性 ============
// 「#」和「操作」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'name', label: '公司名' },
  { key: 'contact_name', label: '联系人' },
  { key: 'contact_phone', label: '联系电话' },
  { key: 'address', label: '地址' },
  { key: 'is_active', label: '状态' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'outsource_company_list' })

const outsourceProcesses = ref<Process[]>([])

// CRUD dialog state
const dialogVisible = ref(false)
const editing = ref<OutsourceCompany | null>(null)
const form = reactive<{
  name: string
  contact_name: string
  contact_phone: string
  address: string
  is_active: boolean
  process_ids: string[]
}>({
  name: '',
  contact_name: '',
  contact_phone: '',
  address: '',
  is_active: true,
  process_ids: [],
})

// 维护工序 dialog state
const manageDialogVisible = ref(false)
const managing = ref<OutsourceCompany | null>(null)
const manageForm = reactive<{ process_ids: string[] }>({ process_ids: [] })

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    const res = await listOutsourceCompanies({
      name_like: search.name_like || undefined,
      is_active: search.is_active,
      limit: search.limit,
      offset: (search.offset - 1) * search.limit,
    })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  } finally {
    loading.value = false
  }
}

async function fetchOutsourceProcesses(): Promise<void> {
  try {
    const res = await listProcesses({ category: 'OUTSOURCE', limit: 200 })
    outsourceProcesses.value = res.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载外协工序失败')
  }
}

function onReset(): void {
  search.name_like = ''
  search.is_active = undefined
  search.offset = 1
  fetchList()
}

function onNew(): void {
  editing.value = null
  form.name = ''
  form.contact_name = ''
  form.contact_phone = ''
  form.address = ''
  form.is_active = true
  form.process_ids = []
  dialogVisible.value = true
}

function onEdit(row: OutsourceCompany): void {
  editing.value = row
  form.name = row.name
  form.contact_name = row.contact_name ?? ''
  form.contact_phone = row.contact_phone ?? ''
  form.address = row.address ?? ''
  form.is_active = row.is_active
  form.process_ids = []
  dialogVisible.value = true
}

async function onSave(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('公司名不能为空')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateOutsourceCompany(editing.value.id, {
        name: form.name.trim(),
        contact_name: form.contact_name.trim() || null,
        contact_phone: form.contact_phone.trim() || null,
        address: form.address.trim() || null,
        is_active: form.is_active,
      })
      ElMessage.success('已保存')
    } else {
      await createOutsourceCompany({
        name: form.name.trim(),
        contact_name: form.contact_name.trim() || null,
        contact_phone: form.contact_phone.trim() || null,
        address: form.address.trim() || null,
        is_active: form.is_active,
        process_ids: form.process_ids,
      })
      ElMessage.success('已新增')
    }
    dialogVisible.value = false
    fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '保存失败')
  } finally {
    saving.value = false
  }
}

function onDialogClosed(): void {
  editing.value = null
  form.name = ''
  form.contact_name = ''
  form.contact_phone = ''
  form.address = ''
  form.is_active = true
  form.process_ids = []
}

async function onManageProcesses(row: OutsourceCompany): Promise<void> {
  managing.value = row
  manageForm.process_ids = []
  manageDialogVisible.value = true
  try {
    const detail = await getOutsourceCompany(row.id)
    manageForm.process_ids = detail.processes.map((p) => p.process_id)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载公司详情失败')
  }
}

async function onSaveProcesses(): Promise<void> {
  if (!managing.value) return
  saving.value = true
  try {
    await setOutsourceCompanyProcesses(managing.value.id, {
      process_ids: manageForm.process_ids,
    })
    ElMessage.success('工序能力已更新')
    manageDialogVisible.value = false
    fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '保存失败')
  } finally {
    saving.value = false
  }
}

function onManageDialogClosed(): void {
  managing.value = null
  manageForm.process_ids = []
}

function onBilling(row: OutsourceCompany): void {
  // 跳到外协对账页（2026-07-28 新增）
  void router.push(`/outsource/companies/${row.id}/sent-parts`)
}

async function onDelete(row: OutsourceCompany): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除外协公司「${row.name}」？若有工序映射会拒绝。`,
      '提示',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await softDeleteOutsourceCompany(row.id)
    ElMessage.success('已删除')
    fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

onMounted(() => {
  void fetchOutsourceProcesses()
  // 从 localStorage 恢复搜索 + 分页大小；强制将当前页重置到第 1 页（避免恢复到无数据页）
  const persisted = restoreOutsourceCompanyFilter()
  if (persisted && persisted.search) {
    Object.assign(search, persisted.search as Partial<typeof search>)
    search.offset = 1
  }
  void fetchList()
})
</script>

<style lang="scss" scoped>
.outsource-list { display: flex; flex-direction: column; gap: 12px; }
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;

  @include until(sm) {
    justify-content: center;
  }
}
.process-check-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 200px;
  overflow-y: auto;
}
.muted { color: #909399; font-size: 12px; }
</style>