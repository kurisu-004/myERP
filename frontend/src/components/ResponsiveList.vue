<!--
  ResponsiveList.vue

  响应式列表容器：
  - ≥md（平板/桌面）：渲染 <el-table>，默认插槽承载既有 <el-table-column>（桌面端零改动，
    表头 popover 筛选 / 排序 / 固定列 / 选择列全部原样保留）。所有 el-table 的 props/事件
    通过 $attrs 透传（stripe / border / size / default-sort / row-class-name /
    @sort-change / @selection-change ...）。
  - <md（手机）：不渲染表格，改为卡片流，由 #card 作用域插槽 { row, index } 定制每张卡片。

  统一处理 loading（v-loading）与空态（emptyText → el-empty），故各视图不再需要
  el-table 上的 #empty 插槽。
-->
<template>
  <div class="responsive-list">
    <!-- 桌面：表格 -->
    <div v-if="!isMobile" v-loading="loading" class="rl-table-wrap">
      <div v-if="$slots.toolbar" class="rl-toolbar">
        <slot name="toolbar" />
      </div>
      <el-table
        ref="elTableRef"
        :data="items"
        :row-key="rowKey"
        :max-height="maxHeight"
        highlight-current-row
        style="width: 100%"
        v-bind="$attrs"
        @row-dblclick="(row, column, event) => emit('row-dblclick', row, column, event)"
      >
        <slot />
        <template #empty>
          <el-empty :description="emptyText" />
        </template>
      </el-table>
    </div>

    <!-- 手机：卡片流 -->
    <div v-else v-loading="loading" class="rl-cards">
      <el-card
        v-for="(row, index) in items"
        :key="rowKeyOf(row, index)"
        shadow="never"
        class="rl-card"
        :class="cardClassOf(row)"
        @click="emit('card-click', row, index)"
      >
        <slot name="card" :row="row" :index="index" />
      </el-card>
      <el-empty v-if="!loading && items.length === 0" :description="emptyText" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { TableInstance } from 'element-plus'
import { useBreakpoint } from '@/composables/useBreakpoint'

defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    items: any[]
    loading?: boolean
    rowKey?: string | ((row: any) => string)
    emptyText?: string
    /** 卡片额外 class：字符串或按行计算（如加急高亮） */
    cardClass?: string | ((row: any) => string)
    /** 表格最大高度；触发表头 sticky 的滚动容器。string|number 都接受
     *  （Element Plus 2.14.x max-height prop）。默认 `calc(100vh - 280px)` —
     *  估算 MainLayout 顶栏 + filter card + 分页器等 chrome。 */
    maxHeight?: string | number
  }>(),
  {
    loading: false,
    rowKey: 'id',
    emptyText: '暂无数据',
    cardClass: '',
    maxHeight: 'calc(100vh - 280px)',
  },
)

const emit = defineEmits<{
  (e: 'card-click', row: any, index: number): void
  // 2026-07-24：透传 el-table 的 row-dblclick 事件，让父组件（如 PartsList /
  // AssemblyList）使用 useRowEditor composable 处理双击行进入编辑。
  (e: 'row-dblclick', row: any, column: any, event: MouseEvent): void
}>()

const { isMobile } = useBreakpoint()

function rowKeyOf(row: any, index: number): string | number {
  if (typeof props.rowKey === 'function') {
    return String(props.rowKey(row) ?? index)
  }
  const k = row?.[props.rowKey]
  return k ?? index
}

function cardClassOf(row: any): string {
  return typeof props.cardClass === 'function' ? props.cardClass(row) : props.cardClass
}

// 2026-07-22：暴露内部 el-table ref，父组件（PartsList）批量模式需调
// toggleRowSelection / clearSelection 等实例方法。手机端不渲染表格，ref 为 null。
const elTableRef = ref<TableInstance | null>(null)
defineExpose({ elTableRef })
</script>

<style lang="scss" scoped>
.rl-table-wrap {
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  padding: 4px;
  overflow-x: auto;
}

.rl-toolbar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  padding: 4px 6px 8px;
  min-height: 32px;
}

.rl-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 60px;
}

.rl-card {
  :deep(.el-card__body) {
    padding: 12px 14px;
  }
}

// 2026-08-01：加急行 / 已开送货单行 用了 !important 强染色 (#fde2e2 / 默认蓝),
// Element Plus 的 .current-row 浅蓝高亮被覆盖看不出点击态。
// 这里集中覆盖 .current-row 在状态色行上的色为「更深的同色」,既保留状态色又显示高亮。
// 2026-08-01 再次更新：全局默认 .current-row 已是 light-8 (#cce0f4)，note-row
// 选中色对应加深到 light-7 (#b3d0ee) 保持区分度。
:deep(.el-table__row.row-urgent.current-row > td.el-table__cell) {
  background-color: #fbcaca !important;
}
:deep(.el-table__row.row-on-delivery-note.current-row > td.el-table__cell) {
  background-color: #b3d0ee !important;
}
</style>
