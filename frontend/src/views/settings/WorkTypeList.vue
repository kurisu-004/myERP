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

    <el-card shadow="never">
      <el-table :data="rows" v-loading="loading" stripe border size="small">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="code" label="代码" width="140" />
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column prop="description" label="描述" min-width="200" />
        <el-table-column prop="sort_order" label="排序" width="80" />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onEdit(row as WorkType)">编辑</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row as WorkType)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑工种' : '新增工种'" width="420px" @closed="onDialogClosed">
      <el-form :model="form" label-width="80px">
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
  createWorkType,
  listWorkTypes,
  softDeleteWorkType,
  updateWorkType,
} from '@/api/workType'
import type { WorkType } from '@/types/workType'

const loading = ref(false)
const saving = ref(false)
const rows = ref<WorkType[]>([])

const search = reactive({ code_like: '' })
const dialogVisible = ref(false)
const editing = ref<WorkType | null>(null)
const form = reactive<{ code: string; name: string; description: string; sort_order: number }>({
  code: '', name: '', description: '', sort_order: 0,
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
  dialogVisible.value = true
}
function onEdit(row: WorkType): void {
  editing.value = row
  form.code = row.code; form.name = row.name
  form.description = row.description ?? ''; form.sort_order = row.sort_order
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
      })
      ElMessage.success('已保存')
    } else {
      await createWorkType({
        code: form.code.trim(),
        name: form.name.trim(),
        description: form.description.trim() || null,
        sort_order: form.sort_order,
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

onMounted(fetchList)
</script>

<style lang="scss" scoped>
.wt-list { display: flex; flex-direction: column; gap: 12px; }
</style>