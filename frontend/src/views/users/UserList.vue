<template>
  <div class="user-page">
    <div class="page-header">
      <h2>账号管理</h2>
      <el-button type="primary" @click="showCreate = true">新增账号</el-button>
    </div>
    <ResponsiveList
      :items="items"
      :loading="loading"
      row-key="id"
      empty-text="暂无账号"
      stripe
    >
      <template #toolbar>
        <ColumnVisibilityPopover
          :defs="columnDefs"
          :model-value="columnVisibility.currentMap" @update:model-value="columnVisibility.update"
          @reset="columnVisibility.showAll"
        />
      </template>
      <el-table-column
        v-if="columnVisibility.isVisible('username')"
        prop="username" label="用户名" min-width="120" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('full_name')"
        prop="full_name" label="姓名" min-width="100" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('roles')"
        label="角色" min-width="200" align="center"
      >
        <template #default="{ row }">
          <el-tag v-for="r in row.roles" :key="r.id" size="small" style="margin-right:4px" :type="r.scope_type ? 'warning' : 'primary'">
            {{ r.role }}{{ r.shelf_code ? ` @${r.shelf_code}` : '' }}
          </el-tag>
          <span v-if="!row.roles.length" class="no-roles">无角色</span>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('is_active')"
        label="状态" min-width="80" align="center"
      >
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" min-width="280" fixed="right" align="center">
        <template #default="{ row }">
          <el-button link size="small" @click="openRoles(row)">角色</el-button>
          <el-button link size="small" @click="editUser(row)">编辑</el-button>
          <el-popconfirm title="确认重置为默认密码 changeme？" width="240" @confirm="doReset(String(row.id))">
            <template #reference><el-button link size="small" type="warning">重置密码</el-button></template>
          </el-popconfirm>
          <el-popconfirm v-if="row.is_active" title="确认停用？" @confirm="doDeactivate(String(row.id))">
            <template #reference><el-button link size="small" type="danger">停用</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>

      <template #card="{ row }">
        <div class="rl-card-head">
          <span class="rl-card-title">{{ (row as UserOut).username }}</span>
          <el-tag :type="(row as UserOut).is_active ? 'success' : 'danger'" size="small">
            {{ (row as UserOut).is_active ? '启用' : '停用' }}
          </el-tag>
        </div>
        <div class="rl-card-sub">{{ (row as UserOut).full_name || '未填写姓名' }}</div>
        <div class="rl-kv">
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">角色</span>
            <span class="rl-kv__val role-tags">
              <el-tag
                v-for="r in (row as UserOut).roles"
                :key="r.id"
                size="small"
                :type="r.scope_type ? 'warning' : 'primary'"
              >
                {{ r.role }}{{ r.shelf_code ? ` @${r.shelf_code}` : '' }}
              </el-tag>
              <span v-if="!(row as UserOut).roles.length" class="no-roles">无角色</span>
            </span>
          </div>
        </div>
        <div class="rl-card-actions">
          <el-button link size="small" @click="openRoles(row)">角色</el-button>
          <el-button link size="small" @click="editUser(row)">编辑</el-button>
          <el-popconfirm title="确认重置为默认密码 changeme？" width="240" @confirm="doReset(String((row as UserOut).id))">
            <template #reference><el-button link size="small" type="warning">重置密码</el-button></template>
          </el-popconfirm>
          <el-popconfirm v-if="(row as UserOut).is_active" title="确认停用？" @confirm="doDeactivate(String((row as UserOut).id))">
            <template #reference><el-button link size="small" type="danger">停用</el-button></template>
          </el-popconfirm>
        </div>
      </template>
    </ResponsiveList>
    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="size"
        :page-sizes="[20, 50, 100]"
        :total="total"
        :layout="paginationLayout"
        :pager-count="isMobile ? 5 : 7"
        @current-change="fetchData"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- create / edit dialog -->
    <el-dialog
      v-model="showCreate"
      :title="editingUser ? '编辑账号' : '新增账号'"
      :width="userDlg.width.value"
      :top="userDlg.top.value"
      @closed="resetForm"
    >
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
    <el-dialog
      v-model="showRoles"
      title="角色管理"
      :width="rolesDlg.width.value"
      :top="rolesDlg.top.value"
    >
      <p style="margin-bottom:8px">当前角色（{{ roleUser?.username }}）：</p>
      <div v-for="r in roleList" :key="r.id" style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <el-tag size="small">{{ r.role }}{{ r.shelf_code ? ` @${r.shelf_code}` : '' }}</el-tag>
        <el-button link size="small" type="danger" @click="removeRole(String(roleUser?.id ?? ''), String(r.id))">移除</el-button>
      </div>
      <el-divider />
      <el-form inline>
        <el-form-item label="加角色">
          <el-select v-model="addRoleForm.role" placeholder="选角色" style="width:160px" clearable>
            <el-option
              v-for="o in ROLE_OPTIONS"
              :key="o.value"
              :label="o.label"
              :value="o.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="addRoleForm.role === 'SHELF_ACCOUNT'" label="货架（可多选）">
          <el-select
            v-model="addRoleForm.shelfIds"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            :max-collapse-tags="3"
            placeholder="选 1+ 个货架；留空 = 共享 HMI 通行"
            style="width:340px"
            clearable
          >
            <el-option
              v-for="s in shelfOptions"
              :key="s.id"
              :disabled="boundShelfIds.has(String(s.id))"
              :label="`${s.code} (${s.zone === 'PRODUCTION' ? '生产' : '品检'})`"
              :value="String(s.id)"
            />
          </el-select>
        </el-form-item>
        <el-form-item><el-button @click="doAddRole" :disabled="!addRoleForm.role">添加</el-button></el-form-item>
      </el-form>
      <p v-if="addRoleForm.role === 'SHELF_ACCOUNT' && addRoleForm.shelfIds.length === 0" class="scope-hint">
        <el-icon><InfoFilled /></el-icon>
        <span>货架留空 = 共享工控机（HMI）通行：该账号意图覆盖车间所有 PRODUCTION 架，不绑死单架。</span>
      </p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listUsers, createUser, updateUser, deactivateUser, resetUserPassword, listUserRoles, addUserRole, removeUserRole } from '@/api/users'
import { listShelves } from '@/api/shelves'
import type { UserOut, UserRoleOut } from '@/types/user'
import type { Shelf } from '@/types/shelf'
import { InfoFilled } from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useDialogSize } from '@/composables/useDialogSize'
import { useListStatePersist } from '@/composables/useListFilterPersist'

const { isMobile } = useBreakpoint()
const userDlg = useDialogSize({ desktopWidth: 420 })
const rolesDlg = useDialogSize({ desktopWidth: 560 })
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

const items = ref<UserOut[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const size = ref(20)

// ============ 筛选状态持久化（page 不持久化）============
const { restore: restoreUserFilter, clear: clearUserFilter } = useListStatePersist(
  'user_list',
  { size },
  { exclude: new Set(['page']) },
)

// ============ 列可见性 ============
// 「操作」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'username', label: '用户名' },
  { key: 'full_name', label: '姓名' },
  { key: 'roles', label: '角色' },
  { key: 'is_active', label: '状态' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'user_list' })

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
// 2026-07-13：5 个 role 全量暴露；shelfIds 多选。
// shelfIds 在前端保持字符串：雪花 ID 长度 > 2^53，Number() 会丢精度。
const ROLE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'MANAGER', label: '管理员' },
  { value: 'CLERK', label: '文员' },
  { value: 'SHELF_ACCOUNT', label: '货架一体机账号' },
  { value: 'INSPECTOR', label: '品检员' },
  { value: 'CNC_PROGRAMMER', label: 'CNC 编程员' },
]
const addRoleForm = reactive<{ role: string; shelfIds: string[] }>({ role: '', shelfIds: [] })

// 当前账号已绑的 SHELF_ACCOUNT 货架 id 集合（多选下拉 disabled 防重复绑）
const boundShelfIds = computed<Set<string>>(() => new Set(
  roleList.value
    .filter((r) => r.role === 'SHELF_ACCOUNT' && r.scope_id)
    .map((r) => String(r.scope_id)),
))

async function fetchData() {
  loading.value = true
  try {
    const res = await listUsers({ limit: size.value, offset: (page.value - 1) * size.value })
    items.value = res.items; total.value = res.total
  } finally { loading.value = false }
}

function onPageSizeChange(value: number) {
  size.value = value
  page.value = 1
  void fetchData()
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

async function doReset(id: string) {
  try {
    await resetUserPassword(id)
    ElMessage.success('已重置为默认密码 changeme')
  } catch (e: any) { ElMessage.error(e?.message || '重置失败') }
}

async function openRoles(obj: any) { const u = obj as UserOut;
  roleUser.value = u
  roleList.value = await listUserRoles(String(u.id))
  shelfOptions.value = (await listShelves({ is_active: true, limit: 200 })).items
  addRoleForm.role = ''; addRoleForm.shelfIds = []
  showRoles.value = true
}

async function doAddRole() {
  if (!roleUser.value || !addRoleForm.role) return
  const targetRole = addRoleForm.role  // 捕获，下面的异步调用之后用
  try {
    if (targetRole === 'SHELF_ACCOUNT') {
      // 多货架绑定：循环 N 次 addUserRole（DB 唯一约束天然去重 → 409 提示）
      // 空数组 → 走 scope_id=NULL 通配（共享 HMI 场景）
      if (addRoleForm.shelfIds.length === 0) {
        await addUserRole(String(roleUser.value.id), {
          role: 'SHELF_ACCOUNT', scope_type: 'shelf', scope_id: null,
        })
      } else {
        for (const sid of addRoleForm.shelfIds) {
          await addUserRole(String(roleUser.value.id), {
            role: 'SHELF_ACCOUNT', scope_type: 'shelf', scope_id: sid,
          })
        }
      }
    } else {
      // 非 SHELF_ACCOUNT role（MANAGER/CLERK/INSPECTOR/CNC_PROGRAMMER）：scope 必须 NULL
      await addUserRole(String(roleUser.value.id), {
        role: targetRole, scope_type: null, scope_id: null,
      })
    }
    roleList.value = await listUserRoles(String(roleUser.value.id))
    addRoleForm.shelfIds = []
    ElMessage.success('已添加')
    // 提示 SHELF_ACCOUNT：绑定变更要等 token 自动刷新（最多 12h）或重新登录
    if (targetRole === 'SHELF_ACCOUNT') {
      ElMessageBox.alert(
        '绑定变更已写入。关联 SHELF_ACCOUNT 账号需重新登录或等待 access token 自动刷新（最多 12h）后才能看到新货架范围。',
        '提示',
        { type: 'info' },
      ).catch(() => { /* 用户关掉提示，忽略 */ })
    }
  } catch (e: any) { ElMessage.error(e?.message || '添加失败') }
}

async function removeRole(uid: string, rid: string) { await removeUserRole(uid, rid); roleList.value = await listUserRoles(uid); ElMessage.success('已移除') }

onMounted(() => {
  // 先尝试恢复 localStorage 中的分页大小
  const persisted = restoreUserFilter()
  if (persisted && typeof persisted.size === 'number') {
    size.value = persisted.size
  }
  void fetchData()
})
</script>

<style lang="scss" scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; h2 { margin: 0; font-size: 18px; } }
.no-roles { color: #c0c4cc; font-size: 13px; }
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;

  @include until(sm) {
    justify-content: center;
  }
}
.role-tags {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
}
.scope-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 0;
  padding: 8px 12px;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 6px;
  font-size: 13px;
  color: #b88230;
  line-height: 1.5;
}
</style>
