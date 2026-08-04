<!--
  QuantityDialog.vue

  大号触屏数量选择弹窗（2026-07-30）。
  - 用于领取 / 放回 / 送检三个扫码流程的 quantity 确认
  - 默认数值 = max（最大化）
  - 按钮网格：-10 / -1 / +1 / +10 / 归零 / 最大化（左负右正）
-->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="min(95vw, 800px)"
    :align-center="true"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    destroy-on-close
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div class="qty-dialog-body">
      <!-- 零件信息 -->
      <div class="part-info">
        <div v-if="serialNo" class="info-line">
          <span class="info-label">序列号</span>
          <span class="info-value">{{ serialNo }}</span>
        </div>
        <div v-if="partName" class="info-line">
          <span class="info-label">名称</span>
          <span class="info-value">{{ partName }}</span>
        </div>
        <div class="info-line">
          <span class="info-label">上限</span>
          <span class="info-value">{{ max }}</span>
        </div>
      </div>

      <!-- 超大数字 -->
      <div class="qty-display" aria-live="polite">
        <span class="qty-number">{{ qty }}</span>
        <span class="qty-max">/ {{ max }}</span>
      </div>

      <!-- 按钮网格 -->
      <div class="btn-grid">
        <el-button class="qty-btn adjust" size="large" @click="sub(10)">-10</el-button>
        <el-button class="qty-btn adjust" size="large" @click="sub(1)">-1</el-button>
        <el-button class="qty-btn adjust" size="large" @click="add(1)">+1</el-button>
        <el-button class="qty-btn adjust" size="large" @click="add(10)">+10</el-button>
        <el-button class="qty-btn action" size="large" @click="setZero">归零</el-button>
        <el-button class="qty-btn action" size="large" type="primary" plain @click="setMax">
          最大化
        </el-button>
      </div>
    </div>

    <template #footer>
      <el-button size="large" class="footer-btn" @click="onCancel">取消</el-button>
      <el-button
        size="large"
        type="primary"
        class="footer-btn"
        :disabled="qty <= 0"
        @click="onConfirm"
      >
        {{ confirmText }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: boolean
  max: number
  serialNo?: string | null
  partName?: string | null
  actionLabel?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  confirm: [qty: number]
  cancel: []
}>()

const qty = ref(0)

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      qty.value = props.max
    }
  },
  { immediate: true },
)

const dialogTitle = computed<string>(() => {
  const label = props.actionLabel || '数量'
  const id = props.serialNo || ''
  return id ? `${label}数量 · ${id}` : `${label}数量`
})

const confirmText = computed<string>(() => {
  return props.actionLabel ? `确定${props.actionLabel}` : '确定'
})

function add(n: number): void {
  qty.value = Math.min(qty.value + n, props.max)
}

function sub(n: number): void {
  qty.value = Math.max(qty.value - n, 0)
}

function setZero(): void {
  qty.value = 0
}

function setMax(): void {
  qty.value = props.max
}

function onCancel(): void {
  emit('cancel')
  emit('update:modelValue', false)
}

function onConfirm(): void {
  if (qty.value <= 0) return
  emit('confirm', qty.value)
}
</script>

<style lang="scss" scoped>
.qty-dialog-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 24px;
  padding: 8px;
  user-select: none;
  -webkit-touch-callout: none;
}

.part-info {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 100%;
}

.info-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
}

.info-label {
  color: #909399;
}

.info-value {
  color: #303133;
  font-weight: 600;
}

.qty-display {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 24px 0;
}

.qty-number {
  font-size: 72px;
  font-weight: 700;
  color: #303133;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.qty-max {
  font-size: 28px;
  color: #909399;
}

.btn-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  width: 100%;
}

.qty-btn {
  min-height: 72px;
  font-size: 24px;
  font-weight: 600;
  touch-action: manipulation;
}

.qty-btn.adjust {
  font-size: 26px;
}

/* 归零 / 最大化 各占 2 列 */
.btn-grid .qty-btn:nth-child(5),
.btn-grid .qty-btn:nth-child(6) {
  grid-column: span 2;
}

.footer-btn {
  min-width: 160px;
  min-height: 64px;
  font-size: 20px;
  font-weight: 600;
}
</style>
