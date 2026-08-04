<script setup lang="ts">
/**
 * 一步式返修下发 dialog（PR-M 2026-08-04 续）
 *
 * 调 POST /parts/{id}/repair-dispatch：
 * DELIVERED / INSPECTION / READY_TO_SHIP → REPAIRING → ON_SHELF / INSPECTION
 * 原子完成；中间状态 REPAIRING 不落库。
 *
 * UI 结构（el-tabs 双子 Tab）：
 * - 「下发到生产架」：先选工序，后选该工序映射的生产区货架
 * - 「送检到品检架」：直接选 INSPECTION 区货架
 *
 * 顶部「返修数量」输入框（quantity < batch.quantity 触发 _maybe_split 拆批）
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { CircleCheck, Select } from '@element-plus/icons-vue'
import { repairDispatch } from '@/api/parts'
import { listShelves } from '@/api/shelves'
import { listProcesses } from '@/api/process'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import type { PartItem } from '@/api/parts'
import type { Shelf } from '@/types/shelf'
import type { Process } from '@/types/process'

const props = defineProps<{
  modelValue: boolean
  target: PartItem | null
}>()
const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  confirm: []
}>()

const actionTab = ref<'dispatch' | 'inspect'>('dispatch')
const quantity = ref<number>(1)
const processId = ref<string>('')
const shelfId = ref<string>('')
const inspShelfId = ref<string>('')
const submittingDispatch = ref(false)
const submittingInspect = ref(false)

const productionShelves = ref<Shelf[]>([])
const inspectionShelves = ref<Shelf[]>([])
const processes = ref<Process[]>([])

const { filteredShelves: filteredProductionShelves, filteredProcesses, load: loadProcessMap } =
  useShelfProcessFilter(
    productionShelves,
    processes,
    computed({
      get: () => shelfId.value || null,
      set: (v) => { shelfId.value = v ?? '' },
    }),
    computed({
      get: () => processId.value || null,
      set: (v) => { processId.value = v ?? '' },
    }),
  )

watch(
  () => [props.modelValue, props.target?.id] as const,
  async ([v]) => {
    if (v) {
      actionTab.value = 'dispatch'
      quantity.value = props.target?.quantity ?? 1
      processId.value = props.target?.next_process_id ?? ''
      shelfId.value = ''
      inspShelfId.value = ''
      await reloadOptions()
    }
  },
  { immediate: true },
)

async function reloadOptions(): Promise<void> {
  const [prod, insp, procs] = await Promise.all([
    listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 }),
    listShelves({ zone: 'INSPECTION', is_active: true, limit: 200 }),
    listProcesses({ limit: 200 }),
  ])
  productionShelves.value = prod.items
  inspectionShelves.value = insp.items
  processes.value = procs.items
  await loadProcessMap()
}

async function onSubmit(): Promise<void> {
  if (!props.target || !quantity.value) return
  const isInspect = actionTab.value === 'inspect'
  if (isInspect) {
    if (!inspShelfId.value) return
  } else {
    if (!shelfId.value) return
  }
  const submitting = isInspect ? submittingInspect : submittingDispatch
  submitting.value = true
  try {
    await repairDispatch(props.target.id, {
      shelf_id: isInspect ? inspShelfId.value : shelfId.value,
      next_process_id: !isInspect ? (processId.value || null) : null,
      batch_id: props.target.batch_id ?? null,
      quantity:
        quantity.value < (props.target.quantity ?? 1)
          ? quantity.value
          : null,
    })
    const label = props.target.serial_no || props.target.drawing_no
    ElMessage.success(`返修完成 · ${label} 已${isInspect ? '送检' : '下发'}`)
    emit('confirm')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(`返修下发失败：${(e as Error).message}`)
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
    title="返修下发"
    width="min(95vw, 720px)"
    :close-on-click-modal="false"
    @update:model-value="(v) => emit('update:modelValue', v)"
  >
    <div v-if="target" class="summary">
      <div><strong>流水号：</strong>{{ target.serial_no || '—' }}</div>
      <div><strong>批次：</strong>{{ target.batch_label || '—' }}</div>
      <div><strong>图号：</strong>{{ target.drawing_no }}</div>
      <div><strong>名称：</strong>{{ target.name }}</div>
      <div><strong>总数：</strong>{{ target.quantity }}</div>
      <el-alert
        v-if="target.has_been_repaired"
        type="warning"
        :closable="false"
        title="该件此前已返修过，本次仍会保留返修标记"
      />
    </div>

    <el-form label-width="84px" style="margin-top: 12px">
      <el-form-item label="返修数量">
        <el-input-number
          v-model="quantity"
          :min="1"
          :max="target?.quantity ?? 1"
          :precision="0"
          style="width: 160px"
        />
        <span v-if="target" class="muted" style="margin-left: 8px">
          / {{ target.quantity }}（留部分返修请改小）
        </span>
      </el-form-item>
    </el-form>

    <el-tabs v-model="actionTab" style="margin-top: 8px">
      <el-tab-pane label="下发到生产架" name="dispatch">
        <el-form label-width="96px">
          <el-form-item label="下一道工序">
            <el-select
              v-model="processId"
              clearable
              placeholder="选工序后过滤货架"
              style="width: 100%"
            >
              <el-option
                v-for="p in filteredProcesses"
                :key="p.id"
                :value="String(p.id)"
                :label="`${p.code} — ${p.name}`"
              />
              <template #empty>
                <span class="muted">无可用工序</span>
              </template>
            </el-select>
          </el-form-item>
          <el-form-item label="目标生产货架" required>
            <el-select
              v-model="shelfId"
              clearable
              placeholder="先选工序，自动按 shelf↔process 过滤"
              :disabled="!processId"
              style="width: 100%"
            >
              <el-option
                v-for="s in filteredProductionShelves"
                :key="s.id"
                :value="String(s.id)"
                :label="`${s.code} — ${s.name}`"
                :disabled="!s.is_active"
              />
              <template #empty>
                <span class="muted">
                  {{ processId
                    ? '当前工序未映射任何生产货架'
                    : '请先选择工序' }}
                </span>
              </template>
            </el-select>
          </el-form-item>
        </el-form>
        <div class="actions">
          <el-button
            type="primary"
            :loading="submittingDispatch"
            :disabled="!shelfId"
            @click="onSubmit"
          >
            <el-icon><Select /></el-icon>
            <span>完成 · 下发到生产架</span>
          </el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane label="送检到品检架" name="inspect">
        <el-form label-width="96px">
          <el-form-item label="品检货架" required>
            <el-select
              v-model="inspShelfId"
              clearable
              placeholder="选 INSPECTION 区 active 货架"
              style="width: 100%"
            >
              <el-option
                v-for="s in inspectionShelves"
                :key="s.id"
                :value="String(s.id)"
                :label="`${s.code} — ${s.name}`"
                :disabled="!s.is_active"
              />
              <template #empty>
                <span class="muted">无可用品检架</span>
              </template>
            </el-select>
          </el-form-item>
        </el-form>
        <div class="actions">
          <el-button
            type="warning"
            :loading="submittingInspect"
            :disabled="!inspShelfId"
            @click="onSubmit"
          >
            <el-icon><CircleCheck /></el-icon>
            <span>完成 · 送检到该架</span>
          </el-button>
        </div>
      </el-tab-pane>
    </el-tabs>

    <template #footer>
      <el-button @click="onCancel">取消</el-button>
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
.actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
