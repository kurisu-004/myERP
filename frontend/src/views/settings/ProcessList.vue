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
          <el-button type="success" @click="onNew"><el-icon><Plus /></el-icon><span>新增工序</span></el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <el-table :data="rows" v-loading="loading" stripe border size="small">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="code" label="代码" width="120" />
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column label="类别" width="100">
          <template #default="{ row }">
            <el-tag :type="(row as Process).category === 'INHOUSE' ? 'primary' : 'warning'" size="small">
              {{ PROCESS_CATEGORY_LABEL[(row as Process).category] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="品检" width="80">
          <template #default="{ row }">
            <el-tag v-if="(row as Process).is_inspection" type="success" size="small">是</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="sort_order" label="排序" width="80" />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onEdit(row as Process)">编辑</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row as Process)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑工序' : '新增工序'" width="460px" @closed="onDialogClosed">
      <el-form :model="form" label-width="80px">
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
        <el-form-item label="品检工序">
          <el-switch v-model="form.is_inspection" />
          <span style="margin-left: 8px; color: #909399; font-size: 12px">
            仅作 UI 提示,不影响取件过滤
          </span>
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
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, RefreshLeft, Plus } from '@element-plus/icons-vue'
import {
  createProcess,
  listProcesses,
  softDeleteProcess,
  updateProcess,
} from '@/api/process'
import type { Process, ProcessCategory } from '@/types/process'
import { PROCESS_CATEGORY_LABEL } from '@/types/process'

const loading = ref(false)
const saving = ref(false)
const rows = ref<Process[]>([])
const search = reactive<{ code_like: string; category: ProcessCategory | undefined }>({
  code_like: '',
  category: undefined,
})
const dialogVisible = ref(false)
const editing = ref<Process | null>(null)
const form = reactive<{
  code: string; name: string; category: ProcessCategory
  is_inspection: boolean; sort_order: number; description: string
}>({ code: '', name: '', category: 'INHOUSE', is_inspection: false, sort_order: 0, description: '' })

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
  Object.assign(form, { code: '', name: '', category: 'INHOUSE', is_inspection: false, sort_order: 0, description: '' })
  dialogVisible.value = true
}
function onEdit(row: Process): void {
  editing.value = row
  Object.assign(form, {
    code: row.code, name: row.name, category: row.category,
    is_inspection: row.is_inspection, sort_order: row.sort_order,
    description: row.description ?? '',
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
        is_inspection: form.is_inspection,
        sort_order: form.sort_order,
        description: form.description.trim() || null,
      })
      ElMessage.success('已保存')
    } else {
      await createProcess({
        code: form.code.trim(),
        name: form.name.trim(),
        category: form.category,
        is_inspection: form.is_inspection,
        sort_order: form.sort_order,
        description: form.description.trim() || null,
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
  Object.assign(form, { code: '', name: '', category: 'INHOUSE', is_inspection: false, sort_order: 0, description: '' })
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

onMounted(fetchList)
</script>

<style lang="scss" scoped>
.process-list { display: flex; flex-direction: column; gap: 12px; }
</style>