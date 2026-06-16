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
        unique-opened
      >
        <template v-for="node in displayMenus" :key="node.code">
          <el-sub-menu
            v-if="node.children && node.children.length > 0"
            :index="node.code"
          >
            <template #title>
              <el-icon v-if="node.icon"><component :is="iconOf(node.icon)" /></el-icon>
              <span>{{ node.name }}</span>
            </template>
            <el-menu-item
              v-for="child in node.children"
              :key="child.code"
              :index="child.path || child.code"
            >
              <el-icon v-if="child.icon"><component :is="iconOf(child.icon)" /></el-icon>
              <template #title>{{ child.name }}</template>
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="node.path || node.code">
            <el-icon v-if="node.icon"><component :is="iconOf(node.icon)" /></el-icon>
            <template #title>{{ node.name }}</template>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 右侧顶部栏 -->
      <el-header class="header">
        <div class="header-left">
          <el-button link class="collapse-btn" @click="toggleCollapse">
            <el-icon :size="20">
              <Fold v-if="!isCollapse" />
              <Expand v-else />
            </el-icon>
          </el-button>

          <el-breadcrumb separator="/" class="breadcrumb">
            <el-breadcrumb-item
              v-for="(item, idx) in breadcrumbItems"
              :key="idx"
            >
              {{ item }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="header-right">
          <el-tooltip content="刷新" placement="bottom">
            <el-button link @click="reload">
              <el-icon :size="18"><Refresh /></el-icon>
            </el-button>
          </el-tooltip>

          <el-tag v-if="auth.isSuperadmin" type="danger" effect="dark" size="small">
            SUPERADMIN
          </el-tag>
          <el-tag v-else type="info" effect="plain" size="small">
            {{ auth.user?.name || 'USER' }}
          </el-tag>

          <el-dropdown trigger="click" @command="handleUserCmd">
            <div class="user-info">
              <el-avatar :size="32" class="user-avatar">
                {{ initial }}
              </el-avatar>
              <span class="user-name">{{ auth.user?.name || '未登录' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon>个人信息
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main-content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, ref, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as ElIcons from '@element-plus/icons-vue'
import {
  Box,
  Fold,
  Expand,
  Refresh,
  ArrowDown,
  User,
  SwitchButton,
} from '@element-plus/icons-vue'
import { useAuthStore } from '@/store/auth'
import type { MenuNode } from '@/types/rbac'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const isCollapse = ref(false)

const initial = computed<string>(() => {
  const n = auth.user?.name || ''
  return n ? n.slice(0, 1) : 'U'
})

const activeMenu = computed<string>(() => route.path)

// 动态菜单: 超管看全部静态菜单, 普通用户按后端下发的 menus 过滤
const allStaticMenus: MenuNode[] = [
  {
    id: 'static-dashboard',
    code: 'dashboard:menu',
    name: '首页',
    path: '/dashboard',
    icon: 'House',
    component: null,
    sort_order: 0,
    children: [],
  },
  {
    id: 'static-parts',
    code: 'parts:menu',
    name: '基础数据',
    path: null,
    icon: 'Document',
    component: null,
    sort_order: 10,
    children: [
      {
        id: 'static-parts-list',
        code: 'parts:list',
        name: '零件一览',
        path: '/parts',
        icon: 'Box',
        component: null,
        sort_order: 11,
        children: [],
      },
    ],
  },
  {
    id: 'static-rbac',
    code: 'rbac:menu',
    name: '系统管理',
    path: null,
    icon: 'Setting',
    component: null,
    sort_order: 900,
    children: [
      {
        id: 'static-rbac-user',
        code: 'rbac:user:list',
        name: '用户管理',
        path: '/rbac/users',
        icon: 'User',
        component: null,
        sort_order: 901,
        children: [],
      },
      {
        id: 'static-rbac-role',
        code: 'rbac:role:list',
        name: '角色管理',
        path: '/rbac/roles',
        icon: 'UserFilled',
        component: null,
        sort_order: 902,
        children: [],
      },
      {
        id: 'static-rbac-perm',
        code: 'rbac:permission:list',
        name: '菜单权限',
        path: '/rbac/permissions',
        icon: 'Key',
        component: null,
        sort_order: 903,
        children: [],
      },
    ],
  },
]

const displayMenus = computed<MenuNode[]>(() => {
  if (auth.isSuperadmin) {
    return allStaticMenus
  }
  // 非超管: 用后端 menus 数组 (从 /auth/me 拿),但是要按 path 找对应的菜单模板
  // 简化: 直接用后端返回的 menus
  return auth.menus
})

function iconOf(name: string | null | undefined): Component | null {
  if (!name) return null
  const iconMap = ElIcons as unknown as Record<string, Component>
  return iconMap[name] || null
}

const breadcrumbItems = computed<string[]>(() => {
  return (route.meta?.breadcrumb as string[] | undefined) || [route.meta?.title || '首页']
})

const toggleCollapse = (): void => {
  isCollapse.value = !isCollapse.value
}

const reload = (): void => {
  ElMessage.success('刷新成功')
  router.go(0)
}

const handleUserCmd = async (cmd: string | number | object): Promise<void> => {
  const command = cmd as string
  if (command === 'logout') {
    try {
      await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      })
    } catch {
      return
    }
    auth.logout()
    ElMessage.success('已退出登录')
    router.push('/login')
  } else if (command === 'profile') {
    ElMessage.info('个人信息')
  }
}
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
  border-right: none;
  background-color: var(--sidebar-bg);
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
    color: #fff;
    font-weight: 600;
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
