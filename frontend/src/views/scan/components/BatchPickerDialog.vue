<!--
  BatchPickerDialog.vue

  同一条码命中列表里多个批次时弹出（2026-08-02 接入）。
  用法（与同目录 ShelfPickerDialog / ProcessPickerDialog 范式一致）：
    props:  modelValue: boolean
            code: string          -- 扫到的条码（用于标题）
            rows: PartItem[]      -- 命中的多个批次
    emits:  update:modelValue(v)
            pick(row)             -- 工人点某行触发；调用方负责后续选中 / 滚动 / 打开下一弹窗

  单行点选即关弹窗（不可改）。卡片按批次号升序展示；显示 batch_no / 数量 /
  当前 holder 文本 / 下一工序。点击 emit('pick')，调用方按业务需要驱动后续动作。
-->

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`扫描 ${code} 在当前列表有 ${rows.length} 个批次`"
    width="640"
    :close-on-click-modal="false"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <p class="hint">
      同一条码在当前列表里命中多个批次。请点击要操作的那一张卡片。
    </p>

    <div v-if="rows.length === 0" class="empty-state">
      <el-icon :size="48" color="#c0c4cc"><Box /></el-icon>
      <p>本条码无批次在当前列表，请刷新后再试。</p>
    </div>

    <div v-else class="batch-list">
      <el-card
        v-for="b in sortedRows"
        :key="b.batch_id || b.id"
        shadow="hover"
        class="batch-row"
        @click="onPick(b)"
      >
        <div class="batch-line">
          <span class="serial">{{ b.serial_no || b.drawing_no }}</span>
          <el-tag type="info" size="small" effect="plain">
            批次{{ b.batch_no ?? 1 }}
          </el-tag>
          <el-tag
            v-if="b.is_urgent"
            type="danger"
            size="small"
            effect="dark"
            class="urgent-pulse"
          >加急</el-tag>
          <span class="name">{{ b.name }}</span>
          <span class="qty">× {{ b.quantity }}</span>
        </div>
        <div class="batch-meta">
          <span class="holder">
            <el-icon><Box /></el-icon>
            <span>{{ holderText(b) }}</span>
          </span>
          <span v-if="b.next_process_name" class="next">
            下一工序：{{ b.next_process_name }}
          </span>
        </div>
      </el-card>
    </div>

    <template #footer>
      <el-button size="large" @click="onCancel">取消</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Box } from '@element-plus/icons-vue'
import type { PartItem } from '@/api/parts'

const props = defineProps<{
  modelValue: boolean
  code: string
  rows: PartItem[]
}>()

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  pick: [row: PartItem]
}>()

/** 批次号升序展示；缺 batch_no 时按 1 处理 */
const sortedRows = computed(() =>
  [...props.rows].sort((a, b) => {
    const ax = a.batch_no ?? 1
    const bx = b.batch_no ?? 1
    return ax - bx
  }),
)

/** 显示卡片当前 holder：kind='shelf' 取货架码，'worker' 取工人名，'outsource_company' 取公司名 */
function holderText(p: PartItem): string {
  switch (p.current_holder_kind) {
    case 'shelf':
      return p.shelf_code ? `货架 ${p.shelf_code}` : '货架 —'
    case 'worker':
      return p.worker_name ? `工人 ${p.worker_name}` : '工人 —'
    case 'outsource_company':
      return p.outsource_company_name
        ? `外协 ${p.outsource_company_name}`
        : '外协 —'
    default:
      return p.current_holder_display ?? p.location ?? '未知位置'
  }
}

function onPick(row: PartItem): void {
  emit('pick', row)
  emit('update:modelValue', false)
}

function onCancel(): void {
  emit('update:modelValue', false)
}
</script>

<style lang="scss" scoped>
.hint {
  margin: 0 0 16px;
  color: #606266;
  font-size: 14px;
}
.batch-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 60vh;
  overflow-y: auto;
}
.batch-row {
  display: flex !important;
  flex-direction: column;
  gap: 8px;
  padding: 14px 18px !important;
  border: 1px solid #e4e7ed;
  border-left: 4px solid #409eff;
  border-radius: 8px;
  cursor: pointer;
  background: #fff;
  transition: border-color .15s, background .15s, box-shadow .15s;
}
.batch-row:hover {
  box-shadow: 0 2px 12px rgba(64, 158, 255, .12);
  border-color: #409eff;
}
.batch-line {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.batch-meta {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 13px;
  color: #606266;
  flex-wrap: wrap;
}
.serial {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 18px;
  font-weight: 700;
  color: #303133;
}
.name { font-size: 14px; color: #303133; }
.qty { color: #409eff; font-weight: 700; }
.holder {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #909399;
}
.next { color: #67c23a; }

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 64px 0;
  gap: 12px;
  color: #606266;
  p { margin: 0; }
}

@keyframes urgentPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}
.urgent-pulse { animation: urgentPulse 1.2s ease-in-out infinite; }
</style>
