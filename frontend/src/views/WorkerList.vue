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

    <el-card shadow="never" class="table-card">
      <el-table :data="rows" v-loading="loading" stripe border size="small">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="badge_code" label="工牌码" width="160" />
        <el-table-column prop="name" label="姓名" min-width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="(row as Worker).is_active ? 'success' : 'info'" effect="light" size="small">
              {{ (row as Worker).is_active ? '在职' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="170" />
        <el-table-column prop="updated_at" label="更新时间" min-width="170" />
        <el-table-column label="操作" width="220" fixed="right">
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
      </el-table>
    </el-card>

    <!-- 新增/编辑弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑工人' : '新增工人'"
      width="420px"
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
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, RefreshLeft, Plus } from '@element-plus/icons-vue'
import {
  createWorker,
  deactivateWorker,
  listWorkers,
  reactivateWorker,
  updateWorker,
} from '@/api/worker'
import type { Worker } from '@/types/worker'

const loading = ref(false)
const saving = ref(false)
const rows = ref<Worker[]>([])
const total = ref(0)

const search = reactive<{ name_like: string; is_active: boolean | undefined }>({
  name_like: '',
  is_active: undefined,
})

const dialogVisible = ref(false)
const editing = ref<Worker | null>(null)
const form = reactive<{ badge_code: string; name: string }>({
  badge_code: '',
  name: '',
})
const formRef = ref<{ validate: () => Promise<boolean> } | null>(null)

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
  dialogVisible.value = true
}
function onEdit(row: Worker): void {
  editing.value = row
  form.badge_code = row.badge_code
  form.name = row.name
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
      })
      ElMessage.success('已保存')
    } else {
      await createWorker({
        badge_code: form.badge_code.trim(),
        name: form.name.trim(),
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

onMounted(fetchList)
</script>

<style lang="scss" scoped>
.worker-list { display: flex; flex-direction: column; gap: 12px; }
.filter-card :deep(.el-card__body) { padding-bottom: 0; }
</style>