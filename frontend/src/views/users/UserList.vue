<template>
  <div class="user-page">
    <div class="page-header">
      <h2>账号管理</h2>
      <el-button type="primary" @click="showCreate = true">新增账号</el-button>
    </div>
    <el-table :data="items" v-loading="loading" stripe>
      <el-table-column prop="username" label="用户名" min-width="120" />
      <el-table-column prop="full_name" label="姓名" min-width="100" />
      <el-table-column label="角色" min-width="200">
        <template #default="{ row }">
          <el-tag v-for="r in row.roles" :key="r.id" size="small" style="margin-right:4px" :type="r.scope_type ? 'warning' : 'primary'">
            {{ r.role }}{{ r.shelf_code ? ` @${r.shelf_code}` : '' }}
          </el-tag>
          <span v-if="!row.roles.length" class="no-roles">无角色</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link size="small" @click="openRoles(row)">角色</el-button>
          <el-button link size="small" @click="editUser(row)">编辑</el-button>
          <el-popconfirm v-if="row.is_active" title="确认停用？" @confirm="doDeactivate(String(row.id))">
            <template #reference><el-button link size="small" type="danger">停用</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="page" :page-size="size" :total="total" layout="total, prev, next"
      @change="fetchData" style="margin-top:16px; justify-content:flex-end"
    />

    <!-- create / edit dialog -->
    <el-dialog v-model="showCreate" :title="editingUser ? '编辑账号' : '新增账号'" width="420px" @closed="resetForm">
      <el-form ref="userFormRef" :model="userForm" :rules="userRules" label-width="80px">
        <el-form-item label="用户名" prop="username"><el-input v-model="userForm.username" :disabled="!!editingUser" /></el-form-item>
        <el-form-item label="姓名" prop="full_name"><el-input v-model="userForm.full_name" /></el-form-item>
        <el-form-item label="密码" prop="password"><el-input v-model="userForm.password" type="password" show-password placeholder="留空不改" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="saveUser" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- role dialog -->
    <el-dialog v-model="showRoles" title="角色管理" width="500px">
      <p style="margin-bottom:8px">当前角色（{{ roleUser?.username }}）：</p>
      <div v-for="r in roleList" :key="r.id" style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <el-tag size="small">{{ r.role }}{{ r.shelf_code ? ` @${r.shelf_code}` : '' }}</el-tag>
        <el-button link size="small" type="danger" @click="removeRole(String(roleUser?.id ?? ''), String(r.id))">移除</el-button>
      </div>
      <el-divider />
      <el-form inline>
        <el-form-item label="加角色">
          <el-select v-model="addRoleForm.role" placeholder="选角色" style="width:140px">
            <el-option label="MANAGER" value="MANAGER" />
            <el-option label="SHELF_ACCOUNT" value="SHELF_ACCOUNT" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="addRoleForm.role === 'SHELF_ACCOUNT'" label="货架">
          <el-select v-model="addRoleForm.shelfId" placeholder="选货架" style="width:160px" clearable>
            <el-option v-for="s in shelfOptions" :key="s.id" :label="`${s.code} (${s.zone === 'PRODUCTION' ? '生产' : '品检'})`" :value="Number(s.id)" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button @click="doAddRole" :disabled="!addRoleForm.role">添加</el-button></el-form-item>
      </el-form>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listUsers, createUser, updateUser, deactivateUser, listUserRoles, addUserRole, removeUserRole } from '@/api/users'
import { listShelves } from '@/api/shelves'
import type { UserOut, UserRoleOut } from '@/types/user'
import type { Shelf } from '@/types/shelf'

const items = ref<UserOut[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const size = ref(20)

const showCreate = ref(false)
const saving = ref(false)
const editingUser = ref<UserOut | null>(null)
const userFormRef = ref()
const userForm = reactive({ username: '', password: '', full_name: '' })
const userRules = {
  username: [{ required: true, message: '必填' }],
  full_name: [{ required: true, message: '必填' }],
}

const showRoles = ref(false)
const roleUser = ref<UserOut | null>(null)
const roleList = ref<UserRoleOut[]>([])
const shelfOptions = ref<Shelf[]>([])
const addRoleForm = reactive<{ role: string; shelfId: number | null }>({ role: '', shelfId: null })

async function fetchData() {
  loading.value = true
  try {
    const res = await listUsers({ limit: size.value, offset: (page.value - 1) * size.value })
    items.value = res.items; total.value = res.total
  } finally { loading.value = false }
}

function resetForm() { userForm.username = ''; userForm.password = ''; userForm.full_name = ''; editingUser.value = null }
function editUser(obj: any) { const u = obj as UserOut; editingUser.value = u; userForm.username = u.username; userForm.full_name = u.full_name; userForm.password = ''; showCreate.value = true }

async function saveUser() {
  const valid = await userFormRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editingUser.value) {
      const p: any = { full_name: userForm.full_name }
      if (userForm.password) p.password = userForm.password
      await updateUser(String(editingUser.value.id), p)
    } else {
      await createUser({ username: userForm.username, password: userForm.password || 'changeme', full_name: userForm.full_name })
    }
    showCreate.value = false
    await fetchData()
    ElMessage.success('已保存')
  } catch (e: any) { ElMessage.error(e?.message || '保存失败') }
  finally { saving.value = false }
}

async function doDeactivate(id: string) { await deactivateUser(id); await fetchData(); ElMessage.success('已停用') }

async function openRoles(obj: any) { const u = obj as UserOut;
  roleUser.value = u
  roleList.value = await listUserRoles(String(u.id))
  shelfOptions.value = (await listShelves({ is_active: true, limit: 200 })).items
  addRoleForm.role = ''; addRoleForm.shelfId = null
  showRoles.value = true
}

async function doAddRole() {
  if (!roleUser.value || !addRoleForm.role) return
  const scopeType = addRoleForm.role === 'SHELF_ACCOUNT' ? 'shelf' : null
  const scopeId = addRoleForm.role === 'SHELF_ACCOUNT' ? addRoleForm.shelfId : null
  if (addRoleForm.role === 'SHELF_ACCOUNT' && !scopeId) { ElMessage.warning('请选择货架'); return }
  try {
    await addUserRole(String(roleUser.value.id), { role: addRoleForm.role, scope_type: scopeType, scope_id: scopeId })
    roleList.value = await listUserRoles(String(roleUser.value.id))
    ElMessage.success('已添加')
  } catch (e: any) { ElMessage.error(e?.message || '添加失败') }
}

async function removeRole(uid: string, rid: string) { await removeUserRole(uid, rid); roleList.value = await listUserRoles(uid); ElMessage.success('已移除') }

onMounted(fetchData)
</script>

<style lang="scss" scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; h2 { margin: 0; font-size: 18px; } }
.no-roles { color: #c0c4cc; font-size: 13px; }
</style>
