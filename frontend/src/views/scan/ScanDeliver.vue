<!--
  ScanDeliver.vue — 扫码台：送货司机确认发货（PR-C 2026-07-10）

  - 入口：/scan/action 选「送货」（仅 work_type='送货司机' 可见）
  - 第一屏：列 READY_TO_SHIP 零件按客户分组（每客户即一张「送货单」）
    点击客户 → 进入第二屏
  - 第二屏：选中送货单的零件清单 + 扫码框
    扫到流水号 → 调 scanDeliverPart 触发 DELIVERED
    全部扫完 → 「完成本次送货」按钮回到第一屏
  - 不依赖 SHELF_ACCOUNT scope（司机不在生产区作业）
-->
<template>
  <div class="scan-deliver">
    <div class="topbar">
      <div class="topbar-left">
        <el-icon :size="22" color="#fff"><Avatar /></el-icon>
        <span class="title">送货</span>
        <el-divider direction="vertical" class="divider" />
        <span class="worker-name">{{ worker?.name ?? '—' }}</span>
        <el-tag size="default" type="info" effect="dark" class="badge-tag">
          {{ worker?.badge_code ?? '' }}
        </el-tag>
      </div>
      <div class="topbar-right">
        <el-button type="info" plain @click="goHome">
          <el-icon><HomeFilled /></el-icon>
          <span>返回首页</span>
        </el-button>
        <el-button type="warning" plain @click="backToBadge">
          <el-icon><Refresh /></el-icon>
          <span>重新扫工牌</span>
        </el-button>
      </div>
    </div>

    <!-- 第一屏：选送货单（按客户分组） -->
    <div v-if="!selectedCustomerPath" class="content">
      <h2 class="state-title">请选择要送的客户</h2>
      <div v-if="loading" class="loading">加载中...</div>
      <div v-else-if="cards.length === 0" class="empty">
        <el-empty description="暂无待送货零件" />
      </div>
      <div v-else class="card-grid">
        <el-card
          v-for="c in cards"
          :key="c.customer_path"
          shadow="hover"
          class="customer-card"
          @click="enterCustomer(c.customer_path)"
        >
          <div class="card-row">
            <span class="card-title">{{ c.customer_path }}</span>
            <el-tag type="warning" size="small" v-if="c.urgent_count > 0">
              加急 {{ c.urgent_count }}
            </el-tag>
          </div>
          <div class="card-stats">
            <span>共 {{ c.total_count }} 件</span>
          </div>
          <div class="card-list">
            <div v-for="it in c.preview" :key="it.id" class="card-item">
              {{ it.serial_no || it.drawing_no }} · {{ it.name }}
            </div>
            <div v-if="c.total_count > c.preview.length" class="card-more">
              还有 {{ c.total_count - c.preview.length }} 件...
            </div>
          </div>
        </el-card>
      </div>
    </div>

    <!-- 第二屏：扫零件确认发货 -->
    <div v-else class="content">
      <div class="customer-header">
        <el-button @click="backToList" link>
          <el-icon><Back /></el-icon>
          <span>返回客户列表</span>
        </el-button>
        <h2 class="customer-title">
          {{ selectedCustomerPath }}
          <span class="customer-stats">
            已扫 <strong>{{ confirmedIds.size }}</strong> / {{ items.length }} 件
          </span>
        </h2>
      </div>

      <el-input
        ref="scanInputRef"
        v-model="scanValue"
        placeholder="扫零件流水号（直接扫码）"
        size="large"
        clearable
        @keyup.enter="onScanSubmit"
        style="max-width: 480px"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>

      <div class="part-list">
        <div
          v-for="it in items"
          :key="it.id"
          class="part-row"
          :class="{ 'part-confirmed': confirmedIds.has(it.id) }"
        >
          <span class="part-serial">{{ it.serial_no || '—' }}</span>
          <span class="part-drawing">{{ it.drawing_no }}</span>
          <span class="part-name">{{ it.name }}</span>
          <span class="part-meta">数量 {{ it.quantity }} · 计划交期 {{ it.planned_delivery_date }}</span>
          <el-tag
            v-if="confirmedIds.has(it.id)"
            type="success"
            size="small"
          >✓ 已确认</el-tag>
          <el-tag v-else type="info" size="small">待扫描</el-tag>
        </div>
      </div>

      <div class="footer-bar">
        <el-button @click="backToList">返回</el-button>
        <el-button
          type="success"
          :loading="finishing"
          :disabled="confirmedIds.size === 0"
          @click="onFinish"
        >完成本次送货</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Avatar, Back, HomeFilled, Search } from '@element-plus/icons-vue'
import {
  listParts,
  scanDeliverPart,
  getPartBySerial,
  type PartItem,
} from '@/api/parts'
import type { PartListItem } from '@/types/parts'
import { useScanSession } from '@/composables/useScanSession'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'

const router = useRouter()
const { worker, requireWorker, reset: resetScanSession } = useScanSession()

const loading = ref(true)
// 第一屏：按客户分组的预览（用 PartListItem 即可；customer_id 不可用，按 path 分组）
const allItems = ref<PartListItem[]>([])
// 第二屏：选中客户的零件（来自 listParts + 单条 getPartBySerial 补全 customer_id）
const items = ref<PartItem[]>([])

// 选中客户的标识（用 customer_path 字符串）
const selectedCustomerPath = ref<string>('')

interface CustomerCard {
  customer_path: string
  total_count: number
  urgent_count: number
  preview: PartListItem[]
}

const cards = ref<CustomerCard[]>([])
const confirmedIds = ref<Set<string>>(new Set())
const scanValue = ref('')
const scanInputRef = ref<{ focus: () => void } | null>(null)
const finishing = ref(false)

const selectedItems = computed<PartListItem[]>(() => {
  if (!selectedCustomerPath.value) return []
  return items.value as unknown as PartListItem[]
})

function groupByCustomer(): CustomerCard[] {
  const map = new Map<string, CustomerCard>()
  for (const it of allItems.value) {
    const key = it.customer_path || it.customer_name || '未知客户'
    if (!map.has(key)) {
      map.set(key, {
        customer_path: key,
        total_count: 0,
        urgent_count: 0,
        preview: [],
      })
    }
    const c = map.get(key)!
    c.total_count += 1
    if (it.is_urgent) c.urgent_count += 1
    if (c.preview.length < 3) c.preview.push(it)
  }
  return Array.from(map.values()).sort(
    (a, b) => b.urgent_count - a.urgent_count,
  )
}

async function fetchReadyToShip(): Promise<void> {
  loading.value = true
  try {
    const resp = await listParts({
      statuses: ['READY_TO_SHIP'],
      sort_by: 'PLANNED_DELIVERY_DATE',
      sort_dir: 'ASC',
      limit: 200,
      offset: 0,
    })
    allItems.value = resp.items
    cards.value = groupByCustomer()
  } catch (e) {
    ElMessage.error(`加载待送货零件失败：${(e as Error).message}`)
    cards.value = []
  } finally {
    loading.value = false
  }
}

function enterCustomer(customerPath: string): void {
  selectedCustomerPath.value = customerPath
  items.value = allItems.value.filter(
    (it) => (it.customer_path || it.customer_name || '未知客户') === customerPath,
  ) as unknown as PartItem[]
  confirmedIds.value = new Set()
  void nextTick(() => {
    scanInputRef.value?.focus()
  })
}

function backToList(): void {
  selectedCustomerPath.value = ''
  items.value = []
  confirmedIds.value = new Set()
  scanValue.value = ''
}

async function onScanSubmit(): Promise<void> {
  const code = scanValue.value.trim()
  if (!code) return
  if (!worker.value) {
    ElMessage.error('未扫工牌')
    return
  }
  // 1) 流水号 → 查零件（getPartBySerial 返回 PartItem 含 customer_id）
  let part: PartItem | null = null
  try {
    part = await getPartBySerial(code)
  } catch {
    part = null
  }
  if (!part) {
    ElMessage.error(`流水号 ${code} 找不到对应零件`)
    scanValue.value = ''
    return
  }
  const partPath = part.customer_path || part.customer_name || '未知客户'
  if (partPath !== selectedCustomerPath.value) {
    ElMessage.error(`该零件不属于当前客户（${partPath}）`)
    scanValue.value = ''
    return
  }
  if (confirmedIds.value.has(part.id)) {
    ElMessage.warning(`零件 ${code} 已经确认过`)
    scanValue.value = ''
    return
  }
  if (part.status !== 'READY_TO_SHIP') {
    ElMessage.error(`零件 ${code} 状态不是「待送货」（当前：${part.status}）`)
    scanValue.value = ''
    return
  }
  // 2) 触发 DELIVERED
  try {
    await scanDeliverPart({
      part_id: part.id,
      worker_badge_code: worker.value.badge_code,
    })
    confirmedIds.value.add(part.id)
    // 从 allItems 中移除（已发货）
    allItems.value = allItems.value.filter((it) => it.id !== part!.id)
    cards.value = groupByCustomer()
    ElMessage.success(`${code} 已确认发货`)
    scanValue.value = ''
    void nextTick(() => {
      scanInputRef.value?.focus()
    })
  } catch (e) {
    ElMessage.error(`发货失败：${(e as Error).message}`)
    scanValue.value = ''
  }
}

async function onFinish(): Promise<void> {
  if (confirmedIds.value.size === 0) return
  try {
    await ElMessageBox.confirm(
      `本批次共 ${confirmedIds.value.size} 件已发货，确认完成本次送货？`,
      '完成本次送货',
      { type: 'success', confirmButtonText: '完成', cancelButtonText: '返回继续扫码' },
    )
  } catch {
    return
  }
  finishing.value = true
  try {
    // 刷新：把已确认的零件从 allItems 中移除（它们现在 status=DELIVERED）
    const confirmedSet = confirmedIds.value
    allItems.value = allItems.value.filter((it) => !confirmedSet.has(it.id))
    cards.value = groupByCustomer()
    if (allItems.value.length === 0 || cards.value.length === 0) {
      ElMessage.success('所有待送货零件已完成')
      backToList()
    } else {
      backToList()
      ElMessage.success('本批次已完成，可继续选择下一客户')
    }
  } finally {
    finishing.value = false
  }
}

function goHome(): void {
  void router.push('/dashboard')
}

function backToBadge(): void {
  resetScanSession()
  void router.replace('/scan/badge')
}

// 全局扫码监听（复用现有 useBarcodeScanner）
// 注：driver 用的是物理扫码枪，跟车间扫码台是同一套硬件；扫码后
// 自动填到 scanValue 并触发 onScanSubmit。
const { onScan } = useBarcodeScanner()
let unsubscribeScan: (() => void) | null = null

import { watch } from 'vue'
onMounted(async () => {
  if (!requireWorker(router)) return
  await fetchReadyToShip()
  // 注册扫码回调（扫描枪在第二屏触发）
  unsubscribeScan = onScan((code) => {
    if (selectedCustomerPath.value && code.trim()) {
      scanValue.value = code
      void onScanSubmit()
    }
  })
  void nextTick(() => {
    scanInputRef.value?.focus()
  })
})

onBeforeUnmount(() => {
  if (unsubscribeScan) {
    unsubscribeScan()
    unsubscribeScan = null
  }
})
</script>

<style lang="scss" scoped>
.scan-deliver {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(90deg, #142d54 0%, #1e4d8b 100%);
  color: #fff;
  padding: 12px 24px;
  height: 60px;
  flex-shrink: 0;
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 16px;
}
.topbar-right {
  display: flex;
  gap: 8px;
}
.title {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 2px;
}
.divider {
  background: rgba(255, 255, 255, 0.3);
  height: 20px;
}
.worker-name {
  font-size: 18px;
  font-weight: 600;
}
.badge-tag {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.content {
  flex: 1;
  overflow: auto;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
  padding: 24px;
}
.state-title {
  text-align: center;
  font-size: 24px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 24px;
}
.loading,
.empty {
  text-align: center;
  padding: 60px 0;
  color: #909399;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.customer-card {
  cursor: pointer;
  transition: transform 0.15s;
}
.customer-card:hover {
  transform: translateY(-2px);
}
.card-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
}
.card-stats {
  color: var(--text-secondary);
  font-size: 13px;
  margin-bottom: 8px;
}
.card-list {
  border-top: 1px dashed var(--border-color-lighter);
  padding-top: 8px;
}
.card-item {
  font-size: 13px;
  color: #606266;
  padding: 2px 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-more {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}
.customer-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.customer-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.customer-stats {
  font-size: 14px;
  font-weight: 400;
  color: var(--text-secondary);
  margin-left: 12px;
}
.customer-stats strong {
  color: var(--el-color-success);
  margin: 0 4px;
}
.part-list {
  margin-top: 16px;
  background: #fff;
  border-radius: 6px;
  padding: 4px 0;
}
.part-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px dashed var(--border-color-lighter);
}
.part-row:last-child {
  border-bottom: none;
}
.part-confirmed {
  background: #f0f9eb;
}
.part-serial {
  width: 90px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-weight: 600;
}
.part-drawing {
  width: 120px;
  color: var(--text-secondary);
}
.part-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.part-meta {
  width: 240px;
  color: var(--text-secondary);
  font-size: 12px;
}
.footer-bar {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
  padding: 12px 16px;
  background: #fff;
  border-radius: 6px;
}
</style>