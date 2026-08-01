<!--
  ColumnVisibilityPopover.vue

  「⚙ 列设置」按钮 + el-popover + el-checkbox-group 弹窗。
  用于 el-table 一览页面:用户可单独勾选每列的可见性,通过 v-model
  绑定到 useColumnVisibility 暴露的 currentMap。

  用法:
    <ColumnVisibilityPopover :defs="columnDefs" :model-value="columnVisibility.currentMap" @update:model-value="(v: Record<string, boolean>) => (columnVisibility.currentMap = v)" />

  Props:
    - defs:        ColumnDef[]          列定义(key + label + defaultVisible?)
    - modelValue:  Record<string,boolean>  可见性 map(v-model 双向绑定)
    - label:       string?              按钮文案(默认空,只显示图标)
    - placement:   string?              弹窗位置(默认 bottom-end)
    - width:       number?              弹窗宽度(默认 220)

  Emits:
    - update:modelValue  Record<string,boolean>  map 变化时触发
    - reset                                    点「重置」时触发(由父组件决定重置策略)
-->
<template>
  <el-popover
    v-model:visible="popoverVisible"
    trigger="click"
    :placement="placement"
    :width="width"
    popper-class="column-visibility-popover"
  >
    <template #reference>
      <el-button text class="cvp-trigger" :title="label || '列设置'">
        <el-icon><Setting /></el-icon>
        <span v-if="label" class="cvp-trigger__label">{{ label }}</span>
      </el-button>
    </template>

    <div class="cvp">
      <div class="cvp__header">显示列</div>
      <el-checkbox-group
        :model-value="visibleKeys"
        @update:model-value="onChange"
        class="cvp__list"
      >
        <el-checkbox
          v-for="d in defs"
          :key="d.key"
          :value="d.key"
          :label="d.label"
          border
          class="cvp__item"
        />
      </el-checkbox-group>
      <div class="cvp__footer">
        <el-button link size="small" type="primary" :disabled="allVisible" @click="emitShowAll">全选</el-button>
        <el-button link size="small" type="primary" :disabled="allHidden" @click="emitHideAll">全不选</el-button>
        <el-button link size="small" @click="emitReset">重置</el-button>
      </div>
    </div>
  </el-popover>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Setting } from '@element-plus/icons-vue'
import type { ColumnDef } from '@/composables/useColumnVisibility'

interface Props {
  defs: readonly ColumnDef[]
  label?: string
  placement?: 'bottom' | 'bottom-start' | 'bottom-end' | 'top' | 'top-start' | 'top-end'
  width?: number
}

const props = withDefaults(defineProps<Props>(), {
  label: '',
  placement: 'bottom-end',
  width: 220,
})

// Vue 3.4+ defineModel:接受 v-model 绑定,自动处理 prop + emit 双向。
// 这里「visible 自身无默认」 → required:true 由 useColumnVisibility.currentMap
// (WritableComputedRef) 提供,无需任何 cast 即可与 `v-model="x.currentMap"` 配合。
const modelValue = defineModel<Record<string, boolean>>({ required: true })

const emit = defineEmits<{
  reset: []
}>()

const popoverVisible = ref(false)

// 把 map → array(el-checkbox-group 期望数组)
const visibleKeys = computed<string[]>(() => {
  const keys: string[] = []
  for (const d of props.defs) {
    if (modelValue.value[d.key] !== false) keys.push(d.key)
  }
  return keys
})

// 把 array → map(每个 defs.key 都出现,不在新数组里的 = false)
// el-checkbox-group 的 @update:model-value 实际类型是 CheckboxGroupValueType
// (string | number | boolean),但我们这里 el-checkbox 的 :value 全部是 string,
// 所以运行时只可能是 string[];为通过 vue-tsc 类型校验用 unknown 收口。
function onChange(newKeys: unknown): void {
  const arr = Array.isArray(newKeys) ? newKeys.filter((k): k is string => typeof k === 'string') : []
  const set = new Set(arr)
  const next: Record<string, boolean> = {}
  for (const d of props.defs) {
    next[d.key] = set.has(d.key)
  }
  modelValue.value = next
}

const allVisible = computed(() => visibleKeys.value.length === props.defs.length)
const allHidden = computed(() => visibleKeys.value.length === 0)

function emitShowAll(): void {
  const next: Record<string, boolean> = {}
  for (const d of props.defs) next[d.key] = true
  modelValue.value = next
}

function emitHideAll(): void {
  const next: Record<string, boolean> = {}
  for (const d of props.defs) next[d.key] = false
  modelValue.value = next
}

function emitReset(): void {
  emit('reset')
}
</script>

<style lang="scss" scoped>
.cvp-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-secondary);

  &:hover {
    color: var(--el-color-primary);
  }

  &__label {
    font-size: 13px;
  }
}

.cvp {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 160px;

  &__header {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border-color);
  }

  &__list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 320px;
    overflow-y: auto;
    padding: 4px 0;
  }

  &__item {
    margin-right: 0 !important;  // 覆盖 el-checkbox border 默认的右外边距
    width: 100%;
  }

  &__footer {
    display: flex;
    justify-content: space-between;
    gap: 4px;
    padding-top: 6px;
    border-top: 1px solid var(--border-color);
  }
}
</style>

<style lang="scss">
/* 非 scoped:el-popover 弹层挂在 body 下,scoped 选择器够不到 */
.column-visibility-popover {
  padding: 10px 12px !important;
}
</style>
