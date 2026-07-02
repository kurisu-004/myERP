<template>
  <!-- 叶子节点：有 path 且无 children → <el-menu-item> -->
  <el-menu-item v-if="isLeaf" :index="menu.path!">
    <el-icon v-if="iconComp"><component :is="iconComp" /></el-icon>
    <template #title>{{ menu.title }}</template>
  </el-menu-item>

  <!-- 分组节点：path 为 NULL 或有 children → <el-sub-menu> -->
  <el-sub-menu v-else :index="menu.code">
    <template #title>
      <el-icon v-if="iconComp"><component :is="iconComp" /></el-icon>
      <span>{{ menu.title }}</span>
    </template>
    <MenuTreeItem
      v-for="child in menu.children"
      :key="child.id"
      :menu="child"
    />
  </el-sub-menu>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import * as ElIcons from '@element-plus/icons-vue'
import type { MenuNode } from '@/types/menu'

// Vue 3 <script setup> 不会自动注册自身用于递归模板。
// 这里显式按文件路径自引用，让模板里能用 <MenuTreeItem />。
import MenuTreeItem from '@/layouts/components/MenuTreeItem.vue'

const props = defineProps<{ menu: MenuNode }>()

const hasChildren = computed(() => props.menu.children.length > 0)
// 叶子节点：必须有 path 且没有 children。
const isLeaf = computed(() => !!props.menu.path && !hasChildren.value)

// 按名字查 Element-Plus 图标；查不到返回 undefined → 模板里 v-if 隐藏图标（不报错）。
const iconComp = computed(() => {
  const name = props.menu.icon
  if (!name) return undefined
  return (ElIcons as Record<string, unknown>)[name] as never
})
</script>