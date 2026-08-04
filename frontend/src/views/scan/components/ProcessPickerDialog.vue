<!--
  ProcessPickerDialog.vue

  HMI 工序选择弹窗（2026-07-17）。
  - el-tabs (自产 / 外协) 切工序大类
  - 每个 tab body 是 HmiPickerCard kind='process' 网格
  - 预填 currentProcessId（来自工件 next_process）并自动选对应 category tab
  - 「下一步」文案按 kind 区分（RETURN → 选货架 / INSPECT → 选送检架）

  props:
    modelValue: boolean                 弹窗可见
    kind?: 'return' | 'inspection'      picker 用途（影响 title / empty 文案 / 下一步按钮）
    currentProcessId?: string | null    工件 next_process_id，预填为推荐
  emits:
    update:modelValue(v: boolean)
    confirm(processId: string)
    cancel()
-->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="titleText"
    width="min(95vw, 1100px)"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div v-if="loading" class="loading-state">
      <el-icon :size="36" class="is-loading"><Loading /></el-icon>
      <p>正在加载工序列表...</p>
    </div>
    <div v-else-if="errorMessage" class="error-state">
      <el-icon :size="36" color="#f56c6c"><CircleCloseFilled /></el-icon>
      <p class="error-text">{{ errorMessage }}</p>
    </div>
    <div v-else>
      <el-tabs v-model="activeTab" class="process-tabs">
        <el-tab-pane
          v-for="cat in tabs"
          :key="cat.key"
          :label="cat.label"
          :name="cat.key"
        >
          <div v-if="cat.processes.length === 0" class="tab-empty">
            <el-icon :size="48" color="#c0c4cc"><Box /></el-icon>
            <p>「{{ cat.label }}」暂无工序</p>
          </div>
          <div v-else class="card-grid">
            <HmiPickerCard
              v-for="p in cat.processes"
              :key="p.id"
              kind="process"
              :code="p.code"
              :name="p.name"
              :category="p.category"
              :is-selected="p.id === selectedId"
              :disabled="excludeProcessIds.includes(p.id)"
              :hint="excludeProcessIds.includes(p.id) ? '当前工序不可选' : undefined"
              @select="onSelect(p.id)"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <template #footer>
      <el-button size="large" @click="onCancel">取消</el-button>
      <el-button
        type="primary"
        size="large"
        :disabled="!selectedId"
        class="confirm-btn"
        @click="onConfirm"
      >
        <el-icon><Select /></el-icon>
        <span>{{ nextButtonText }}</span>
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 工序大按钮 picker（2026-07-17，HMI 重设计）。
 *
 * 数据源：一次性 GET /processes?limit=200 拉所有 INHOUSE+OUTSOURCE 工序，
 * 不按工人 scope 过滤（用户决策：显示全部 + 后端 strict invariant 兜底）。
 * 工件 next_process_id 推荐态通过 currentProcessId prop 传入，预填选中并切到对应 category tab。
 *
 * 与 ShelfPickerDialog 同样基于 HmiPickerCard 视觉规范（kind=process），
 * 视觉一致性靠共享组件保证。
 */
import { computed, ref, watch } from 'vue'
import {
  Box,
  CircleCloseFilled,
  Loading,
  Select,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import HmiPickerCard from '@/components/HmiPickerCard.vue'
import { listProcesses } from '@/api/process'
import type { Process } from '@/types/process'

const props = withDefaults(defineProps<{
  modelValue: boolean
  /** 'return' = 选下一道工序；'inspection' = 选送检对应工序 */
  kind?: 'return' | 'inspection'
  /** 工件 next_process_id：预填选中并标推荐（橙色边框） */
  currentProcessId?: string | null
  /** 不可选的工序 id 列表（被排除的卡片显示为禁用 + hint） */
  excludeProcessIds?: string[]
}>(), {
  kind: 'return',
  currentProcessId: null,
  excludeProcessIds: () => [],
})

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  /** 确认后把整条 Process 传给父组件；父组件按 process.id 提交 + 用 name/code 展示 */
  confirm: [process: Process]
  cancel: []
}>()

const loading = ref(false)
const errorMessage = ref<string | null>(null)
const processes = ref<Process[]>([])
const selectedId = ref<string | null>(null)
const activeTab = ref<'INHOUSE' | 'OUTSOURCE'>('INHOUSE')

const titleText = computed<string>(() =>
  props.kind === 'inspection' ? '选择送检对应工序' : '选择下一道工序',
)

const nextButtonText = computed<string>(() =>
  props.kind === 'inspection' ? '下一步 · 选送检架' : '下一步 · 选货架',
)

interface TabEntry {
  key: 'INHOUSE' | 'OUTSOURCE'
  label: string
  processes: Process[]
}

const tabs = computed<TabEntry[]>(() => [
  {
    key: 'INHOUSE' as const,
    label: '自产',
    processes: processes.value.filter((p) => p.category === 'INHOUSE'),
  },
  {
    key: 'OUTSOURCE' as const,
    label: '外协',
    processes: processes.value.filter((p) => p.category === 'OUTSOURCE'),
  },
])

watch(
  () => [props.modelValue, props.currentProcessId] as const,
  async ([visible, currentId]) => {
    if (!visible) return
    // 先把 selectedId + activeTab 用 currentId 预填；load 完成后保持
    selectedId.value = currentId ?? null
    await load()
    if (currentId) {
      const matched = processes.value.find((p) => p.id === currentId)
      if (matched) {
        activeTab.value = matched.category === 'OUTSOURCE'
          ? 'OUTSOURCE' : 'INHOUSE'
      }
    } else {
      // 无 currentProcessId：默认自产 tab
      activeTab.value = 'INHOUSE'
    }
    // 被排除的工序不能预填：清空（确认按钮会因 selectedId null 而禁用，强制用户改选）
    if (selectedId.value && props.excludeProcessIds.includes(selectedId.value)) {
      selectedId.value = null
    }
  },
  { immediate: true },
)

async function load(): Promise<void> {
  loading.value = true
  errorMessage.value = null
  try {
    const resp = await listProcesses({ limit: 200 })
    processes.value = resp.items
    // 若 currentProcessId 在新列表中找不到（已被删除），清空选中让用户重选
    if (selectedId.value && !processes.value.some((p) => p.id === selectedId.value)) {
      selectedId.value = null
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    errorMessage.value = msg || '加载失败'
    selectedId.value = null
    ElMessage.error(errorMessage.value)
  } finally {
    loading.value = false
  }
}

function onSelect(id: string): void {
  selectedId.value = id
}

function onConfirm(): void {
  if (!selectedId.value) {
    ElMessage.warning('请先选择工序')
    return
  }
  const selected = processes.value.find((p) => p.id === selectedId.value)
  if (!selected) {
    // 不该发生：selectedId 是从 processes 派生出来的；保护一下
    ElMessage.error('所选工序已不可用，请重新选择')
    return
  }
  emit('confirm', selected)
}

function onCancel(): void {
  emit('cancel')
  emit('update:modelValue', false)
}
</script>

<style lang="scss" scoped>
.process-tabs {
  // 抵消 el-dialog 默认内边距，让 tabs 内容贴边
  margin: -16px -8px;
  padding: 0 8px;
}
.tab-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  gap: 12px;
  color: #909399;
  p { margin: 0; font-size: 16px; }
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
  padding: 16px 0;
  max-height: 60vh;
  overflow-y: auto;
}
.loading-state,
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 0;
  gap: 12px;
  color: #606266;
  p { margin: 0; }
}
.error-state {
  color: #f56c6c;
  .error-text { font-size: 16px; font-weight: 600; }
}
.is-loading { animation: spin 1s linear infinite; }
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
/* HMI 触摸友好 tab 标签 */
:deep(.el-tabs__item) {
  font-size: 20px;
  font-weight: 600;
  padding: 0 32px;
  height: 56px;
  line-height: 56px;
}
:deep(.el-tabs__active-bar) {
  height: 4px;
}
.confirm-btn {
  min-width: 200px;
  font-size: 18px;
  font-weight: 600;
}
</style>
