<template>
  <div class="wt-proc">
    <el-card shadow="never" class="layout-card">
      <div class="layout">
        <!-- 左:工种列表 -->
        <div class="left">
          <div class="left-title">工种</div>
          <el-table
            :data="workTypes"
            v-loading="loadingWT"
            highlight-current-row
            :row-key="(r: WorkType) => r.id"
            @row-click="onSelectWT"
            size="small"
            border
            stripe
            :height="isMobile ? 280 : '100%'"
          >
            <el-table-column prop="code" label="代码" min-width="120" align="center"/>
            <el-table-column prop="name" label="名称" min-width="120" align="center"/>
          </el-table>
        </div>
        <!-- 右:映射工序 -->
        <div class="right">
          <div class="right-title">
            <span>{{ mappingTitle }}</span>
            <el-button
              v-if="selectedWT"
              type="primary" size="small"
              :loading="saving"
              :disabled="!dirty"
              @click="onSave"
            >{{ isMobile ? '保存' : '保存映射' }}</el-button>
          </div>
          <el-checkbox-group v-model="selectedProcessIds" v-loading="loadingMapping" class="proc-group">
            <el-checkbox
              v-for="p in processes"
              :key="p.id"
              :value="p.id"
              :label="p.id"
              border
              class="proc-item"
            >
              <span class="proc-label">
                <strong>{{ p.code }}</strong>
                <span style="margin-left: 6px">{{ p.name }}</span>
                <el-tag
                  :type="p.category === 'INHOUSE' ? 'primary' : 'warning'"
                  size="small" style="margin-left: 6px"
                >{{ PROCESS_CATEGORY_LABEL[p.category] }}</el-tag>
              </span>
            </el-checkbox>
          </el-checkbox-group>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { listWorkTypes, getWorkTypeProcesses, setWorkTypeProcesses } from '@/api/workType'
import { listProcesses } from '@/api/process'
import type { WorkType, WorkTypeWithProcesses } from '@/types/workType'
import type { Process } from '@/types/process'
import { PROCESS_CATEGORY_LABEL } from '@/types/process'
import { useBreakpoint } from '@/composables/useBreakpoint'

const { isMobile } = useBreakpoint()

const workTypes = ref<WorkType[]>([])
const processes = ref<Process[]>([])
const selectedWT = ref<WorkType | null>(null)
const selectedProcessIds = ref<string[]>([])
const initialProcessIds = ref<string[]>([])

const loadingWT = ref(false)
const loadingMapping = ref(false)
const saving = ref(false)

const dirty = ref(false)
const mappingTitle = computed(() => (
  selectedWT.value ? `「${selectedWT.value.name}」可执行的工序` : '请选择工种'
))

async function fetchWorkTypes(): Promise<void> {
  loadingWT.value = true
  try {
    const res = await listWorkTypes({ limit: 200 })
    workTypes.value = res.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载工种失败')
  } finally {
    loadingWT.value = false
  }
}

async function fetchProcesses(): Promise<void> {
  try {
    const res = await listProcesses({ limit: 200 })
    processes.value = res.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载工序失败')
  }
}

async function onSelectWT(row: WorkType): Promise<void> {
  selectedWT.value = row
  loadingMapping.value = true
  try {
    const detail: WorkTypeWithProcesses = await getWorkTypeProcesses(row.id)
    selectedProcessIds.value = detail.processes.map((p) => p.process_id)
    initialProcessIds.value = [...selectedProcessIds.value]
    dirty.value = false
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载映射失败')
  } finally {
    loadingMapping.value = false
  }
}

// 监听选中变化,标记 dirty
watch(selectedProcessIds, (v) => {
  if (!selectedWT.value) return
  const a = [...v].sort()
  const b = [...initialProcessIds.value].sort()
  dirty.value = a.length !== b.length || a.some((x, i) => x !== b[i])
}, { deep: true })

async function onSave(): Promise<void> {
  if (!selectedWT.value) return
  saving.value = true
  try {
    await setWorkTypeProcesses(selectedWT.value.id, { process_ids: selectedProcessIds.value })
    initialProcessIds.value = [...selectedProcessIds.value]
    dirty.value = false
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await Promise.all([fetchWorkTypes(), fetchProcesses()])
})
</script>

<style lang="scss" scoped>
.wt-proc { display: flex; flex-direction: column; gap: 12px; height: calc(100vh - 160px); }
.layout-card { flex: 1; min-height: 0; :deep(.el-card__body) { height: 100%; } }
.layout { display: flex; gap: 16px; height: 100%; min-height: 480px; }
.left { flex: 0 0 320px; display: flex; flex-direction: column; }
.left-title, .right-title {
  font-weight: 600; font-size: 14px; margin-bottom: 8px;
  display: flex; align-items: center; justify-content: space-between;
}
.right { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.proc-group {
  display: flex; flex-direction: column; gap: 8px; overflow-y: auto; flex: 1;
}
.proc-item {
  margin: 0 !important; padding: 8px 12px !important;
  :deep(.el-checkbox__label) { width: 100%; }
}
.proc-label { display: inline-flex; align-items: center; }

@include until(md) {
  .wt-proc {
    height: auto;
    min-height: 0;
  }

  .layout-card {
    flex: none;
    :deep(.el-card__body) { height: auto; }
  }

  .layout {
    flex-direction: column;
    height: auto;
    min-height: 0;
  }

  .left {
    flex: none;
    width: 100%;
  }

  .left :deep(.el-table) {
    height: 280px !important;
  }

  .right {
    flex: none;
    min-height: 320px;
    overflow: visible;
  }

  .right-title {
    gap: 8px;
    flex-wrap: wrap;
  }

  .proc-group {
    min-height: 0;
    max-height: 420px;
  }

  .proc-label {
    min-width: 0;
    flex-wrap: wrap;
  }
}
</style>