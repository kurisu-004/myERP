<template>
  <div class="worker-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline @submit.prevent="onSearch">
        <el-form-item label="姓名">
          <el-input
            v-model="search.name_like"
            placeholder="模糊匹配"
            clearable
            style="width: 160px"
            @keyup.enter="onSearch"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="search.is_active"
            placeholder="全部"
            clearable
            style="width: 110px"
          >
            <el-option label="在职" :value="true" />
            <el-option label="停用" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">
            <el-icon><Search /></el-icon>
            <span>查询</span>
          </el-button>
          <el-button @click="onReset">
            <el-icon><RefreshLeft /></el-icon>
            <span>重置</span>
          </el-button>
          <el-button type="success" @click="onNew">
            <el-icon><Plus /></el-icon>
            <span>新增工人</span>
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <ResponsiveList
      :items="rows"
      :loading="loading"
      row-key="id"
      empty-text="暂无工人"
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
        v-if="columnVisibility.isVisible('badge_code')"
        prop="badge_code" label="工牌码" min-width="160" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('name')"
        prop="name" label="姓名" min-width="120" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('work_type')"
        label="工种" min-width="120" align="center"
      >
        <template #default="{ row }">
          <el-tag v-if="(row as Worker).work_type_id" size="small" type="primary">
            {{ workTypeNameById[(row as Worker).work_type_id!] || '...' }}
          </el-tag>
          <span v-else style="color: #c0c4cc">未分配</span>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('is_active')"
        label="状态" min-width="100" align="center"
      >
        <template #default="{ row }">
          <el-tag :type="(row as Worker).is_active ? 'success' : 'info'" effect="light" size="small">
            {{ (row as Worker).is_active ? '在职' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('created_at')"
        prop="created_at" label="创建时间" min-width="170" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('updated_at')"
        prop="updated_at" label="更新时间" min-width="170" align="center"
      />
      <el-table-column label="操作" min-width="220" fixed="right" align="center">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="onEdit(row as Worker)">编辑</el-button>
          <el-button
            v-if="(row as Worker).is_active"
            link
            type="warning"
            size="small"
            @click="onDeactivate(row as Worker)"
          >停用</el-button>
          <el-button
            v-else
            link
            type="success"
            size="small"
            @click="onReactivate(row as Worker)"
          >启用</el-button>
        </template>
      </el-table-column>

      <!-- 手机卡片 -->
      <template #card="{ row }">
        <div class="rl-card-head">
          <span class="rl-card-title">{{ (row as Worker).name }}</span>
          <el-tag
            :type="(row as Worker).is_active ? 'success' : 'info'"
            effect="light"
            size="small"
          >
            {{ (row as Worker).is_active ? '在职' : '停用' }}
          </el-tag>
        </div>
        <div class="rl-card-sub">工牌码 {{ (row as Worker).badge_code }}</div>
        <div class="rl-kv">
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">工种</span>
            <span class="rl-kv__val">
              <el-tag
                v-if="(row as Worker).work_type_id"
                size="small"
                type="primary"
              >
                {{ workTypeNameById[(row as Worker).work_type_id!] || '...' }}
              </el-tag>
              <span v-else class="muted">未分配</span>
            </span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">创建时间</span>
            <span class="rl-kv__val">{{ (row as Worker).created_at }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">更新时间</span>
            <span class="rl-kv__val">{{ (row as Worker).updated_at }}</span>
          </div>
        </div>
        <div class="rl-card-actions">
          <el-button link type="primary" size="small" @click="onEdit(row as Worker)">编辑</el-button>
          <el-button
            v-if="(row as Worker).is_active"
            link
            type="warning"
            size="small"
            @click="onDeactivate(row as Worker)"
          >停用</el-button>
          <el-button
            v-else
            link
            type="success"
            size="small"
            @click="onReactivate(row as Worker)"
          >启用</el-button>
        </div>
      </template>
    </ResponsiveList>

    <!-- 新增/编辑弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑工人' : '新增工人'"
      :width="workerDlg.width.value"
      :top="workerDlg.top.value"
      @closed="onDialogClosed"
    >
      <el-form :model="form" label-width="80px" ref="formRef">
        <el-form-item label="工牌码" required>
          <el-input
            v-model="form.badge_code"
            placeholder="工牌上的条码/二维码值"
            clearable
          />
        </el-form-item>
        <el-form-item label="姓名" required>
          <el-input v-model="form.name" placeholder="工人姓名" clearable />
        </el-form-item>
        <el-form-item label="工种">
          <el-select v-model="form.work_type_id" clearable placeholder="未分配" style="width: 100%">
            <el-option
              v-for="wt in workTypeOptions"
              :key="wt.id"
              :label="`${wt.code} / ${wt.name}`"
              :value="wt.id"
            />
          </el-select>
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
import { useDialogSize } from '@/composables/useDialogSize'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useListStatePersist } from '@/composables/useListFilterPersist'
import {
  createWorker,
  deactivateWorker,
  listWorkers,
  reactivateWorker,
  updateWorker,
} from '@/api/worker'
import { listWorkTypes } from '@/api/workType'
import type { Worker } from '@/types/worker'
import type { WorkType } from '@/types/workType'

const loading = ref(false)
const saving = ref(false)
const rows = ref<Worker[]>([])
const total = ref(0)

const workTypes = ref<WorkType[]>([])
const workTypeOptions = computed(() => workTypes.value)
const workTypeNameById = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const wt of workTypes.value) map[wt.id] = wt.name
  return map
})

const search = reactive<{ name_like: string; is_active: boolean | undefined }>({
  name_like: '',
  is_active: undefined,
})

// ============ 筛选状态持久化 ============
const { restore: restoreWorkerFilter, clear: clearWorkerFilter } = useListStatePersist(
  'worker_list',
  { search },
)

// ============ 列可见性 ============
// 「#」和「操作」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'badge_code', label: '工牌码' },
  { key: 'name', label: '姓名' },
  { key: 'work_type', label: '工种' },
  { key: 'is_active', label: '状态' },
  { key: 'created_at', label: '创建时间' },
  { key: 'updated_at', label: '更新时间' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'worker_list' })

const dialogVisible = ref(false)
const editing = ref<Worker | null>(null)
const form = reactive<{ badge_code: string; name: string; work_type_id: string | null }>({
  badge_code: '',
  name: '',
  work_type_id: null,
})
const formRef = ref<{ validate: () => Promise<boolean> } | null>(null)
// 弹窗尺寸：桌面 420px，手机 92vw + 6vh
const workerDlg = useDialogSize({ desktopWidth: 420 })

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    const res = await listWorkers({
      name_like: search.name_like || undefined,
      is_active: search.is_active,
      limit: 200,
    })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  fetchList()
}
function onReset(): void {
  search.name_like = ''
  search.is_active = undefined
  fetchList()
}
function onNew(): void {
  editing.value = null
  form.badge_code = ''
  form.name = ''
  form.work_type_id = null
  dialogVisible.value = true
}
function onEdit(row: Worker): void {
  editing.value = row
  form.badge_code = row.badge_code
  form.name = row.name
  form.work_type_id = row.work_type_id ?? null
  dialogVisible.value = true
}

async function onSave(): Promise<void> {
  if (!form.badge_code.trim() || !form.name.trim()) {
    ElMessage.warning('工牌码和姓名不能为空')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateWorker(editing.value.id, {
        badge_code: form.badge_code.trim(),
        name: form.name.trim(),
        work_type_id: form.work_type_id,
      })
      ElMessage.success('已保存')
    } else {
      await createWorker({
        badge_code: form.badge_code.trim(),
        name: form.name.trim(),
        work_type_id: form.work_type_id,
      })
      ElMessage.success('已新增')
    }
    // 客户端不再持有 worker 缓存（findWorkerByBadge 改打后端），无需手动失效。
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
  form.badge_code = ''
  form.name = ''
  form.work_type_id = null
}

async function onDeactivate(row: Worker): Promise<void> {
  await ElMessageBox.confirm(
    `确认停用「${row.name}」？停用后该工牌将不能扫码领取。`,
    '提示',
    { confirmButtonText: '停用', cancelButtonText: '取消', type: 'warning' },
  )
    .then(async () => {
      await deactivateWorker(row.id)
      ElMessage.success('已停用')
      fetchList()
    })
    .catch(() => undefined)
}

async function onReactivate(row: Worker): Promise<void> {
  try {
    await reactivateWorker(row.id)
    ElMessage.success('已重新启用')
    fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '启用失败')
  }
}

onMounted(async () => {
  // 先尝试恢复 localStorage 中的搜索条件
  const persisted = restoreWorkerFilter()
  if (persisted) {
    Object.assign(search, persisted.search)
  }
  await fetchList()
  try {
    const res = await listWorkTypes({ limit: 200 })
    workTypes.value = res.items
  } catch {
    // 不阻塞主页面
  }
})
</script>

<style lang="scss" scoped>
.worker-list { display: flex; flex-direction: column; gap: 12px; }
.filter-card :deep(.el-card__body) { padding-bottom: 0; }
.muted { color: var(--text-secondary); }
</style>