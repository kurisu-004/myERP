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
      <el-table :data="rows" v-loading="loading" stripe border size="small">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="name" label="公司名" min-width="160" />
        <el-table-column prop="contact_name" label="联系人" min-width="100">
          <template #default="{ row }">
            {{ (row as OutsourceCompany).contact_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column prop="contact_phone" label="联系电话" min-width="120">
          <template #default="{ row }">
            {{ (row as OutsourceCompany).contact_phone || '—' }}
          </template>
        </el-table-column>
        <el-table-column prop="address" label="地址" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            {{ (row as OutsourceCompany).address || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="(row as OutsourceCompany).is_active ? 'success' : 'info'" size="small">
              {{ (row as OutsourceCompany).is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onEdit(row as OutsourceCompany)">编辑</el-button>
            <el-button link type="warning" size="small" @click="onManageProcesses(row as OutsourceCompany)">维护工序</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row as OutsourceCompany)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="table-footer">
        <el-pagination
          v-model:current-page="search.offset"
          v-model:page-size="search.limit"
          :total="total"
          :page-sizes="[50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="fetchList"
          @current-change="fetchList"
        />
      </div>
    </el-card>

    <!-- CRUD 对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑外协公司' : '新增外协公司'"
      width="520px"
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
      width="520px"
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
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, RefreshLeft, Plus } from '@element-plus/icons-vue'
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
  void fetchList()
})
</script>

<style lang="scss" scoped>
.outsource-list { display: flex; flex-direction: column; gap: 12px; }
.table-footer { display: flex; justify-content: flex-end; margin-top: 12px; }
.process-check-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 200px;
  overflow-y: auto;
}
.muted { color: #909399; font-size: 12px; }
</style>