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
          <el-icon><User /></el-icon>
          <span>用户管理</span>
          <el-tag type="info" effect="plain" class="ml-2">共 {{ total }} 条</el-tag>
        </div>
        <div class="card-actions">
          <el-button type="primary" :icon="Plus" @click="openCreate">新建用户</el-button>
        </div>
      </div>

      <el-form :inline="true" :model="search" class="search-bar">
        <el-form-item label="关键字">
          <el-input
            v-model="search.keyword"
            placeholder="工号 / 用户名 / 姓名"
            clearable
            @keyup.enter="reload(1)"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="reload(1)">查询</el-button>
          <el-button :icon="RefreshLeft" @click="resetSearch">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table
        v-loading="loading"
        :data="list"
        border
        stripe
        style="width: 100%"
      >
        <el-table-column type="index" label="#" width="55" align="center" />
        <el-table-column prop="employee_no" label="工号" width="130" />
        <el-table-column prop="username" label="用户名" width="130" />
        <el-table-column prop="name" label="姓名" width="130" />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="(row as UserRow).is_active ? 'success' : 'info'" effect="light">
              {{ (row as UserRow).is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="角色" min-width="200">
          <template #default="{ row }">
            <el-tag
              v-for="r in ((row as UserRow).roles || [])"
              :key="r.id"
              type="primary"
              effect="plain"
              size="small"
              class="role-tag"
            >
              {{ r.name }}
            </el-tag>
            <span v-if="!(row as UserRow).roles || (row as UserRow).roles!.length === 0" class="muted">未分配</span>
          </template>
        </el-table-column>
        <el-table-column prop="last_login_at" label="最近登录" width="170">
          <template #default="{ row }">
            <span class="muted">{{ (row as UserRow).last_login_at || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right" align="center">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="openEdit(row as UserRow)">编辑</el-button>
            <el-button type="primary" link size="small" @click="openAssign(row as UserRow)">分配角色</el-button>
            <el-button type="danger" link size="small" @click="remove(row as UserRow)">删除</el-button>
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

    <!-- 创建/编辑 弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑用户' : '新建用户'"
      width="540px"
      :close-on-click-modal="false"
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="90px"
      >
        <el-form-item label="工号" prop="employee_no">
          <el-input v-model="form.employee_no" :disabled="editing" placeholder="如 U20260001" />
        </el-form-item>
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" :disabled="editing" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item :label="editing ? '重置密码' : '密码'" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            :placeholder="editing ? '留空表示不修改' : '至少 6 位'"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch
            v-model="form.isActive"
            :active-value="1"
            :inactive-value="0"
            active-text="启用"
            inactive-text="停用"
          />
        </el-form-item>
        <el-form-item v-if="!editing" label="角色">
          <el-select v-model="form.role_ids" multiple placeholder="选择角色" style="width: 100%">
            <el-option
              v-for="r in allRoles"
              :key="r.id"
              :label="r.name"
              :value="Number(r.id)"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSubmit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 分配角色 弹窗 -->
    <el-dialog
      v-model="assignVisible"
      title="分配角色"
      width="480px"
      :close-on-click-modal="false"
    >
      <p class="muted">为【{{ assignTarget?.name }}】分配角色</p>
      <el-select
        v-model="assignIds"
        multiple
        placeholder="选择角色"
        style="width: 100%"
      >
        <el-option
          v-for="r in allRoles"
          :key="r.id"
          :label="r.name"
          :value="Number(r.id)"
        />
      </el-select>
      <template #footer>
        <el-button @click="assignVisible = false">取消</el-button>
        <el-button type="primary" :loading="assigning" @click="onAssign">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import {
  User,
  Plus,
  Search,
  RefreshLeft,
} from '@element-plus/icons-vue'
import * as rbacApi from '@/api/rbac'
import { useAuthStore } from '@/store/auth'
import type { Role, User as UserT } from '@/types/rbac'

type UserRow = UserT & { roles?: Role[] }

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const assigning = ref(false)

const list = ref<UserRow[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const search = reactive({ keyword: '' })

const allRoles = ref<Role[]>([])

const dialogVisible = ref(false)
const editing = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({
  id: null as number | null,
  employee_no: '',
  username: '',
  name: '',
  password: '',
  isActive: 1,
  role_ids: [] as number[],
})
const rules: FormRules<typeof form> = {
  employee_no: [{ required: true, message: '请输入工号', trigger: 'blur' }],
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  password: [
    {
      validator: (_r, v, cb) => {
        if (!editing.value && (!v || v.length < 6)) {
          return cb(new Error('密码至少 6 位'))
        }
        if (editing.value && v && v.length < 6) {
          return cb(new Error('密码至少 6 位'))
        }
        cb()
      },
      trigger: 'blur',
    },
  ],
}

const assignVisible = ref(false)
const assignTarget = ref<UserRow | null>(null)
const assignIds = ref<number[]>([])

async function reload(p?: number) {
  if (p) page.value = p
  loading.value = true
  try {
    const r = await rbacApi.listUsers({ page: page.value, size: size.value, keyword: search.keyword || undefined })
    list.value = r.data.items
    total.value = r.data.total
    // 串行加载每个用户的角色详情 (页面体量小,直接拿)
    for (const u of list.value) {
      try {
        const d = await rbacApi.getUserDetail(String(u.id))
        u.roles = d.data.roles
      } catch {
        u.roles = []
      }
    }
  } catch (e) {
    handleErr(e, '加载用户失败')
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
  Object.assign(form, {
    id: null,
    employee_no: '',
    username: '',
    name: '',
    password: '',
    isActive: 1,
    role_ids: [],
  })
  dialogVisible.value = true
}

function openEdit(row: UserT) {
  editing.value = true
  Object.assign(form, {
    id: Number(row.id),
    employee_no: row.employee_no,
    username: row.username,
    name: row.name,
    password: '',
    isActive: row.is_active,
    role_ids: [],
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
      await rbacApi.updateUser(String(form.id), {
        name: form.name,
        is_active: form.isActive,
        password: form.password || undefined,
      })
      ElMessage.success('已更新')
    } else {
      await rbacApi.createUser({
        employee_no: form.employee_no,
        username: form.username,
        name: form.name,
        password: form.password,
        role_ids: form.role_ids,
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

function openAssign(row: UserRow) {
  assignTarget.value = row
  assignIds.value = (row.roles || []).map((r: Role) => Number(r.id))
  assignVisible.value = true
}

async function onAssign() {
  if (!assignTarget.value) return
  assigning.value = true
  try {
    await rbacApi.assignUserRoles(String(assignTarget.value.id), assignIds.value)
    ElMessage.success('分配成功')
    assignVisible.value = false
    reload()
  } catch (e) {
    handleErr(e, '分配失败')
  } finally {
    assigning.value = false
  }
}

async function remove(row: UserT) {
  try {
    await ElMessageBox.confirm(`确定删除用户【${row.name}】？该操作不可撤销`, '警告', {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await rbacApi.deleteUser(String(row.id))
    ElMessage.success('已删除')
    reload()
  } catch (e) {
    handleErr(e, '删除失败')
  }
}

onMounted(async () => {
  if (!auth.isSuperadmin) return
  try {
    const r = await rbacApi.listAllRoles()
    allRoles.value = r.data
  } catch (e) {
    handleErr(e, '加载角色失败')
  }
  reload(1)
})
</script>

<style lang="scss" scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.card {
  background: #fff;
  border-radius: 6px;
  padding: 16px 20px;
  box-shadow: var(--shadow-sm);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
}

.card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);

  .el-icon {
    color: var(--primary-color);
    font-size: 16px;
  }
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ml-2 { margin-left: 8px; }
.muted { color: var(--text-secondary); font-size: 12px; }

.search-bar {
  margin-bottom: 8px;
}

.role-tag {
  margin-right: 4px;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
