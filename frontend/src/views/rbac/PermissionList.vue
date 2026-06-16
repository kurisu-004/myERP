<template>
  <div class="page">
    <el-alert
      v-if="!auth.isSuperadmin"
      type="warning"
      :closable="false"
      title="此页面仅超级管理员可访问"
      show-icon
    />

    <div v-else class="card">
      <div class="card-header">
        <div class="card-title">
          <el-icon><Key /></el-icon>
          <span>菜单 / 权限点</span>
        </div>
        <div class="card-actions">
          <el-button type="primary" :icon="Plus" @click="openCreate">新建权限点</el-button>
        </div>
      </div>

      <el-table v-loading="loading" :data="list" border stripe style="width: 100%">
        <el-table-column prop="code" label="代码" width="240" />
        <el-table-column prop="name" label="名称" width="160" />
        <el-table-column label="类型" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="(row as Permission).type === 'MENU' ? 'primary' : 'info'" effect="plain" size="small">
              {{ (row as Permission).type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="path" label="路径/组件" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="muted">{{ (row as Permission).path || (row as Permission).component || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="sort_order" label="排序" width="80" align="center" />
        <el-table-column label="启用" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="(row as Permission).is_enabled ? 'success' : 'info'" effect="light" size="small">
              {{ (row as Permission).is_enabled ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right" align="center">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="openEdit(row as Permission)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑权限点' : '新建权限点'"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="代码" prop="code">
          <el-input v-model="form.code" :disabled="editing" placeholder="如 order:menu" />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="类型" prop="type">
          <el-select v-model="form.type" :disabled="editing" style="width: 100%">
            <el-option label="MENU 菜单" value="MENU" />
            <el-option label="API 接口" value="API" />
            <el-option label="BUTTON 按钮(预留)" value="BUTTON" />
            <el-option label="DATA 数据范围(预留)" value="DATA" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.type === 'MENU'" label="父级菜单">
          <el-select v-model="form.parent_id" clearable placeholder="无 (顶级菜单)" style="width: 100%">
            <el-option
              v-for="p in parentOptions"
              :key="p.id"
              :label="`${p.name} (${p.code})`"
              :value="Number(p.id)"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.type === 'MENU'" label="路由路径">
          <el-input v-model="form.path" placeholder="如 /order" />
        </el-form-item>
        <el-form-item v-if="form.type === 'MENU'" label="图标">
          <el-input v-model="form.icon" placeholder="如 Box" />
        </el-form-item>
        <el-form-item v-if="form.type === 'MENU'" label="组件路径">
          <el-input v-model="form.component" placeholder="如 @/views/order/Index.vue" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" :max="9999" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_enabled" :active-value="1" :inactive-value="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Key, Plus } from '@element-plus/icons-vue'
import * as rbacApi from '@/api/rbac'
import { useAuthStore } from '@/store/auth'
import type { Permission } from '@/types/rbac'

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const list = ref<Permission[]>([])

const dialogVisible = ref(false)
const editing = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({
  id: null as number | null,
  code: '',
  name: '',
  type: 'MENU' as 'MENU' | 'API' | 'BUTTON' | 'DATA',
  parent_id: null as number | null,
  path: '' as string,
  icon: '' as string,
  component: '' as string,
  sort_order: 0,
  is_enabled: 1,
})
const rules: FormRules<typeof form> = {
  code: [{ required: true, message: '请输入代码', trigger: 'blur' }],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
}

const parentOptions = computed<Permission[]>(() =>
  list.value.filter((p) => p.type === 'MENU' && String(p.id) !== String(form.id ?? '')),
)

async function reload() {
  loading.value = true
  try {
    const r = await rbacApi.listPermissions()
    list.value = r.data
  } catch (e) {
    handleErr(e, '加载失败')
  } finally {
    loading.value = false
  }
}

function handleErr(e: unknown, fallback: string) {
  if (e instanceof Error) ElMessage.error(e.message || fallback)
  else ElMessage.error(fallback)
}

function openCreate() {
  editing.value = false
  Object.assign(form, {
    id: null,
    code: '',
    name: '',
    type: 'MENU',
    parent_id: null,
    path: '',
    icon: '',
    component: '',
    sort_order: 0,
    is_enabled: 1,
  })
  dialogVisible.value = true
}

function openEdit(row: Permission) {
  editing.value = true
  Object.assign(form, {
    id: Number(row.id),
    code: row.code,
    name: row.name,
    type: row.type as any,
    parent_id: row.parent_id ? Number(row.parent_id) : null,
    path: row.path || '',
    icon: row.icon || '',
    component: row.component || '',
    sort_order: row.sort_order,
    is_enabled: row.is_enabled,
  })
  dialogVisible.value = true
}

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editing.value) {
      await rbacApi.updatePermission(String(form.id), {
        name: form.name,
        parent_id: form.parent_id,
        path: form.path || null,
        icon: form.icon || null,
        component: form.component || null,
        sort_order: form.sort_order,
        is_enabled: form.is_enabled,
      })
      ElMessage.success('已更新')
    } else {
      await rbacApi.createPermission({
        code: form.code,
        name: form.name,
        type: form.type,
        parent_id: form.parent_id,
        path: form.path || null,
        icon: form.icon || null,
        component: form.component || null,
        sort_order: form.sort_order,
        is_enabled: form.is_enabled,
      })
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    reload()
  } catch (e) {
    handleErr(e, '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  if (!auth.isSuperadmin) return
  reload()
})
</script>

<style lang="scss" scoped>
.page { display: flex; flex-direction: column; gap: 12px; }
.card { background: #fff; border-radius: 6px; padding: 16px 20px; box-shadow: var(--shadow-sm); }
.card-header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 12px; margin-bottom: 12px; border-bottom: 1px solid var(--border-color); }
.card-title { display: flex; align-items: center; gap: 6px; font-size: 15px; font-weight: 600; color: var(--text-primary); .el-icon { color: var(--primary-color); font-size: 16px; } }
.muted { color: var(--text-secondary); font-size: 12px; }
</style>
