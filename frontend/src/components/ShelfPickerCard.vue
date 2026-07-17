<!--
  ShelfPickerCard.vue

  @deprecated 2026-07-17：视觉部分已迁到 HmiPickerCard.vue（kind='shelf'）。
  本文件保留作为 fallback；ShelfPickerDialog 现在消费 HmiPickerCard，
  旧 props 接口 shelf/isSelected 已不再被引用。下个 sprint 整体迁移完成后删除本文件。

  历史背景（2026-07-10）：共享 HMI RETURN 卡片网格 picker 的单张卡片。
  - 大卡片：≥ 160×120 px（手套触屏友好）
  - 显示架号、名称、在架件数、映射工序 chips
  - 推荐状态：橙色边框 + ✓ 推荐 徽章
  - 选中状态：绿色背景 + 边框

  原 props:
    shelf: ShelfForReturn
    isSelected: boolean
  原 emits:
    select(shelf.id)
-->
<template>
  <div
    class="shelf-card"
    :class="{
      'is-recommended': shelf.is_recommended,
      'is-selected': isSelected,
    }"
    role="button"
    tabindex="0"
    @click="onClick"
    @keydown.enter="onClick"
    @keydown.space.prevent="onClick"
  >
    <div class="card-header">
      <span class="code">{{ shelf.code }}</span>
      <el-tag
        v-if="shelf.is_recommended"
        type="warning"
        size="default"
        effect="dark"
        class="recommended-tag"
      >
        <el-icon><StarFilled /></el-icon>
        <span>推荐</span>
      </el-tag>
    </div>
    <div class="name">{{ shelf.name }}</div>
    <div v-if="shelf.location" class="location">{{ shelf.location }}</div>
    <div class="load">
      <el-icon><Box /></el-icon>
      <span>在架 <strong>{{ shelf.current_load }}</strong> 件</span>
    </div>
    <div v-if="shelf.mapped_process_codes.length" class="processes">
      <el-tag
        v-for="code in shelf.mapped_process_codes"
        :key="code"
        type="info"
        size="small"
        effect="plain"
        class="process-chip"
      >
        {{ code }}
      </el-tag>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Box, StarFilled } from '@element-plus/icons-vue'
import type { ShelfForReturn } from '@/types/shelf'

const props = defineProps<{
  shelf: ShelfForReturn
  isSelected: boolean
}>()

const emit = defineEmits<{
  select: [shelfId: string]
}>()

function onClick(): void {
  emit('select', props.shelf.id)
}
</script>

<style lang="scss" scoped>
.shelf-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 18px;
  background: #fff;
  border: 3px solid #e4e7ed;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
  min-height: 140px;
  user-select: none;
  // 手套触屏：放大点击区
  -webkit-tap-highlight-color: transparent;
  &:hover {
    border-color: #409eff;
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(64, 158, 255, 0.15);
  }
  &:active {
    transform: scale(0.98);
  }
  &.is-recommended {
    border-color: #e6a23c;
    background: linear-gradient(135deg, #fdf6ec 0%, #fff 60%);
    box-shadow: 0 2px 8px rgba(230, 162, 60, 0.2);
  }
  &.is-selected {
    border-color: #67c23a;
    background: linear-gradient(135deg, #f0f9eb 0%, #fff 60%);
    box-shadow: 0 4px 16px rgba(103, 194, 58, 0.25);
  }
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.code {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 20px;
  font-weight: 700;
  color: #303133;
  letter-spacing: 1px;
}
.recommended-tag {
  font-size: 14px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.name {
  font-size: 16px;
  color: #303133;
  font-weight: 500;
}
.location {
  font-size: 13px;
  color: #909399;
}
.load {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #606266;
  strong {
    color: #409eff;
    font-size: 18px;
    font-weight: 700;
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    margin: 0 2px;
  }
}
.processes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}
.process-chip {
  font-size: 12px;
}
</style>
