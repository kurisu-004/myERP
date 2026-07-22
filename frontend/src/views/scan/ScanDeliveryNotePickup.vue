<!--
  司机扫码领取（PR-G 2026-07-22 新增）

  路径：/scan/delivery-note-pickup/:id（id = delivery_note.id）

  流程：
  1) 进入 → GET /delivery-notes/{id} 拉取 line_items + scanned_serials（已扫描进度）
  2) 全局 useBarcodeScanner 监听：扫到图纸码 → POST /pickup-scan
     - 累积成功 → 写一行 PICKUP_SCANNED 事件，UI 实时刷新
     - 错扫/重复/不属于本单 → 提示
  3) 右侧实时「已扫 X / 共 Y」计数器；X == Y 时「领取并归档」按钮高亮可用
  4) 点击「领取并归档」 → POST /pickup（要求 driver_worker_id）→ 跳回 /scan/delivery-note-pickup

  设计参考 ScanInspectParts.vue（select + awaitingScan + exact-match 形态）。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import { useScanSession } from '@/composables/useScanSession'
import {
  getNote,
  pickupScan,
  pickup as pickupFinalize,
} from '@/api/deliveryNote'
import type { DeliveryNoteDetailOut } from '@/types/deliveryNote'

const route = useRoute()
const router = useRouter()
const { onScan } = useBarcodeScanner()
const { worker } = useScanSession()

const note = ref<DeliveryNoteDetailOut | null>(null)
const scanValue = ref('')
const expectingScan = ref(false)
const submitting = ref(false)
const driverWorkerId = ref<string>('')

async function fetchNote() {
  const id = route.params.id as string
  if (!id) return
  try {
    note.value = await getNote(id)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  }
}

onMounted(async () => {
  if (!worker.value) {
    router.replace('/scan/badge')
    return
  }
  await fetchNote()
  subscribeScan()
})

const expectedCount = computed(() => note.value?.line_items.length ?? 0)
const scannedCount = computed(() => note.value?.scanned_serials.length ?? 0)
const isReady = computed(() =>
  expectedCount.value > 0 && scannedCount.value >= expectedCount.value,
)

let unsub: (() => void) | null = null
function subscribeScan() {
  unsub?.()
  unsub = onScan((raw) => {
    if (!raw.trim()) return
    if (submitting.value) return
    if (!expectingScan.value) return
    scanValue.value = raw.trim()
    void submitScan()
  })
}

onBeforeUnmount(() => {
  unsub?.()
  unsub = null
})

async function submitScan() {
  if (!note.value) return
  const code = scanValue.value.trim()
  if (!code) return
  if (note.value.scanned_serials.includes(code)) {
    ElMessage.warning(`序列号 ${code} 已扫过`)
    return
  }
  try {
    await pickupScan(note.value.id, {
      part_serial: code,
      badge_code: worker.value?.badge_code ?? null,
    })
    ElMessage.success(`已登记 ${code}`)
    scanValue.value = ''
    await fetchNote()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '扫码失败')
  }
}

async function onManualScan() {
  if (!scanValue.value.trim()) {
    ElMessage.warning('请输入或扫描序列号')
    return
  }
  await submitScan()
}

async function finalize() {
  if (!note.value) return
  if (!isReady.value) {
    ElMessage.warning('请先扫齐所有零件')
    return
  }
  if (!worker.value?.id) {
    ElMessage.error('当前 worker 会话无效')
    return
  }
  // 取当前扫码台登录的 worker.id 作为 driver；后端 pickup() 会再校验 worker.work_type.code
  driverWorkerId.value = String(worker.value.id)

  try {
    await ElMessageBox.confirm(
      `已扫 ${scannedCount.value} 件；点击确认完成领取并归档。`,
      '领取并归档',
      { type: 'success', confirmButtonText: '确认领取', cancelButtonText: '取消' },
    )
  } catch { return }
  submitting.value = true
  try {
    await pickupFinalize(note.value.id, {
      driver_worker_id: driverWorkerId.value,
      version: note.value.version,
      badge_code: worker.value.badge_code,
    })
    ElMessage.success(`已领取并归档 ${note.value.delivery_note_no}`)
    router.replace('/scan/delivery-note-pickup')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '领取失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div v-if="note" class="scan-pickup">
    <el-page-header @back="$router.replace('/scan/delivery-note-pickup')" class="page-header">
      <template #content>
        <span class="page-title">{{ note.delivery_note_no }}</span>
      </template>
    </el-page-header>

    <div class="counts">
      已扫 <strong>{{ scannedCount }}</strong> / 共
      <strong>{{ expectedCount }}</strong>
      <el-tag
        v-if="isReady"
        type="success"
        size="small"
        style="margin-left: 8px"
      >
        已扫齐
      </el-tag>
    </div>

    <el-card shadow="never" class="scan-card">
      <div class="scan-input-row">
        <el-input
          v-model="scanValue"
          placeholder="扫图纸码（serial_no）"
          :disabled="!expectingScan"
          size="large"
          @keyup.enter="onManualScan"
          @focus="expectingScan = true"
        />
        <el-button
          type="primary"
          size="large"
          :loading="submitting"
          :disabled="!isReady"
          @click="finalize"
        >
          领取并归档
        </el-button>
      </div>
      <p class="hint">
        扫描图纸背面的 Code128 条码（payload = serial_no），每扫一件记录一次。
        全部扫齐后按钮高亮，点击完成领取。
      </p>
    </el-card>

    <el-card shadow="never" class="line-items-card">
      <template #header><span>零件清单</span></template>
      <el-table
        :data="note.line_items"
        stripe
        border
        height="500"
        :row-class-name="({ row }: any) =>
          row.scanned ? 'row-scanned' : ''
        "
      >
        <el-table-column type="index" label="#" width="60" />
        <el-table-column prop="serial_no" label="序列号" width="160" />
        <el-table-column prop="drawing_no" label="图号" width="160" />
        <el-table-column prop="name" label="名称" min-width="200" />
        <el-table-column prop="quantity" label="数量" width="80" align="center" />
        <el-table-column label="已扫" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.scanned" type="success" size="small">✓</el-tag>
            <span v-else class="muted">待扫</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.scan-pickup { padding: 16px; }
.page-header { margin-bottom: 16px; }
.page-title { font-size: 18px; font-weight: 600; }
.counts { font-size: 16px; margin-bottom: 12px; }
.scan-card, .line-items-card { margin-bottom: 16px; }
.scan-input-row { display: flex; gap: 12px; align-items: center; }
.hint { font-size: 13px; color: #909399; margin: 8px 0 0; }
:deep(.el-table__row.row-scanned) > td.el-table__cell {
  background-color: #e6f4ff !important;
}
.muted { color: #909399; }
</style>
