<template>
  <el-container class="main-layout">
    <!-- 左侧菜单栏 -->
    <el-aside :width="isCollapse ? '64px' : '220px'" class="sidebar">
      <div class="logo">
        <el-icon class="logo-icon"><Box /></el-icon>
        <span v-show="!isCollapse" class="logo-text">myERP</span>
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapse"
        :collapse-transition="false"
        background-color="var(--sidebar-bg)"
        text-color="var(--sidebar-text)"
        active-text-color="var(--sidebar-text-active)"
        class="sidebar-menu"
        router
      >
        <!-- 菜单来自后端 t_menu + t_role_menu（登录时拉回，存 localStorage）。
             菜单项定义见 MainLayout.vue 之外的 @/layouts/components/MenuTreeItem.vue
             —— 它递归渲染 <el-sub-menu> 与 <el-menu-item>。 -->
        <template v-if="menuList.length > 0">
          <MenuTreeItem v-for="m in menuList" :key="m.id" :menu="m" />
        </template>
        <div v-else class="sidebar-empty">暂无可用菜单</div>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 右侧顶部栏 -->
      <el-header class="header">
        <div class="header-left">
          <el-button
            link
            class="collapse-btn"
            @click="toggleCollapse"
          >
            <el-icon :size="20">
              <Fold v-if="!isCollapse" />
              <Expand v-else />
            </el-icon>
          </el-button>

          <el-breadcrumb separator="/" class="breadcrumb">
            <el-breadcrumb-item
              v-for="(item, idx) in breadcrumbItems"
              :key="idx"
              :to="item.to"
            >
              {{ item.label }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="header-right">
          <el-tooltip content="刷新" placement="bottom">
            <el-button link @click="reload">
              <el-icon :size="18"><Refresh /></el-icon>
            </el-button>
          </el-tooltip>

          <el-dropdown trigger="click" @command="handleUserCmd">
            <div class="user-info">
              <el-avatar :size="32" class="user-avatar" />
              <span class="user-name">{{ userInfo.name }}</span>
              <el-icon><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="change-password">
                  <el-icon><Lock /></el-icon>修改密码
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 主要内容区 -->
      <el-main class="main-content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>

    <!-- 修改密码弹窗 -->
    <el-dialog v-model="showChangePwd" title="修改密码" width="420px" @closed="resetPwdForm">
      <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-width="90px">
        <el-form-item label="原密码" prop="oldPassword">
          <el-input v-model="pwdForm.oldPassword" type="password" show-password placeholder="请输入原密码" />
        </el-form-item>
        <el-form-item label="新密码" prop="newPassword">
          <el-input v-model="pwdForm.newPassword" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirmPassword">
          <el-input v-model="pwdForm.confirmPassword" type="password" show-password placeholder="再次输入新密码" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showChangePwd = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="submitChangePwd">确定</el-button>
      </template>
    </el-dialog>

    <!-- 全局业务事件横幅：Teleport 到 body，右上角浮层 -->
    <NotificationBanner />
  </el-container>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  Box, Fold, Expand, Refresh, ArrowDown, Lock, SwitchButton,
} from '@element-plus/icons-vue'
import { useAuthSession } from '@/composables/useAuthSession'
import { me as apiMe, changeMyPassword } from '@/api/auth'
import MenuTreeItem from '@/layouts/components/MenuTreeItem.vue'
import type { CurrentUser } from '@/types/user'

type UserCmd = 'change-password' | 'logout'

const route = useRoute()
const router = useRouter()

const isCollapse = ref(false)
const currentUser = ref<CurrentUser | null>(null)
const { logout, menus } = useAuthSession()

const menuList = computed(() => menus())

const userInfo = computed(() => ({ name: currentUser.value?.full_name || currentUser.value?.username || '未登录' }))

const activeMenu = computed<string>(() => route.path)

const breadcrumbItems = computed<{ label: string; to?: string }[]>(() => {
  const raw = route.meta?.breadcrumb ?? []
  const list = raw.length > 0 ? raw : [{ label: route.meta?.title || '首页' }]
  return list.map((it, idx, arr) => ({
    label: it.label,
    to: idx === arr.length - 1 || !it.path ? undefined : it.path,
  }))
})

const toggleCollapse = (): void => { isCollapse.value = !isCollapse.value }

const reload = (): void => { ElMessage.success('刷新成功'); router.go(0) }

const handleUserCmd = async (cmd: string | number | object): Promise<void> => {
  const command = cmd as UserCmd
  if (command === 'logout') {
    try {
      await ElMessageBox.confirm('确定要退出登录吗？', '提示', { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' })
      await logout()
      ElMessage.success('已退出登录')
      router.replace('/login')
    } catch { /* cancelled */ }
  } else if (command === 'change-password') {
    showChangePwd.value = true
  }
}

// ---- 修改密码 ----
const showChangePwd = ref(false)
const pwdSaving = ref(false)
const pwdFormRef = ref<FormInstance>()
const pwdForm = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })

const validateNewPwd = (_rule: unknown, value: string, callback: (err?: Error) => void): void => {
  if (!value) return callback(new Error('请输入新密码'))
  if (value.length < 6) return callback(new Error('新密码至少 6 位'))
  if (value === pwdForm.oldPassword) return callback(new Error('新密码不能与原密码相同'))
  // 新密码变化时，若确认框已填，重新触发确认框校验
  if (pwdForm.confirmPassword) pwdFormRef.value?.validateField('confirmPassword')
  callback()
}
const validateConfirmPwd = (_rule: unknown, value: string, callback: (err?: Error) => void): void => {
  if (!value) return callback(new Error('请再次输入新密码'))
  if (value !== pwdForm.newPassword) return callback(new Error('两次输入的新密码不一致'))
  callback()
}
const pwdRules: FormRules = {
  oldPassword: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  newPassword: [{ validator: validateNewPwd, trigger: 'blur' }],
  confirmPassword: [{ validator: validateConfirmPwd, trigger: 'blur' }],
}

function resetPwdForm(): void {
  pwdForm.oldPassword = ''
  pwdForm.newPassword = ''
  pwdForm.confirmPassword = ''
  pwdFormRef.value?.clearValidate()
}

async function submitChangePwd(): Promise<void> {
  const valid = await pwdFormRef.value?.validate().catch(() => false)
  if (!valid) return
  pwdSaving.value = true
  try {
    await changeMyPassword({ old_password: pwdForm.oldPassword, new_password: pwdForm.newPassword })
    showChangePwd.value = false
    ElMessage.success('密码已修改，请重新登录')
    await logout()
    router.replace('/login')
  } catch (e: any) {
    ElMessage.error(e?.message || '修改密码失败')
  } finally {
    pwdSaving.value = false
  }
}

onMounted(async () => {
  try { currentUser.value = await apiMe() } catch { router.replace('/login') }
})
</script>

<style lang="scss" scoped>
.main-layout {
  height: 100vh;
}

.sidebar {
  background-color: var(--sidebar-bg);
  transition: width 0.3s;
  overflow: hidden;
  box-shadow: 2px 0 6px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #fff;
  font-size: 20px;
  font-weight: 600;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  background-color: #142d54;

  .logo-icon {
    font-size: 24px;
    color: #7eb0e3;
  }

  .logo-text {
    letter-spacing: 1px;
  }
}

.sidebar-menu {
  flex: 1;
  min-height: 0; /* 让 flex 项可收缩至内容尺寸以下,否则溢出时不会触发滚动 */
  overflow-y: auto; /* 菜单项超出可视高度时纵向滚动 */
  border-right: none;
  background-color: var(--sidebar-bg);
}

.sidebar-menu::-webkit-scrollbar {
  width: 6px;
}

.sidebar-menu::-webkit-scrollbar-thumb {
  background-color: rgba(255, 255, 255, 0.2);
  border-radius: 3px;
}

.sidebar-menu::-webkit-scrollbar-thumb:hover {
  background-color: rgba(255, 255, 255, 0.35);
}

.sidebar-menu::-webkit-scrollbar-track {
  background-color: transparent;
}

.sidebar-empty {
  color: var(--sidebar-text);
  opacity: 0.6;
  text-align: center;
  padding: 24px 8px;
  font-size: 13px;
}

:deep(.el-menu-item:hover),
:deep(.el-sub-menu__title:hover) {
  background-color: var(--sidebar-hover) !important;
}

:deep(.el-menu-item.is-active) {
  background-color: var(--sidebar-active-bg) !important;
  color: #fff !important;
  border-left: 3px solid #4a8fd6;
}

:deep(.el-sub-menu .el-menu-item) {
  background-color: #142d54 !important;
  min-width: 220px;
}

.header {
  background-color: var(--header-bg);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  box-shadow: var(--shadow-sm);
  height: 60px;
  z-index: 10;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.collapse-btn {
  font-size: 20px;
  color: var(--text-regular);
  padding: 4px;

  &:hover {
    color: var(--primary-color);
  }
}

.breadcrumb {
  font-size: 14px;

  :deep(.el-breadcrumb__item:last-child .el-breadcrumb__inner) {
    color: var(--primary-color);
    font-weight: 500;
  }
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  transition: background 0.2s;

  &:hover {
    background-color: var(--primary-bg);
  }

  .user-avatar {
    background-color: var(--primary-light);
  }

  .user-name {
    font-size: 14px;
    color: var(--text-primary);
  }
}

.main-content {
  background-color: var(--content-bg);
  padding: 16px;
  overflow: auto;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
