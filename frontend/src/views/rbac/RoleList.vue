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
          <el-icon><UserFilled /></el-icon>
          <span>角色管理</span>
        </div>
        <div class="card-actions">
          <el-button type="primary" :icon="Plus" @click="openCreate">新建角色</el-button>
        </div>
      </div>

      <el-form :inline="true" :model="search" class="search-bar">
        <el-form-item label="关键字">
          <el-input v-model="search.keyword" placeholder="代码 / 名称" clearable @keyup.enter="reload(1)" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="reload(1)">查询</el-button>
          <el-button :icon="RefreshLeft" @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="list" border stripe style="width: 100%">
        <el-table-column type="index" label="#" width="55" align="center" />
        <el-table-column prop="code" label="代码" width="200" />
        <el-table-column prop="name" label="名称" width="160" />
        <el-table-column label="内置" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="(row as Role).is_builtin" type="warning" effect="dark" size="small">内置</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
        <el-table-column label="操作" width="180" fixed="right" align="center">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="openEdit(row as Role)">编辑</el-button>
            <el-button
              type="danger"
              link
              size="small"
              :disabled="(row as Role).is_builtin === 1"
              @click="remove(row as Role)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="size"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="reload()"
          @size-change="reload(1)"
        />
      </div>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑角色' : '新建角色'"
      width="640px"
      :close-on-click-modal="false"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="代码" prop="code">
          <el-input v-model="form.code" :disabled="editing" placeholder="如 R_NEW" />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="权限">
          <div class="perm-tree">
            <el-input v-model="permFilter" placeholder="搜索权限" clearable size="small" class="mb-2" />
            <el-tree
              ref="treeRef"
              :data="permissionTree"
              show-checkbox
              node-key="id"
              :default-checked-keys="form.permission_ids.map(String)"
              :props="{ label: 'label', children: 'children' }"
              :filter-node-method="filterNode"
            />
          </div>
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
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import {
  UserFilled,
  Plus,
  Search,
  RefreshLeft,
} from '@element-plus/icons-vue'
import * as rbacApi from '@/api/rbac'
import { useAuthStore } from '@/store/auth'
import type { Permission, Role } from '@/types/rbac'

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const list = ref<Role[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const search = reactive({ keyword: '' })

const allPerms = ref<Permission[]>([])

const permFilter = ref('')
const treeRef = ref()
watch(permFilter, (v) => {
  treeRef.value?.filter(v)
})

const permissionTree = computed<Array<{ id: string; label: string; children?: any[] }>>(() => {
  const map = new Map<string, any>()
  const roots: any[] = []
  for (const p of allPerms.value) {
    map.set(p.id, { id: p.id, label: `${p.name} (${p.code})`, children: [] as any[] })
  }
  for (const p of allPerms.value) {
    const node = map.get(p.id)!
    if (p.parent_id && map.has(p.parent_id)) {
      map.get(p.parent_id)!.children.push(node)
    } else {
      roots.push(node)
    }
  }
  // 只保留 MENU 类型作为树,API 类型作为叶子
  return roots
    .filter((r) => {
      const perm = allPerms.value.find((p) => p.id === r.id)
      return perm?.type === 'MENU'
    })
    .map((r) => r)
})

function filterNode(value: string, data: any): boolean {
  if (!value) return true
  return data.label.includes(value)
}

const dialogVisible = ref(false)
const editing = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({
  id: null as number | null,
  code: '',
  name: '',
  description: '' as string,
  permission_ids: [] as number[],
})
const rules: FormRules<typeof form> = {
  code: [{ required: true, message: '请输入代码', trigger: 'blur' }],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
}

async function reload(p?: number) {
  if (p) page.value = p
  loading.value = true
  try {
    const r = await rbacApi.listRoles({ page: page.value, size: size.value, keyword: search.keyword || undefined })
    list.value = r.data.items
    total.value = r.data.total
  } catch (e) {
    handleErr(e, '加载失败')
  } finally {
    loading.value = false
  }
}

function resetSearch() {
  search.keyword = ''
  reload(1)
}

function handleErr(e: unknown, fallback: string) {
  if (e instanceof Error) ElMessage.error(e.message || fallback)
  else ElMessage.error(fallback)
}

function openCreate() {
  editing.value = false
  Object.assign(form, { id: null, code: '', name: '', description: '', permission_ids: [] })
  dialogVisible.value = true
  nextTickReset()
}

async function openEdit(row: Role) {
  editing.value = true
  Object.assign(form, { id: Number(row.id), code: row.code, name: row.name, description: row.description || '', permission_ids: [] })
  dialogVisible.value = true
  // 编辑模式直接给所有权限, 让用户挑选
  nextTickReset()
}

function nextTickReset() {
  setTimeout(() => {
    treeRef.value?.setCheckedKeys([])
  }, 50)
}

async function onSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  // 收集选中的菜单+所有 API
  const checkedMenuIds = (treeRef.value?.getCheckedNodes?.() || [])
    .filter((n: any) => allPerms.value.find((p) => p.id === n.id)?.type === 'MENU')
    .map((n: any) => Number(n.id))
  const halfCheckedMenuIds = (treeRef.value?.getHalfCheckedNodes?.() || [])
    .filter((n: any) => allPerms.value.find((p) => p.id === n.id)?.type === 'MENU')
    .map((n: any) => Number(n.id))
  // 这里简化: 只提交选中的菜单, API 权限单独管理
  const permission_ids = [...checkedMenuIds, ...halfCheckedMenuIds]
  saving.value = true
  try {
    if (editing.value) {
      await rbacApi.updateRole(String(form.id), {
        name: form.name,
        description: form.description || null,
        permission_ids,
      })
      ElMessage.success('已更新')
    } else {
      await rbacApi.createRole({
        code: form.code,
        name: form.name,
        description: form.description || null,
        permission_ids,
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

async function remove(row: Role) {
  try {
    await ElMessageBox.confirm(`确定删除角色【${row.name}】？`, '警告', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await rbacApi.deleteRole(String(row.id))
    ElMessage.success('已删除')
    reload()
  } catch (e) {
    handleErr(e, '删除失败')
  }
}

onMounted(async () => {
  if (!auth.isSuperadmin) return
  try {
    const r = await rbacApi.listPermissions()
    allPerms.value = r.data
  } catch (e) {
    handleErr(e, '加载权限失败')
  }
  reload(1)
})
</script>

<style lang="scss" scoped>
.page { display: flex; flex-direction: column; gap: 12px; }
.card { background: #fff; border-radius: 6px; padding: 16px 20px; box-shadow: var(--shadow-sm); }
.card-header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 12px; margin-bottom: 12px; border-bottom: 1px solid var(--border-color); }
.card-title { display: flex; align-items: center; gap: 6px; font-size: 15px; font-weight: 600; color: var(--text-primary); .el-icon { color: var(--primary-color); font-size: 16px; } }
.muted { color: var(--text-secondary); font-size: 12px; }
.search-bar { margin-bottom: 8px; }
.pagination-wrap { display: flex; justify-content: flex-end; margin-top: 12px; }
.perm-tree { width: 100%; max-height: 360px; overflow: auto; padding: 8px; border: 1px solid var(--border-color); border-radius: 4px; }
.mb-2 { margin-bottom: 8px; }
</style>
