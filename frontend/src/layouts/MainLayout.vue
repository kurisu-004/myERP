<template>
  <el-container class="main-layout">
    <!-- 左侧菜单栏（≥md 持久显示；<md 收进抽屉） -->
    <el-aside v-if="!isMobile" :width="isCollapse ? '64px' : '220px'" class="sidebar">
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
        <!-- 手机：左侧 = 个人信息；右侧 = 汉堡（右手拇指易触达） -->
        <!-- 桌面：左侧 = 折叠按钮 + 面包屑；右侧 = 刷新 + 个人信息 -->
        <div class="header-left" :class="{ 'header-left--mobile': isMobile }">
          <template v-if="isMobile">
            <el-dropdown trigger="click" @command="handleUserCmd" class="header-user-dd">
              <div class="user-info">
                <el-avatar :size="32" class="user-avatar" />
                <el-icon class="user-caret"><ArrowDown /></el-icon>
              </div>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item disabled>
                    {{ userInfo.name }}
                  </el-dropdown-item>
                  <el-dropdown-item divided command="change-password">
                    <el-icon><Lock /></el-icon>修改密码
                  </el-dropdown-item>
                  <el-dropdown-item command="logout">
                    <el-icon><SwitchButton /></el-icon>退出登录
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <span class="page-title">{{ pageTitle }}</span>
          </template>
          <template v-else>
            <el-button
              link
              class="collapse-btn"
              @click="onNavToggle"
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
          </template>
        </div>

        <div class="header-right">
          <template v-if="!isMobile">
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
          </template>

          <!-- 手机：右侧只放汉堡（拇指易触达） -->
          <el-button
            v-if="isMobile"
            link
            class="collapse-btn collapse-btn--mobile"
            @click="onNavToggle"
          >
            <el-icon :size="22"><Menu /></el-icon>
          </el-button>
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
    <el-dialog
      v-model="showChangePwd"
      title="修改密码"
      :width="pwdDlg.width.value"
      :top="pwdDlg.top.value"
      @closed="resetPwdForm"
    >
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

    <!-- 手机侧栏抽屉（<md，从右侧滑出与顶栏右侧汉堡按钮对齐） -->
    <el-drawer
      v-if="isMobile"
      v-model="mobileNavOpen"
      direction="rtl"
      :size="260"
      :with-header="false"
      append-to-body
      class="mobile-nav-drawer"
    >
      <div class="logo">
        <el-icon class="logo-icon"><Box /></el-icon>
        <span class="logo-text">myERP</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        background-color="var(--sidebar-bg)"
        text-color="var(--sidebar-text)"
        active-text-color="var(--sidebar-text-active)"
        class="sidebar-menu"
        menu-trigger="click"
        router
        @select="onMenuSelect"
      >
        <template v-if="menuList.length > 0">
          <MenuTreeItem v-for="m in menuList" :key="m.id" :menu="m" />
        </template>
        <div v-else class="sidebar-empty">暂无可用菜单</div>
      </el-menu>
    </el-drawer>

    <!-- 全局业务事件横幅：Teleport 到 body，右上角浮层 -->
    <NotificationBanner />
  </el-container>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  Box, Fold, Expand, Menu, Refresh, ArrowDown, Lock, SwitchButton,
} from '@element-plus/icons-vue'
import { useAuthSession } from '@/composables/useAuthSession'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'
import { me as apiMe, changeMyPassword } from '@/api/auth'
import MenuTreeItem from '@/layouts/components/MenuTreeItem.vue'
import type { CurrentUser } from '@/types/user'

type UserCmd = 'change-password' | 'logout'

const route = useRoute()
const router = useRouter()

const { isMobile } = useBreakpoint()

const isCollapse = ref(false)
const mobileNavOpen = ref(false)
const currentUser = ref<CurrentUser | null>(null)
const { logout, menus } = useAuthSession()

const menuList = computed(() => menus())

const userInfo = computed(() => ({ name: currentUser.value?.full_name || currentUser.value?.username || '未登录' }))

const activeMenu = computed<string>(() => route.path)

const pageTitle = computed<string>(() => route.meta?.title || 'myERP')

const breadcrumbItems = computed<{ label: string; to?: string }[]>(() => {
  const raw = route.meta?.breadcrumb ?? []
  const list = raw.length > 0 ? raw : [{ label: route.meta?.title || '首页' }]
  return list.map((it, idx, arr) => ({
    label: it.label,
    to: idx === arr.length - 1 || !it.path ? undefined : it.path,
  }))
})

// 顶栏按钮：手机开抽屉；桌面切折叠
const onNavToggle = (): void => {
  if (isMobile.value) mobileNavOpen.value = !mobileNavOpen.value
  else isCollapse.value = !isCollapse.value
}
// 抽屉内点菜单项后关闭
const onMenuSelect = (): void => { mobileNavOpen.value = false }

// 路由变化 / 切回桌面时收起抽屉
watch(() => route.fullPath, () => { mobileNavOpen.value = false })
watch(isMobile, (m) => { if (!m) mobileNavOpen.value = false })

// 修改密码弹窗尺寸
const pwdDlg = useDialogSize({ desktopWidth: 420 })

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

  @include until(md) {
    padding: 12px;
  }
  @include until(sm) {
    padding: 8px;
  }
}

/* 手机顶栏更紧凑 */
@include until(sm) {
  .header {
    padding: 0 12px;
  }
  .header-left {
    gap: 8px;
    min-width: 0;
  }
  .header-right {
    gap: 4px;
  }
  /* 手机隐藏用户名，仅留头像 */
  .user-info .user-name {
    display: none;
  }
  .user-info {
    padding: 4px;
  }
}

.page-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 手机侧栏抽屉：抽屉体去内边距，沿用侧栏配色（整块深色填满，无白边） */
.mobile-nav-drawer {
  /* 抽屉外层与抽屉框都设为 100% 高度，避免它只按内容尺寸 */
  :deep(.el-overlay),
  :deep(.el-drawer),
  :deep(.el-drawer__rtl),
  :deep(.el-drawer__ltr) {
    height: 100% !important;
  }
  :deep(.el-drawer) {
    background-color: var(--sidebar-bg);
  }
  :deep(.el-drawer__body) {
    padding: 0;
    background-color: var(--sidebar-bg);
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }
}

/* 抽屉内 menu 占满剩余高度（避免下方白边） */
.mobile-nav-drawer {
  :deep(.sidebar-menu),
  :deep(.el-menu--vertical),
  :deep(.el-menu--vertical > ul[role="menubar"]) {
    flex: 1 1 auto;
    min-height: 0;
    border-right: none;
    width: 100%;
  }
  :deep(.el-menu-item),
  :deep(.el-sub-menu__title) {
    width: 100%;
  }
  /* 填满整个 drawer，去除 Element Plus 默认边框 / 外距 / 内边距（消除白色边） */
  :deep(.el-menu),
  :deep(.el-menu--vertical) {
    border: none !important;
  }
  :deep(.el-menu--vertical > ul[role="menubar"]) {
    margin: 0 !important;
    padding: 0 !important;
  }
  /* 嵌套子菜单展开后的子级 ul：去边框 / 外距，背景与侧栏深色保持一致 */
  :deep(.el-sub-menu .el-menu) {
    border: none !important;
    margin: 0 !important;
    background-color: #142d54 !important;
  }
}

/* 手机 header 翻转：左侧 user，右侧汉堡 */
.header-left--mobile {
  flex-direction: row-reverse; /* 头像放最左；title 紧跟其后 */
  gap: 10px;
}
.header-left--mobile .header-user-dd {
  display: inline-flex;
}
.header-left--mobile .page-title {
  flex: 1;
  min-width: 0; /* 让 ellipsis 生效 */
  font-size: 15px;
}
.user-caret {
  font-size: 12px;
  color: var(--text-secondary);
}
.collapse-btn--mobile {
  font-size: 22px;
  color: var(--text-regular);
  padding: 4px 6px;
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
