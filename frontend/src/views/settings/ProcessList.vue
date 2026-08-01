<template>
  <div class="process-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline>
        <el-form-item label="代码">
          <el-input v-model="search.code_like" clearable style="width: 160px" />
        </el-form-item>
        <el-form-item label="类别">
          <el-select v-model="search.category" clearable style="width: 120px">
            <el-option label="自产" value="INHOUSE" />
            <el-option label="外协" value="OUTSOURCE" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="fetchList"><el-icon><Search /></el-icon><span>查询</span></el-button>
          <el-button @click="onReset"><el-icon><RefreshLeft /></el-icon><span>重置</span></el-button>
          <el-button v-if="isManager" type="success" @click="onNew"><el-icon><Plus /></el-icon><span>新增工序</span></el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <ResponsiveList
      :items="rows"
      :loading="loading"
      row-key="id"
      empty-text="暂无工序"
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
        v-if="columnVisibility.isVisible('code')"
        prop="code" label="代码" min-width="120" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('name')"
        prop="name" label="名称" min-width="160" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('category')"
        label="类别" min-width="100" align="center"
      >
        <template #default="{ row }">
          <el-tag :type="(row as Process).category === 'INHOUSE' ? 'primary' : 'warning'" size="small">
            {{ PROCESS_CATEGORY_LABEL[(row as Process).category] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('requires_approval')"
        label="审批模式" min-width="110" align="center"
      >
        <template #default="{ row }">
          <el-tag :type="(row as Process).requires_approval ? 'warning' : 'success'" size="small">
            {{ (row as Process).requires_approval ? '需要审批' : '直接发送' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('sort_order')"
        prop="sort_order" label="排序" min-width="80" align="center"
      />
      <el-table-column v-if="isManager" label="操作" min-width="180" fixed="right" align="center">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="onEdit(row as Process)">编辑</el-button>
          <el-button link type="danger" size="small" @click="onDelete(row as Process)">删除</el-button>
        </template>
      </el-table-column>

      <template #card="{ row }">
        <div class="rl-card-head">
          <span class="rl-card-title">{{ row.name }}</span>
          <el-tag :type="row.category === 'INHOUSE' ? 'primary' : 'warning'" size="small">
            {{ PROCESS_CATEGORY_LABEL[(row as Process).category] }}
          </el-tag>
          <el-tag :type="(row as Process).requires_approval ? 'warning' : 'success'" size="small">
            {{ (row as Process).requires_approval ? '需要审批' : '直接发送' }}
          </el-tag>
        </div>
        <div class="rl-card-sub">代码 {{ row.code }}</div>
        <div class="rl-kv">
          <div class="rl-kv__item">
            <span class="rl-kv__key">排序</span>
            <span class="rl-kv__val">{{ row.sort_order }}</span>
          </div>
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">描述</span>
            <span class="rl-kv__val">{{ row.description || '—' }}</span>
          </div>
        </div>
        <div v-if="isManager" class="rl-card-actions">
          <el-button link type="primary" size="small" @click="onEdit(row as Process)">编辑</el-button>
          <el-button link type="danger" size="small" @click="onDelete(row as Process)">删除</el-button>
        </div>
      </template>
    </ResponsiveList>

    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      :width="dialogSize.width.value"
      :top="dialogSize.top.value"
      :fullscreen="dialogSize.fullscreen.value"
      @closed="onDialogClosed"
    >
      <el-form :model="form" label-width="80px" :label-position="isMobile ? 'top' : 'right'">
        <el-form-item label="代码" required>
          <el-input v-model="form.code" :disabled="!!editing" placeholder="如 车 / 铣 / CNC / 热处理" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="类别" required>
          <el-select v-model="form.category" style="width: 100%">
            <el-option label="自产" value="INHOUSE" />
            <el-option label="外协" value="OUTSOURCE" />
          </el-select>
        </el-form-item>
        <el-form-item label="需要审批">
          <el-switch
            v-model="form.requires_approval"
            :disabled="form.category === 'INHOUSE'"
            active-text="需要审批"
            inactive-text="直接发送"
            inline-prompt
            style="--el-switch-off-color: #67c23a"
          />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, RefreshLeft, Plus } from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useDialogSize } from '@/composables/useDialogSize'
import { usePermissions } from '@/composables/usePermissions'
import { useListStatePersist } from '@/composables/useListFilterPersist'
import {
  createProcess,
  listProcesses,
  softDeleteProcess,
  updateProcess,
} from '@/api/process'
import type { Process, ProcessCategory } from '@/types/process'
import { PROCESS_CATEGORY_LABEL } from '@/types/process'

const { isMobile } = useBreakpoint()
const { isManager } = usePermissions()
const dialogSize = useDialogSize({ desktopWidth: 460 })

const loading = ref(false)
const saving = ref(false)
const rows = ref<Process[]>([])
const search = reactive<{ code_like: string; category: ProcessCategory | undefined }>({
  code_like: '',
  category: undefined,
})

// ============ 筛选状态持久化 ============
const { restore: restoreProcessFilter, clear: clearProcessFilter } = useListStatePersist(
  'process_list',
  { search },
)

// ============ 列可见性 ============
// 「#」和「操作」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'code', label: '代码' },
  { key: 'name', label: '名称' },
  { key: 'category', label: '类别' },
  { key: 'requires_approval', label: '审批模式' },
  { key: 'sort_order', label: '排序' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'process_list' })

const dialogVisible = ref(false)
const editing = ref<Process | null>(null)
const dialogTitle = computed(() => (editing.value ? '编辑工序' : '新增工序'))
const form = reactive<{
  code: string; name: string; category: ProcessCategory
  sort_order: number; description: string
  requires_approval: boolean
}>({
  code: '', name: '', category: 'INHOUSE',
  sort_order: 0, description: '',
  requires_approval: true,  // OUTSOURCE 默认；INHOUSE 在保存时由后端强制为 false
})

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    const res = await listProcesses({
      code_like: search.code_like || undefined,
      category: search.category,
      limit: 200,
    })
    rows.value = res.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  } finally {
    loading.value = false
  }
}

function onReset(): void { search.code_like = ''; search.category = undefined; fetchList() }
function onNew(): void {
  editing.value = null
  Object.assign(form, {
    code: '', name: '', category: 'INHOUSE',
    sort_order: 0, description: '',
    requires_approval: true,
  })
  dialogVisible.value = true
}
function onEdit(row: Process): void {
  editing.value = row
  Object.assign(form, {
    code: row.code, name: row.name, category: row.category,
    sort_order: row.sort_order,
    description: row.description ?? '',
    requires_approval: row.requires_approval ?? true,
  })
  dialogVisible.value = true
}

async function onSave(): Promise<void> {
  if (!form.code.trim() || !form.name.trim()) {
    ElMessage.warning('代码与名称不能为空')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateProcess(editing.value.id, {
        name: form.name.trim(),
        category: form.category,
        sort_order: form.sort_order,
        description: form.description.trim() || null,
        requires_approval: form.requires_approval,
      })
      ElMessage.success('已保存')
    } else {
      await createProcess({
        code: form.code.trim(),
        name: form.name.trim(),
        category: form.category,
        sort_order: form.sort_order,
        description: form.description.trim() || null,
        requires_approval: form.requires_approval,
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
  Object.assign(form, {
    code: '', name: '', category: 'INHOUSE',
    sort_order: 0, description: '',
    requires_approval: true,
  })
}

async function onDelete(row: Process): Promise<void> {
  await ElMessageBox.confirm(
    `确认删除工序「${row.name}」?被引用时拒绝。`,
    '提示',
    { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
  ).then(async () => {
    try {
      await softDeleteProcess(row.id)
      ElMessage.success('已删除')
      fetchList()
    } catch (e) {
      ElMessage.error((e as Error).message ?? '删除失败')
    }
  }).catch(() => undefined)
}

onMounted(() => {
  // 先尝试恢复 localStorage 中的搜索条件
  const persisted = restoreProcessFilter()
  if (persisted) {
    Object.assign(search, persisted.search)
  }
  void fetchList()
})
</script>

<style lang="scss" scoped>
.process-list { display: flex; flex-direction: column; gap: 12px; }

@include until(sm) {
  .filter-card :deep(.el-form--inline) {
    display: grid;
    grid-template-columns: 1fr;
  }

  .filter-card :deep(.el-form-item) {
    margin-right: 0;
  }

  .filter-card :deep(.el-form-item:nth-child(-n + 2)),
  .filter-card :deep(.el-form-item:nth-child(-n + 2) .el-input),
  .filter-card :deep(.el-form-item:nth-child(-n + 2) .el-select) {
    width: 100% !important;
  }
}
</style>