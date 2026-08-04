<!--
  HmiPickerCard.vue

  共享 HMI 触摸友好大卡片（2026-07-17 创建，2026-07-17 移除「推荐」视觉提示）。
  视觉规范沿用 ShelfPickerCard：min-height 140px、3px 实线边框、12px 圆角、选中绿、
  active scale(0.98)、-webkit-tap-highlight-color: transparent。

  用于：
  - ProcessPickerDialog 的工序大按钮（kind='process'）
  - ShelfPickerDialog 的货架大按钮（kind='shelf'，视觉代替原 ShelfPickerCard）

  props:
    kind: 'process' | 'shelf'                       必填，决定卡内字段
    code: string                                     主标题（架号 / 工序代码），mono 字体
    name: string                                     副标题（中文名）
    isSelected: boolean                              选中态（绿色边框 + 渐变背景）

    # kind='process' 时必填
    category?: 'INHOUSE' | 'OUTSOURCE'

    # kind='shelf' 时必填
    location?: string                                货架物理位置
    currentLoad?: number                             在架件数
    mappedProcessCodes?: readonly string[]           货架映射工序 chips

  emits:
    select()                                         点选 / Enter / Space 触发
-->
<template>
  <div
    class="hmi-card"
    :class="{ 'is-selected': isSelected, 'is-disabled': disabled }"
    role="button"
    tabindex="0"
    :aria-disabled="disabled || undefined"
    @click="onClick"
    @keydown.enter="onClick"
    @keydown.space.prevent="onClick"
  >
    <div class="card-header">
      <span class="code">{{ code }}</span>
    </div>
    <div class="name">{{ name }}</div>

    <!-- 工序卡：显示 PROCESS_CATEGORY_LABEL（自产/外协） -->
    <el-tag
      v-if="kind === 'process'"
      :type="category === 'INHOUSE' ? 'primary' : 'warning'"
      size="small"
      effect="plain"
      class="category-tag"
    >
      {{ category === 'INHOUSE' ? '自产' : '外协' }}
    </el-tag>

    <!-- 货架卡：在架件数 + 映射工序 chips -->
    <template v-if="kind === 'shelf'">
      <div v-if="location" class="location">{{ location }}</div>
      <div class="load">
        <el-icon><Box /></el-icon>
        <span>在架 <strong>{{ currentLoad }}</strong> 件</span>
      </div>
      <div
        v-if="mappedProcessCodes && mappedProcessCodes.length"
        class="processes"
      >
        <el-tag
          v-for="c in mappedProcessCodes"
          :key="c"
          type="info"
          size="small"
          effect="plain"
          class="process-chip"
        >
          {{ c }}
        </el-tag>
      </div>
    </template>

    <!-- 禁用提示（2026-08-05：放回禁选自身工序场景） -->
    <div v-if="hint" class="hmi-picker-hint">{{ hint }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 通用 HMI 大按钮卡。
 *
 * 视觉规范：与 ShelfPickerCard.vue（2026-07-10）像素级一致；本组件是 ShelfPickerCard
 * 的 kind='process' 扩展形态，kind='shelf' 是 ShelfPickerCard 视觉部分的等价实现。
 *
 * 2026-07-17 移除：原 isRecommended prop（橙边框 + 渐变 + 「推荐」徽章）。流程不再做
 * 自动推荐；工人点选 free。
 *
 * 选中态：调用方传 isSelected=true（通常由 selectedId 比较卡 id 得出）。
 *
 * 2026-08-05：新增 disabled/hint props，支持 ProcessPickerDialog 排除指定工序
 *（不隐藏：工人能看到工序存在但不可选，避免困惑）。
 */
import { Box } from '@element-plus/icons-vue'

const props = defineProps<{
  kind: 'process' | 'shelf'
  code: string
  name: string
  isSelected: boolean
  /** 禁用态：半透明 + 不可点击/键盘激活（pointer-events:none + onClick 短路） */
  disabled?: boolean
  /** 禁用原因展示在卡内底部（红字小字） */
  hint?: string

  /** kind='process' */
  category?: 'INHOUSE' | 'OUTSOURCE'

  /** kind='shelf' */
  location?: string
  currentLoad?: number
  mappedProcessCodes?: readonly string[]
}>()

const emit = defineEmits<{
  select: []
}>()

function onClick(): void {
  if (props.disabled) return
  emit('select')
}
</script>

<style lang="scss" scoped>
/* 沿用 ShelfPickerCard 的视觉规范：min-height 140px、3px 边框、12px 圆角 */
.hmi-card {
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
  &.is-selected {
    border-color: #67c23a;
    background: linear-gradient(135deg, #f0f9eb 0%, #fff 60%);
    box-shadow: 0 4px 16px rgba(103, 194, 58, 0.25);
  }
  /* 禁用：半透明 + 不可点；键盘激活仍走 onClick 短路保证语义一致 */
  &.is-disabled {
    opacity: 0.5;
    cursor: not-allowed;
    pointer-events: none;
  }
}

.hmi-picker-hint {
  font-size: 12px;
  color: #f56c6c;
  margin-top: 4px;
  font-weight: 600;
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
.name {
  font-size: 16px;
  color: #303133;
  font-weight: 500;
}
.category-tag {
  align-self: flex-start;
  font-size: 13px;
  font-weight: 600;
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
