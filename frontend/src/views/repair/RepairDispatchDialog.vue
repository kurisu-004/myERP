<script setup lang="ts">
/**
 * RepairDispatchDialog.vue - 「完成返修」弹窗（PR-M 2026-08-04）
 *
 * REPAIRING → ON_SHELF/INSPECTION：双 Tab 完成返修
 * - 「下发到生产架」：先选工序（可选），后选该工序对应的生产区货架
 * - 「送检到品检架」：选 INSPECTION 区货架
 *
 * 与下发页风格一致；后端按 shelf.zone 自动路由（PRODUCTION/INSPECTION）。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { CircleCheck, Select } from '@element-plus/icons-vue'
import { completePartRepair } from '@/api/parts'
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

watch(
  () => [props.modelValue, props.target?.id] as const,
  async ([v]) => {
    if (v) {
      actionTab.value = 'dispatch'
      processId.value = props.target?.next_process_id ?? ''
      shelfId.value = ''
      inspShelfId.value = ''
      await reloadOptions()
    }
  },
  { immediate: true },
)

async function onDispatchConfirm(): Promise<void> {
  if (!props.target || !shelfId.value) return
  submittingDispatch.value = true
  try {
    await completePartRepair(props.target.id, shelfId.value, {
      batch_id: props.target.batch_id ?? null,
      next_process_id: processId.value || null,
    })
    const label = props.target.serial_no || props.target.drawing_no
    ElMessage.success(`返修完成 · 已下发 ${label} 到生产架`)
    emit('confirm')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(`返修下发失败：${(e as Error).message}`)
  } finally {
    submittingDispatch.value = false
  }
}

async function onInspectConfirm(): Promise<void> {
  if (!props.target || !inspShelfId.value) return
  submittingInspect.value = true
  try {
    await completePartRepair(props.target.id, inspShelfId.value, {
      batch_id: props.target.batch_id ?? null,
    })
    const label = props.target.serial_no || props.target.drawing_no
    ElMessage.success(`返修完成 · 已送检 ${label} 到品检架`)
    emit('confirm')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(`返修送检失败：${(e as Error).message}`)
  } finally {
    submittingInspect.value = false
  }
}

function onCancel(): void {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="完成返修"
    width="min(95vw, 720px)"
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
        title="该件此前已返修过，本次仍会保留返修标记"
      />
    </div>

    <el-tabs v-model="actionTab" style="margin-top: 8px">
      <!-- 下发到生产架 -->
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
            @click="onDispatchConfirm"
          >
            <el-icon><Select /></el-icon>
            <span>完成 · 下发到生产架</span>
          </el-button>
        </div>
      </el-tab-pane>

      <!-- 送检到品检架 -->
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
            @click="onInspectConfirm"
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
