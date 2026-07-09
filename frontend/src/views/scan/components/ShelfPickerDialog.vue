<!--
  ShelfPickerDialog.vue

  共享 HMI RETURN 卡片网格 picker 弹窗（2026-07-10）。
  - 卡片网格（auto-fit, 220-280px 列宽）
  - 默认高亮 + 自动选中推荐架
  - 「完成」直接接受当前选中架；点其他卡片切换选中
  - 取消按钮保留（工人可放弃放回）

  props:
    modelValue: boolean     // 弹窗可见
    nextProcessId: string   // 必填（用于查 /shelves/for-return）
  emits:
    update:modelValue(v: boolean)
    confirm(shelfId: string)
    cancel()
-->
<template>
  <el-dialog
    :model-value="modelValue"
    title="选择放回货架"
    width="800px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div v-if="loading" class="loading-state">
      <el-icon :size="36" class="is-loading"><Loading /></el-icon>
      <p>正在加载候选货架...</p>
    </div>
    <div v-else-if="errorMessage" class="error-state">
      <el-icon :size="36" color="#f56c6c"><CircleCloseFilled /></el-icon>
      <p class="error-text">{{ errorMessage }}</p>
      <p class="error-hint">请联系管理员在「货架管理」给某架配置该工序</p>
    </div>
    <div v-else-if="shelves.length === 0" class="empty-state">
      <el-icon :size="36" color="#c0c4cc"><Box /></el-icon>
      <p>暂无可用货架</p>
    </div>
    <div v-else class="card-grid">
      <ShelfPickerCard
        v-for="s in shelves"
        :key="s.id"
        :shelf="s"
        :is-selected="s.id === selectedId"
        @select="onSelect"
      />
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
        <span>完成 · 放到该架</span>
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  Box,
  CircleCloseFilled,
  Loading,
  Select,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import ShelfPickerCard from '@/components/ShelfPickerCard.vue'
import { listShelvesForReturn } from '@/api/shelves'
import type { ShelfForReturn } from '@/types/shelf'

const props = defineProps<{
  modelValue: boolean
  nextProcessId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  confirm: [shelfId: string]
  cancel: []
}>()

const loading = ref(false)
const errorMessage = ref<string | null>(null)
const shelves = ref<ShelfForReturn[]>([])
const selectedId = ref<string | null>(null)

watch(
  () => [props.modelValue, props.nextProcessId] as const,
  async ([visible, pid]) => {
    if (!visible || !pid) return
    await load(pid)
  },
  { immediate: true },
)

async function load(nextProcessId: string): Promise<void> {
  loading.value = true
  errorMessage.value = null
  shelves.value = []
  selectedId.value = null
  try {
    const result = await listShelvesForReturn(nextProcessId)
    shelves.value = result.items
    // 默认选中推荐架
    const recommended = result.items.find((s) => s.is_recommended)
    if (recommended) {
      selectedId.value = recommended.id
    } else {
      selectedId.value = result.recommended_shelf_id || result.items[0]?.id || null
    }
  } catch (err: unknown) {
    // 后端 BIZ_SHELF_NO_MATCH_FOR_PROCESS 等业务异常会进到这里
    const msg = err instanceof Error ? err.message : String(err)
    // 后端 axios 拦截器把 BizError message 放进 err.message
    errorMessage.value = msg || '加载失败'
    selectedId.value = null
    ElMessage.error(errorMessage.value)
  } finally {
    loading.value = false
  }
}

function onSelect(shelfId: string): void {
  selectedId.value = shelfId
}

function onConfirm(): void {
  if (!selectedId.value) {
    ElMessage.warning('请先选择放回货架')
    return
  }
  emit('confirm', selectedId.value)
}

function onCancel(): void {
  emit('cancel')
  emit('update:modelValue', false)
}
</script>

<style lang="scss" scoped>
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
  max-height: 60vh;
  overflow-y: auto;
  padding: 4px;
}
.loading-state,
.error-state,
.empty-state {
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
  .error-hint { font-size: 13px; color: #909399; }
}
.is-loading { animation: spin 1s linear infinite; }
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.confirm-btn {
  min-width: 200px;
  font-size: 16px;
  font-weight: 600;
}
</style>
