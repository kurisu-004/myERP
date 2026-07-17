<!--
  ShelfPickerDialog.vue

  共享 HMI 卡片网格 picker 弹窗（2026-07-10 创建，2026-07-13 扩展为 RETURN + INSPECT 两用，2026-07-17
  接入 HmiPickerCard 通用卡 + 空状态回退）。
  - 卡片网格（auto-fit, 220-280px 列宽）
  - 默认高亮 + 自动选中推荐架
  - 「完成」直接接受当前选中架；点其他卡片切换选中
  - 取消按钮保留（工人可放弃放回/送检）
  - 空状态可配置「返回上级」动作：调用方传 emptyActionLabel 则按钮显示，点击触发 empty-action 事件

  props:
    modelValue: boolean                       // 弹窗可见
    nextProcessId: string                     // RETURN 必填（用于查 /shelves/for-return）
    kind?: 'return' | 'inspection' = 'return' // picker 用途（INSPECT 走 /shelves/for-inspection）
    emptyActionLabel?: string                  // 空状态下方的操作按钮文案；不传则不显示
  emits:
    update:modelValue(v: boolean)
    confirm(shelfId: string)
    cancel()
    empty-action()                            // 空状态操作按钮点击；接收方应关闭 dialog 并回退流程
-->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="kind === 'inspection' ? '选择送检货架' : '选择放回货架'"
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
      <p class="error-hint">
        {{ kind === 'inspection'
            ? '请联系管理员在「账号管理」给本 SHELF_ACCOUNT 账号绑定品检货架'
            : '请联系管理员在「货架管理」给某架配置该工序' }}
      </p>
    </div>
    <div v-else-if="shelves.length === 0" class="empty-state">
      <el-icon :size="48" color="#c0c4cc"><Box /></el-icon>
      <p class="empty-text">暂无可用货架</p>
      <el-button
        v-if="emptyActionLabel"
        type="primary"
        size="large"
        class="empty-action-btn"
        @click="onEmptyAction"
      >
        {{ emptyActionLabel }}
      </el-button>
    </div>
    <div v-else class="card-grid">
      <HmiPickerCard
        v-for="s in shelves"
        :key="s.id"
        kind="shelf"
        :code="s.code"
        :name="s.name"
        :location="s.location || undefined"
        :current-load="s.current_load"
        :mapped-process-codes="s.mapped_process_codes"
        :is-selected="s.id === selectedId"
        @select="onSelect(s.id)"
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
        <span>{{ kind === 'inspection' ? '完成 · 送检到该架' : '完成 · 放到该架' }}</span>
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
import HmiPickerCard from '@/components/HmiPickerCard.vue'
import { listShelvesForReturn, listShelvesForInspection } from '@/api/shelves'
import type { ShelfForReturn } from '@/types/shelf'

const props = withDefaults(defineProps<{
  modelValue: boolean
  /** RETURN 必填（用于查 /shelves/for-return）；INSPECT 时可省略。 */
  nextProcessId?: string
  kind?: 'return' | 'inspection'
  /** 空状态操作按钮文案；不传则不显示按钮 */
  emptyActionLabel?: string
}>(), {
  kind: 'return',
  nextProcessId: '',
  emptyActionLabel: '',
})

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  confirm: [shelfId: string]
  cancel: []
  /** 空状态操作按钮点击；接收方应关闭 dialog 并回退流程 */
  'empty-action': []
}>()

const loading = ref(false)
const errorMessage = ref<string | null>(null)
const shelves = ref<ShelfForReturn[]>([])
const selectedId = ref<string | null>(null)

watch(
  () => [props.modelValue, props.nextProcessId, props.kind] as const,
  async ([visible, pid, k]) => {
    if (!visible) return
    if (k === 'inspection') {
      await loadInspection()
    } else if (pid) {
      await loadReturn(pid)
    }
  },
  { immediate: true },
)

async function loadReturn(nextProcessId: string): Promise<void> {
  loading.value = true
  errorMessage.value = null
  shelves.value = []
  selectedId.value = null
  try {
    const result = await listShelvesForReturn(nextProcessId)
    shelves.value = result.items
    // 2026-07-17 移除「默认选中推荐架」行为：工人点选 free；推荐字段后端保留
    // 用于客户端下次调用 if needed，但本 dialog 不再自动高亮 + 一键提交。
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

async function loadInspection(): Promise<void> {
  loading.value = true
  errorMessage.value = null
  shelves.value = []
  selectedId.value = null
  try {
    const result = await listShelvesForInspection()
    shelves.value = result.items
    // 不自动选中推荐架（与 RETURN 一致，2026-07-17 移除）
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
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
    ElMessage.warning(props.kind === 'inspection' ? '请先选择送检货架' : '请先选择放回货架')
    return
  }
  emit('confirm', selectedId.value)
}

function onCancel(): void {
  emit('cancel')
  emit('update:modelValue', false)
}

/**
 * 空状态「返回上级」按钮点击：发事件给调用方处理流程回退（比如 RETURN 流程回退到
 * ProcessPickerDialog 让工人重选工序），同时把 dialog 关闭。
 */
function onEmptyAction(): void {
  emit('empty-action')
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
.empty-state {
  gap: 16px;
  padding: 64px 0;
  .empty-text { font-size: 18px; font-weight: 500; color: #606266; }
  .empty-action-btn {
    min-width: 200px;
    font-size: 18px;
    font-weight: 600;
    padding: 14px 32px;
  }
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
