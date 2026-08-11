<!--
  DeliveryDateChip.vue

  报工台（PICK / RETURN / INSPECT）工件卡片右上角的交期 chip。
  把 ScanPickParts / ScanReturnParts / ScanInspectParts 三页重复的 span 模板与样式集中。
  「系统交期」优先级：若 system_delivery_date 已设置，显示它 + 加一个「系统交期」小标签；
  否则 fallback 到 planned_delivery_date（保持原行为）。
-->

<template>
  <span class="delivery-date" :class="urgencyClass">
    <el-icon><Calendar /></el-icon>
    <span>{{ formattedDate }}</span>
    <span v-if="daysText" class="days-left">· {{ daysText }}</span>
    <el-tag
      v-if="hasSystem"
      type="info"
      size="small"
      effect="plain"
      class="system-tag"
    >系统交期</el-tag>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Calendar } from '@element-plus/icons-vue'
import {
  formatDeliveryDate,
  deliveryDaysLeftText,
  deliveryUrgencyClass,
} from '@/utils/deliveryDate'

const props = defineProps<{
  plannedDeliveryDate: string | null
  systemDeliveryDate: string | null
}>()

const effectiveDate = computed(() => props.systemDeliveryDate ?? props.plannedDeliveryDate)
const hasSystem = computed(() => props.systemDeliveryDate != null)
const formattedDate = computed(() => formatDeliveryDate(effectiveDate.value))
const daysText = computed(() => deliveryDaysLeftText(effectiveDate.value))
const urgencyClass = computed(() => deliveryUrgencyClass(effectiveDate.value))
</script>

<style scoped>
.delivery-date {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 16px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 4px;
  background: #f5f7fa;
  color: #606266;
}
.delivery-date.overdue { color: #f56c6c; background: #fef0f0; }
.delivery-date.due-soon { color: #e6a23c; background: #fdf6ec; }
.days-left { font-weight: 500; font-size: 13px; margin-left: 2px; }
.system-tag { margin-left: 2px; }
</style>