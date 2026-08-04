<template>
  <div class="wt-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline>
        <el-form-item label="代码">
          <el-input v-model="search.code_like" clearable style="width: 180px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="fetchList"><el-icon><Search /></el-icon><span>查询</span></el-button>
          <el-button @click="onReset"><el-icon><RefreshLeft /></el-icon><span>重置</span></el-button>
          <el-button type="success" @click="onNew"><el-icon><Plus /></el-icon><span>新增工种</span></el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <ResponsiveList
      :items="rows"
      :loading="loading"
      row-key="id"
      empty-text="暂无工种"
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
        prop="code" label="代码" min-width="140" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('name')"
        prop="name" label="名称" min-width="160" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('description')"
        prop="description" label="描述" min-width="200" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('sort_order')"
        prop="sort_order" label="排序" min-width="80" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('max_held_batches')"
        prop="max_held_batches" label="可领取上限" min-width="100" align="center"
      />
      <el-table-column label="操作" min-width="180" fixed="right" align="center">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="onEdit(row as WorkType)">编辑</el-button>
          <el-button link type="danger" size="small" @click="onDelete(row as WorkType)">删除</el-button>
        </template>
      </el-table-column>

      <template #card="{ row }">
        <div class="rl-card-head">
          <span class="rl-card-title">{{ row.name }}</span>
        </div>
        <div class="rl-card-sub">代码 {{ row.code }}</div>
        <div class="rl-kv">
          <div class="rl-kv__item">
            <span class="rl-kv__key">排序</span>
            <span class="rl-kv__val">{{ row.sort_order }}</span>
          </div>
          <div class="rl-kv__item" v-if="columnVisibility.isVisible('max_held_batches')">
            <span class="rl-kv__key">可领取上限</span>
            <span class="rl-kv__val">{{ row.max_held_batches ?? '不限' }}</span>
          </div>
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">描述</span>
            <span class="rl-kv__val">{{ row.description || '—' }}</span>
          </div>
        </div>
        <div class="rl-card-actions">
          <el-button link type="primary" size="small" @click="onEdit(row as WorkType)">编辑</el-button>
          <el-button link type="danger" size="small" @click="onDelete(row as WorkType)">删除</el-button>
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
          <el-input v-model="form.code" :disabled="!!editing" placeholder="如 车床 / CNC操机" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="前端显示名" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" />
        </el-form-item>
        <el-form-item label="可领取上限">
          <el-input-number
            v-model="form.max_held_batches"
            :min="1"
            :value-on-clear="null"
            placeholder="留空不限"
          />
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
import { useListStatePersist } from '@/composables/useListFilterPersist'
import {
  createWorkType,
  listWorkTypes,
  softDeleteWorkType,
  updateWorkType,
} from '@/api/workType'
import type { WorkType } from '@/types/workType'

const { isMobile } = useBreakpoint()
const dialogSize = useDialogSize({ desktopWidth: 420 })

const loading = ref(false)
const saving = ref(false)
const rows = ref<WorkType[]>([])

const search = reactive({ code_like: '' })

// ============ 筛选状态持久化 ============
const { restore: restoreWorkTypeFilter, clear: clearWorkTypeFilter } = useListStatePersist(
  'work_type_list',
  { search },
)

// ============ 列可见性 ============
// 「#」和「操作」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'code', label: '代码' },
  { key: 'name', label: '名称' },
  { key: 'description', label: '描述' },
  { key: 'sort_order', label: '排序' },
  { key: 'max_held_batches', label: '可领取上限' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'work_type_list' })

const dialogVisible = ref(false)
const editing = ref<WorkType | null>(null)
const dialogTitle = computed(() => (editing.value ? '编辑工种' : '新增工种'))
const form = reactive<{
  code: string
  name: string
  description: string
  sort_order: number
  max_held_batches: number | null
}>({
  code: '', name: '', description: '', sort_order: 0, max_held_batches: null,
})

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    const res = await listWorkTypes({ code_like: search.code_like || undefined, limit: 200 })
    rows.value = res.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  } finally {
    loading.value = false
  }
}

function onReset(): void { search.code_like = ''; fetchList() }
function onNew(): void {
  editing.value = null
  form.code = ''; form.name = ''; form.description = ''; form.sort_order = 0
  form.max_held_batches = null
  dialogVisible.value = true
}
function onEdit(row: WorkType): void {
  editing.value = row
  form.code = row.code; form.name = row.name
  form.description = row.description ?? ''; form.sort_order = row.sort_order
  form.max_held_batches = row.max_held_batches ?? null
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
      await updateWorkType(editing.value.id, {
        name: form.name.trim(),
        description: form.description.trim() || null,
        sort_order: form.sort_order,
        max_held_batches: form.max_held_batches,
      })
      ElMessage.success('已保存')
    } else {
      await createWorkType({
        code: form.code.trim(),
        name: form.name.trim(),
        description: form.description.trim() || null,
        sort_order: form.sort_order,
        max_held_batches: form.max_held_batches,
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
  form.code = ''; form.name = ''; form.description = ''; form.sort_order = 0
  form.max_held_batches = null
}

async function onDelete(row: WorkType): Promise<void> {
  await ElMessageBox.confirm(
    `确认删除工种「${row.name}」?被引用时拒绝。`,
    '提示',
    { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
  ).then(async () => {
    try {
      await softDeleteWorkType(row.id)
      ElMessage.success('已删除')
      fetchList()
    } catch (e) {
      ElMessage.error((e as Error).message ?? '删除失败')
    }
  }).catch(() => undefined)
}

onMounted(() => {
  // 先尝试恢复 localStorage 中的搜索条件
  const persisted = restoreWorkTypeFilter()
  if (persisted) {
    Object.assign(search, persisted.search)
  }
  void fetchList()
})
</script>

<style lang="scss" scoped>
.wt-list { display: flex; flex-direction: column; gap: 12px; }

@include until(sm) {
  .filter-card :deep(.el-form--inline) {
    display: grid;
    grid-template-columns: 1fr;
  }

  .filter-card :deep(.el-form-item) {
    margin-right: 0;
  }

  .filter-card :deep(.el-form-item:first-child),
  .filter-card :deep(.el-form-item:first-child .el-input) {
    width: 100% !important;
  }
}
</style>