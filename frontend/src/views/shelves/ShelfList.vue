<template>
  <div class="shelf-page">
    <div class="page-header">
      <h2>货架管理</h2>
      <el-button type="primary" @click="showCreate = true">新增货架</el-button>
    </div>
    <el-table :data="items" v-loading="loading" stripe :default-sort="{ prop: 'display_order', order: 'ascending' }">
      <el-table-column prop="code" label="代码" width="110" />
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="区域" width="90">
        <template #default="{ row }"><el-tag :type="row.zone === 'PRODUCTION' ? 'primary' : 'warning'" size="small">{{ row.zone === 'PRODUCTION' ? '生产' : '品检' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="location" label="位置" min-width="120" />
      <el-table-column prop="display_order" label="物理顺序" width="100" align="center" sortable>
        <template #default="{ row }">
          <el-tag
            :type="row.display_order > 0 ? 'info' : 'warning'"
            size="small"
            effect="plain"
          >
            {{ row.display_order > 0 ? row.display_order : '未设置' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="account_count" label="账号数" width="80" align="center" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'danger'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button link size="small" @click="editShelf(row)">编辑</el-button>
          <el-popconfirm v-if="row.is_active" title="确认停用？" @confirm="doDeactivate(String(row.id))">
            <template #reference><el-button link size="small" type="danger">停用</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showCreate" :title="editingShelf ? '编辑货架' : '新增货架'" width="400px" @closed="resetForm">
      <el-form ref="shelfFormRef" :model="shelfForm" :rules="shelfRules" label-width="80px">
        <el-form-item label="代码" prop="code"><el-input v-model="shelfForm.code" :disabled="!!editingShelf" placeholder="如 PROD-A1" /></el-form-item>
        <el-form-item label="名称" prop="name"><el-input v-model="shelfForm.name" placeholder="如 生产区-A1 货架" /></el-form-item>
        <el-form-item label="区域" prop="zone">
          <el-select v-model="shelfForm.zone" style="width:100%">
            <el-option label="生产区" value="PRODUCTION" /><el-option label="品检区" value="INSPECTION" />
          </el-select>
        </el-form-item>
        <el-form-item label="位置" prop="location"><el-input v-model="shelfForm.location" placeholder="可选的自由文本" /></el-form-item>
        <el-form-item label="物理顺序" prop="display_order">
          <el-input-number
            v-model="shelfForm.display_order"
            :min="0"
            :step="1"
            controls-position="right"
            placeholder="0=未设置"
          />
          <span class="field-hint">用于共享 HMI 卡片网格 picker 的物理顺序；0=未设置</span>
        </el-form-item>
        <el-form-item label="工序">
          <el-select
            v-model="selectedProcessIds"
            multiple filterable
            placeholder="选择该货架可执行的工序（可多选）"
            style="width:100%"
          >
            <el-option
              v-for="p in allProcesses"
              :key="p.id"
              :label="`${p.code} — ${p.name}`"
              :value="p.id"
            >
              <span style="font-weight:600">{{ p.code }}</span>
              <span style="margin-left:4px">{{ p.name }}</span>
              <el-tag :type="p.category === 'INHOUSE' ? 'primary' : 'warning'" size="small" style="margin-left:6px">{{ PROCESS_CATEGORY_LABEL[p.category] }}</el-tag>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="saveShelf" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listShelves, createShelf, updateShelf, deactivateShelf, getShelfProcesses, setShelfProcesses } from '@/api/shelves'
import { listProcesses } from '@/api/process'
import type { Shelf } from '@/types/shelf'
import type { Process } from '@/types/process'
import { PROCESS_CATEGORY_LABEL } from '@/types/process'

const items = ref<Shelf[]>([])
const loading = ref(false)

const showCreate = ref(false)
const saving = ref(false)
const allProcesses = ref<Process[]>([])
const selectedProcessIds = ref<string[]>([])
const editingShelf = ref<Shelf | null>(null)
const shelfFormRef = ref()
const shelfForm = reactive({ code: '', name: '', zone: 'PRODUCTION' as string, location: '', display_order: 0 })
const shelfRules = {
  code: [{ required: true, message: '必填' }],
  name: [{ required: true, message: '必填' }],
  zone: [{ required: true, message: '必选' }],
}

async function fetchData() {
  loading.value = true
  try { items.value = (await listShelves({ limit: 200 })).items }
  finally { loading.value = false }
}

function resetForm() { shelfForm.code = ''; shelfForm.name = ''; shelfForm.zone = 'PRODUCTION'; shelfForm.location = ''; shelfForm.display_order = 0; selectedProcessIds.value = []; editingShelf.value = null }
async function editShelf(s: any) { const sh = s as Shelf; editingShelf.value = sh; shelfForm.code = sh.code; shelfForm.name = sh.name; shelfForm.zone = sh.zone; shelfForm.location = sh.location ?? ''; shelfForm.display_order = sh.display_order ?? 0; showCreate.value = true; try { const sp = await getShelfProcesses(String(sh.id)); selectedProcessIds.value = sp.processes.map((p) => p.process_id) } catch { selectedProcessIds.value = [] } }

async function saveShelf() {
  const valid = await shelfFormRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    let shelfId: string
    if (editingShelf.value) {
      await updateShelf(String(editingShelf.value.id), {
        name: shelfForm.name,
        location: shelfForm.location || undefined,
        display_order: shelfForm.display_order,
      })
      shelfId = String(editingShelf.value.id)
    } else {
      const created = await createShelf({
        code: shelfForm.code,
        name: shelfForm.name,
        zone: shelfForm.zone,
        location: shelfForm.location || undefined,
        display_order: shelfForm.display_order,
      })
      shelfId = String(created.id)
    }
    await setShelfProcesses(shelfId, { process_ids: selectedProcessIds.value })
    showCreate.value = false
    await fetchData()
    ElMessage.success('已保存')
  } catch (e: any) { ElMessage.error(e?.message || '保存失败') }
  finally { saving.value = false }
}

async function doDeactivate(id: string) { await deactivateShelf(id); await fetchData(); ElMessage.success('已停用') }

onMounted(async () => {
  await fetchData()
  try { const res = await listProcesses({ limit: 200 }); allProcesses.value = res.items } catch { /* ignore */ }
})
</script>

<style lang="scss" scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; h2 { margin: 0; font-size: 18px; } }
.field-hint {
  margin-left: 12px;
  font-size: 12px;
  color: #909399;
}
</style>
