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
        <el-menu-item index="/dashboard">
          <el-icon><House /></el-icon>
          <template #title>首页</template>
        </el-menu-item>

        <el-sub-menu index="base">
          <template #title>
            <el-icon><Document /></el-icon>
            <span>基础数据</span>
          </template>
          <el-menu-item index="/parts">
            <el-icon><Box /></el-icon>
            <template #title>零件一览</template>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="sales">
          <template #title>
            <el-icon><Money /></el-icon>
            <span>销售管理</span>
          </template>
          <el-menu-item index="/sales/order">
            <el-icon><Tickets /></el-icon>
            <template #title>销售订单</template>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="purchase">
          <template #title>
            <el-icon><ShoppingCart /></el-icon>
            <span>采购管理</span>
          </template>
          <el-menu-item index="/purchase/order">
            <el-icon><Tickets /></el-icon>
            <template #title>采购订单</template>
          </el-menu-item>
        </el-sub-menu>
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

          <el-dropdown trigger="click" @command="handleUserCmd">
            <div class="user-info">
              <el-avatar :size="32" :src="userAvatar" class="user-avatar" />
              <span class="user-name">{{ userInfo.name }}</span>
              <el-icon><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon>个人信息
                </el-dropdown-item>
                <el-dropdown-item command="settings">
                  <el-icon><Setting /></el-icon>系统设置
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
  </el-container>
</template>

<script setup lang="ts">
import { ref, computed, type Component as VueComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Fold, Expand, Refresh, ArrowDown, User, Setting, SwitchButton } from '@element-plus/icons-vue'

type UserCmd = 'profile' | 'settings' | 'logout'

interface UserInfo {
  name: string
}

const route = useRoute()
const router = useRouter()

const isCollapse = ref(false)

const userInfo = ref<UserInfo>({ name: '管理员' })
const userAvatar = ref<string>(
  'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png'
)

const activeMenu = computed<string>(() => route.path)

const breadcrumbItems = computed<string[]>(() => {
  return route.meta?.breadcrumb || [route.meta?.title || '首页']
})

const toggleCollapse = (): void => {
  isCollapse.value = !isCollapse.value
}

const reload = (): void => {
  ElMessage.success('刷新成功')
  router.go(0)
}

const handleUserCmd = (cmd: string | number | object): void => {
  const command = cmd as UserCmd
  if (command === 'logout') {
    ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
      .then(() => ElMessage.success('已退出登录'))
      .catch(() => undefined)
  } else if (command === 'profile') {
    ElMessage.info('个人信息')
  } else if (command === 'settings') {
    ElMessage.info('系统设置')
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
