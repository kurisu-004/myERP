<template>
  <div class="shelf-page">
    <div class="page-header">
      <h2>货架管理</h2>
      <el-button type="primary" @click="showCreate = true">新增货架</el-button>
    </div>
    <el-table :data="items" v-loading="loading" stripe>
      <el-table-column prop="code" label="代码" width="110" />
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="区域" width="90">
        <template #default="{ row }"><el-tag :type="row.zone === 'PRODUCTION' ? 'primary' : 'warning'" size="small">{{ row.zone === 'PRODUCTION' ? '生产' : '品检' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="location" label="位置" min-width="120" />
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
import { listShelves, createShelf, updateShelf, deactivateShelf } from '@/api/shelves'
import type { Shelf } from '@/types/shelf'

const items = ref<Shelf[]>([])
const loading = ref(false)

const showCreate = ref(false)
const saving = ref(false)
const editingShelf = ref<Shelf | null>(null)
const shelfFormRef = ref()
const shelfForm = reactive({ code: '', name: '', zone: 'PRODUCTION' as string, location: '' })
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

function resetForm() { shelfForm.code = ''; shelfForm.name = ''; shelfForm.zone = 'PRODUCTION'; shelfForm.location = ''; editingShelf.value = null }
function editShelf(s: any) { const sh = s as Shelf; editingShelf.value = sh; shelfForm.code = sh.code; shelfForm.name = sh.name; shelfForm.zone = sh.zone; shelfForm.location = sh.location ?? ''; showCreate.value = true }

async function saveShelf() {
  const valid = await shelfFormRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editingShelf.value) {
      await updateShelf(String(editingShelf.value.id), { name: shelfForm.name, location: shelfForm.location || undefined })
    } else {
      await createShelf({ code: shelfForm.code, name: shelfForm.name, zone: shelfForm.zone, location: shelfForm.location || undefined })
    }
    showCreate.value = false
    await fetchData()
    ElMessage.success('已保存')
  } catch (e: any) { ElMessage.error(e?.message || '保存失败') }
  finally { saving.value = false }
}

async function doDeactivate(id: string) { await deactivateShelf(id); await fetchData(); ElMessage.success('已停用') }

onMounted(fetchData)
</script>

<style lang="scss" scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; h2 { margin: 0; font-size: 18px; } }
</style>
