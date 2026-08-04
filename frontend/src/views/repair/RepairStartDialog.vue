<script setup lang="ts">
/**
 * RepairStartDialog.vue - 「开始返修」确认弹窗（PR-M 2026-08-04）
 *
 * DELIVERED → REPAIRING：选择返修数量（部分量先拆再转），调 startPartRepair。
 */
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Tools } from '@element-plus/icons-vue'
import { startPartRepair } from '@/api/parts'
import type { PartItem } from '@/api/parts'

const props = defineProps<{
  modelValue: boolean
  target: PartItem | null
}>()
const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  confirm: []
}>()

const quantity = ref<number>(1)
const submitting = ref(false)

watch(
  () => [props.modelValue, props.target?.id] as const,
  ([v]) => {
    if (v) quantity.value = props.target?.quantity ?? 1
  },
  { immediate: true },
)

async function onConfirm(): Promise<void> {
  if (!props.target || !quantity.value) return
  submitting.value = true
  try {
    await startPartRepair(props.target.id, {
      batch_id: props.target.batch_id ?? null,
      quantity: quantity.value,
    })
    const label = props.target.serial_no || props.target.drawing_no
    ElMessage.success(`零件 ${label} 已开始返修 × ${quantity.value}`)
    emit('confirm')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(`开始返修失败：${(e as Error).message}`)
  } finally {
    submitting.value = false
  }
}

function onCancel(): void {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="开始返修"
    width="480px"
    :close-on-click-modal="false"
    @update:model-value="(v) => emit('update:modelValue', v)"
  >
    <div v-if="target" class="summary">
      <div><strong>流水号：</strong>{{ target.serial_no || '—' }}</div>
      <div><strong>批次：</strong>{{ target.batch_label || '—' }}</div>
      <div><strong>图号：</strong>{{ target.drawing_no }}</div>
      <div><strong>名称：</strong>{{ target.name }}</div>
      <div><strong>数量：</strong>{{ target.quantity }}</div>
      <el-alert
        v-if="target.has_been_repaired"
        type="warning"
        :closable="false"
        show-icon
        title="该件此前已返修过，会再次标记返修"
      />
    </div>
    <el-form label-width="84px" style="margin-top: 12px">
      <el-form-item label="返修数量" required>
        <el-input-number
          v-model="quantity"
          :min="1"
          :max="target?.quantity ?? 1"
          :precision="0"
          style="width: 160px"
        />
        <span v-if="target" class="muted" style="margin-left: 8px">
          / {{ target.quantity }}
        </span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="onCancel">取消</el-button>
      <el-button
        type="warning"
        :loading="submitting"
        :disabled="!quantity"
        @click="onConfirm"
      >
        <el-icon><Tools /></el-icon>
        <span>确认开始返修</span>
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.summary > div {
  margin-bottom: 6px;
  font-size: 14px;
}
.summary > div strong {
  display: inline-block;
  min-width: 70px;
  color: #606266;
}
.muted {
  color: #909399;
}
</style>
