<!-- 外协发送/接收页（2026-07-16 新增；合并原 OutsourceSendList + OutsourceReceiveList）

3 个 tab：
  - 可发送（默认）：列出至少有一条 APPROVED 报价的「可发送」零件
  - 待接收：列出 status=OUTSOURCE 的零件，等回收

PR-H 2026-07-29：「已接收历史」tab 已移除 —— 功能由 per-company 对账页承担
（/outsource/companies/:id/sent-parts，基于 t_outsource_quote 统一事实表）。

URL ?tab=sendable|receiving 记忆上次选择；初次进入默认 可发送。
-->
<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Promotion } from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'
import { listApprovedForSend } from '@/api/outsource'
import { listCustomers, type Customer } from '@/api/customer'
import { listShelves } from '@/api/shelves'
import type { Shelf as ShelfItem } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import type { Process } from '@/types/process'
import {
  receiveFromOutsource,
  receiveFromOutsourceToInspection,
  sendToOutsource as sendPartToOutsource,
  getPartBySerial,
  listDirectOutsourceCandidates,
  listOutsourceSendable,
  type SendToOutsourcePayload,
  type PartItem,
  listParts,
} from '@/api/parts'
import type {
  ApprovedQuoteForSendItem,
  OutsourceSendableItem,
} from '@/types/outsource'
import type { DirectOutsourceCandidateItem } from '@/types/directOutsource'
import type { PartListItem } from '@/types/parts'

/** 合并后的可发送项：2026-07-28 后由 GET /parts/outsource-sendable 单端点返回
 * OutsourceSendableItem 统一承担；每行已含 send_mode + source_status。 */
type SendableItem = OutsourceSendableItem

type TabName = 'sendable' | 'receiving'

const route = useRoute()
const router = useRouter()
const { isMobile } = useBreakpoint()
const sendDlg = useDialogSize({ desktopWidth: 520 })
const receiveDlg = useDialogSize({ desktopWidth: 560 })
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

// ============================================================
// Tab 状态（URL ?tab= 同步）
// PR-H 2026-07-29：「已接收历史」tab 已移除（功能由 per-company 对账页承担）。
// ============================================================
function readTabFromQuery(): TabName {
  const t = route.query.tab
  if (t === 'receiving') return t
  return 'sendable'
}
const activeTab = ref<TabName>(readTabFromQuery())

watch(() => route.query.tab, (q) => {
  if (q === 'sendable' || q === 'receiving') {
    activeTab.value = q
  }
})

function onTabChange(name: string | number): void {
  const n = name as TabName
  activeTab.value = n
  router.replace({ path: '/outsource/send-receive', query: { tab: n } })
}

// ============================================================
// 通用下拉数据
// ============================================================
const customers = ref<Customer[]>([])
const shelves = ref<ShelfItem[]>([])
const processes = ref<Process[]>([])

async function loadLookups(): Promise<void> {
  try {
    const [cs, ss, ps] = await Promise.all([
      listCustomers(),
      listShelves({ is_active: true }),
      listProcesses({ limit: 200 }),
    ])
    customers.value = cs
    shelves.value = ss.items
    processes.value = ps.items
  } catch (e) {
    ElMessage.error((e as Error).message ?? '下拉数据加载失败')
  }
}

// ============================================================
// Tab 1：可发送（APPROVAL 已批报价 + DIRECT 直接发送候选，2026-07-28 合并）
// ============================================================
const sendableItems = ref<SendableItem[]>([])
const sendableTotal = ref(0)
const sendableLoading = ref(false)
const sendableError = ref<string | null>(null)
const sendableFilter = reactive({ keyword: '', customer_id: '' })
const sendablePage = ref(1)
const sendablePageSize = ref(20)

async function refreshSendable(): Promise<void> {
  sendableLoading.value = true
  sendableError.value = null
  try {
    // 2026-07-28：统一端点 GET /parts/outsource-sendable（取代旧的两端点合并）
    const r = await listOutsourceSendable({
      keyword: sendableFilter.keyword || undefined,
      customer_id: sendableFilter.customer_id || undefined,
      limit: sendablePageSize.value,
      offset: (sendablePage.value - 1) * sendablePageSize.value,
    })
    sendableItems.value = r.items
    sendableTotal.value = r.total
  } catch (e) {
    sendableItems.value = []
    sendableTotal.value = 0
    sendableError.value = (e as Error).message ?? '加载可发送列表失败'
    ElMessage.error(sendableError.value)
  } finally {
    sendableLoading.value = false
  }
}

const sendDialogVisible = ref(false)
const sendTarget = ref<SendableItem | null>(null)
const sendSelectedCompanyId = ref<string>('')
const sendSubmitting = ref(false)

function canSend(item: SendableItem): boolean {
  if (item.status_label !== 'sendable') return false
  if (item.send_mode === 'DIRECT') {
    return item.company_options.length >= 1
  }
  // APPROVAL: outsource_company_id 由后端确定
  return true
}
function openSend(item: SendableItem): void {
  if (!canSend(item)) {
    ElMessage.warning('该零件当前状态不满足发送条件')
    return
  }
  sendTarget.value = item
  // 直接发送：默认选第一家公司（不能为空数组；前端必有 ≥1）
  if (item.send_mode === 'DIRECT') {
    sendSelectedCompanyId.value = item.company_options[0]?.id ?? ''
  } else {
    sendSelectedCompanyId.value = ''
  }
  sendDialogVisible.value = true
}
async function onConfirmSend(): Promise<void> {
  if (!sendTarget.value) return
  const target = sendTarget.value
  if (target.send_mode === 'DIRECT' && !sendSelectedCompanyId.value) {
    ElMessage.warning('请选择外协公司')
    return
  }
  const companyId: string = target.send_mode === 'DIRECT'
    ? sendSelectedCompanyId.value
    : (target.outsource_company_id ?? '')
  const companyName: string = target.send_mode === 'DIRECT'
    ? target.company_options.find((c) => c.id === sendSelectedCompanyId.value)?.name ?? ''
    : (target.outsource_company_name ?? '')
  try {
    await ElMessageBox.confirm(
      `确认把「${target.part_drawing_no}」（批次 ${target.batch_no}，${target.batch_quantity} 件）发送到「${companyName}」？`,
      '发送外协',
      { type: 'warning', confirmButtonText: '确认发送', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  sendSubmitting.value = true
  try {
    const payload: SendToOutsourcePayload = {
      outsource_company_id: companyId,
      next_process_id: target.next_process_id,
      version: target.version,
      // 2026-07-29 PR-fix-0.2.0 批次化：显式携带 batch_id，
      // 多批次工单下避免 _resolve_target_batch fallback 选错批次。
      batch_id: target.batch_id,
    }
    await sendPartToOutsource(target.part_id, payload)
    ElMessage.success('已发送至外协')
    sendDialogVisible.value = false
    await refreshSendable()
    // 发送成功后该零件应出现在「待接收」tab，主动 refresh 一次
    void refreshReceiving()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '发送失败')
  } finally {
    sendSubmitting.value = false
  }
}

function onSendableSearch(): void {
  sendablePage.value = 1
  void refreshSendable()
}
function onSendableReset(): void {
  sendableFilter.keyword = ''
  sendableFilter.customer_id = ''
  sendablePage.value = 1
  void refreshSendable()
}
function onSendablePageSizeChange(size: number): void {
  sendablePageSize.value = size
  sendablePage.value = 1
  void refreshSendable()
}

// ============================================================
// 扫码批量发送队列（PR-I 2026-07-20）
//
// INSPECTOR / 文员 扫一个序列号 → 查 part → 校验「在可发送列表」 → 入队。
// 队列 N 件后点「确认发送」→ for 循环 POST 单 part 端点。
// 后端 Schema 不变（仍走 POST /parts/{id}/send-to-outsource）。
// ============================================================

interface SendQueueItem {
  part: { id: string; serial_no: string; drawing_no: string; name: string }
  outsource_company_id: string
  outsource_company_name: string
  process_id: string
  process_name: string
  /** 直接发送时为 null；APPROVAL 时为单件报价 */
  price: number | null
  /** OCC：发送时必传（批次 TPartBatch.version） */
  version: number
  /** 2026-07-29 PR-fix-0.2.0 批次化：可发送批次 id（雪花 ID 字符串） */
  batch_id: string
  // 入队后做标记，给 UI 看
  _failed?: boolean
  _failMsg?: string
}

const sendQueue = ref<SendQueueItem[]>([])
const batchSending = ref(false)
const scanInput = ref('')

async function handleScannedSerialForSend(code: string): Promise<void> {
  const trimmed = code.trim()
  if (!trimmed) return
  let part: PartItem
  try {
    part = await getPartBySerial(trimmed)
  } catch (e) {
    ElMessage.error(`序列号 ${trimmed} 未找到：${(e as Error).message}`)
    return
  }
  // 已在队列里？
  if (sendQueue.value.find((q) => q.part.id === part.id)) {
    ElMessage.warning(`${part.serial_no ?? trimmed} 已在发送队列中`)
    return
  }
  // 必须在当前可发送列表里（status_label === 'sendable'）
  const match = sendableItems.value.find(
    (it) => it.part_id === part.id && it.status_label === 'sendable',
  )
  if (!match) {
    ElMessage.warning(`${part.serial_no ?? trimmed} 当前不在可发送列表（可能状态不满足或没有 APPROVED 报价）`)
    return
  }
  // 直接发送 + 多公司 → 强制用户先选公司（不让扫码盲目入队）
  if (match.send_mode === 'DIRECT' && match.company_options.length > 1) {
    sendTarget.value = match
    sendSelectedCompanyId.value = match.company_options[0]?.id ?? ''
    sendDialogVisible.value = true
    ElMessage.info('该外协工序映射了多家公司，请先在弹窗中选择后再扫码入队')
    return
  }
  // 直接发送 + 单公司 或 APPROVAL → 直接入队
  const companyId: string = match.send_mode === 'DIRECT'
    ? (match.company_options[0]?.id ?? '')
    : (match.outsource_company_id ?? '')
  const companyName: string = match.send_mode === 'DIRECT'
    ? (match.company_options[0]?.name ?? '')
    : (match.outsource_company_name ?? '')
  sendQueue.value.push({
    part: {
      id: part.id,
      serial_no: part.serial_no ?? '',
      drawing_no: part.drawing_no,
      name: part.name,
    },
    outsource_company_id: companyId,
    outsource_company_name: companyName,
    process_id: match.next_process_id ?? '',
    process_name: match.next_process_name ?? '',
    price: match.send_mode === 'DIRECT' ? null : Number(match.price),
    version: match.version,
    // 2026-07-29 PR-fix-0.2.0 批次化：携带 batch_id 供发送时回传
    batch_id: match.batch_id,
  })
  ElMessage.success(`已加入发送队列：${part.serial_no ?? trimmed}`)
}

function onScanInputEnter(): void {
  const code = scanInput.value
  scanInput.value = ''
  void handleScannedSerialForSend(code)
}

function onScanInputClear(): void {
  scanInput.value = ''
}

function removeFromSendQueue(idx: number): void {
  sendQueue.value.splice(idx, 1)
}

function clearSendQueue(): void {
  sendQueue.value = []
}

async function onConfirmBatchSend(): Promise<void> {
  if (sendQueue.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确认批量发送 ${sendQueue.value.length} 件零件到外协？`,
      '批量发送',
      { type: 'warning', confirmButtonText: '确认发送', cancelButtonText: '取消' },
    )
  } catch {
    return  // 用户取消
  }
  batchSending.value = true
  const errors: { serial: string; msg: string; idx: number }[] = []
  let okCount = 0
  // 串行 for 循环：避免并发踩状态机；失败项保留在队列可重试
  for (let i = 0; i < sendQueue.value.length; i++) {
    const item = sendQueue.value[i]
    try {
      const payload: SendToOutsourcePayload = {
        outsource_company_id: item.outsource_company_id,
        next_process_id: item.process_id,
        version: item.version,
        // 2026-07-29 PR-fix-0.2.0 批次化：显式携带 batch_id。
        batch_id: item.batch_id,
      }
      await sendPartToOutsource(item.part.id, payload)
      okCount++
      // 成功后从队列移除
      sendQueue.value.splice(i, 1)
      i--  // 抵消 splice 导致的位移
    } catch (e) {
      const msg = (e as Error).message ?? '未知错误'
      errors.push({
        serial: item.part.serial_no || item.part.drawing_no,
        msg,
        idx: i,
      })
      sendQueue.value[i]._failed = true
      sendQueue.value[i]._failMsg = msg
    }
  }
  batchSending.value = false
  if (okCount > 0) {
    ElMessage.success(`成功发送 ${okCount} 件`)
    await refreshSendable()
    void refreshReceiving()
  }
  if (errors.length > 0) {
    ElMessage.error(
      `失败 ${errors.length} 件：${errors.map((e) => `${e.serial} (${e.msg})`).join('; ')}`,
    )
  }
}

// 全局扫码枪监听（学 ScanDeliver.vue）
const { onScan: onGlobalScan } = useBarcodeScanner()
let unsubScan: (() => void) | null = null

onMounted(() => {
  unsubScan = onGlobalScan((code) => {
    // 仅在「可发送」tab 接收扫码（其他 tab 用户若误扫不会触发）
    if (activeTab.value === 'sendable') {
      void handleScannedSerialForSend(code)
    }
  })
})

onBeforeUnmount(() => {
  if (unsubScan) {
    unsubScan()
    unsubScan = null
  }
})

// ============================================================
// Tab 2：待接收
// ============================================================
const receivingItems = ref<PartListItem[]>([])
const receivingTotal = ref(0)
const receivingLoading = ref(false)
const receivingError = ref<string | null>(null)
const receivingFilter = reactive({ keyword: '', customer_id: '' })
const receivingPage = ref(1)
const receivingPageSize = ref(20)

async function refreshReceiving(): Promise<void> {
  receivingLoading.value = true
  receivingError.value = null
  try {
    const r = await listParts({
      statuses: ['OUTSOURCE'],
      keyword: receivingFilter.keyword || undefined,
      customer_id: receivingFilter.customer_id || undefined,
      limit: receivingPageSize.value,
      offset: (receivingPage.value - 1) * receivingPageSize.value,
    })
    receivingItems.value = r.items
    receivingTotal.value = r.total
  } catch (e) {
    receivingItems.value = []
    receivingTotal.value = 0
    receivingError.value = (e as Error).message ?? '加载待接收列表失败'
    ElMessage.error(receivingError.value)
  } finally {
    receivingLoading.value = false
  }
}

const receiveDialogVisible = ref(false)
const receiveTarget = ref<PartListItem | null>(null)
const receiveSubmitting = ref(false)
type Branch = 'production' | 'inspection'
const receiveBranch = ref<Branch>('production')
const receiveShelf = ref('')
const receiveProcess = ref('')
const autoPass = ref(false)

const productionShelves = computed(() =>
  shelves.value.filter((s) => s.zone === 'PRODUCTION' && s.is_active),
)
const inspectionShelves = computed(() =>
  shelves.value.filter((s) => s.zone === 'INSPECTION' && s.is_active),
)
const inhouseProcesses = computed(() =>
  processes.value.filter((p) => p.category === 'INHOUSE'),
)

// 2026-07-17：useShelfProcessFilter 双向收窄（仅 production 分支）。
// inspection 分支无 next_process，走 INSPECTION 货架不过滤。
const {
  filteredShelves: filteredProductionShelves,
  filteredProcesses: filteredInhouseProcesses,
  load: loadReceiveMap,
} = useShelfProcessFilter(
  productionShelves,
  inhouseProcesses,
  computed({
    get: () => receiveShelf.value || null,
    set: (v) => { receiveShelf.value = v ?? '' },
  }),
  computed({
    get: () => receiveProcess.value || null,
    set: (v) => { receiveProcess.value = v ?? '' },
  }),
)

function openReceive(row: PartListItem): void {
  receiveTarget.value = row
  receiveBranch.value = 'production'
  receiveShelf.value = ''
  receiveProcess.value = ''
  autoPass.value = false
  receiveDialogVisible.value = true
  // 2026-07-17：弹窗打开后异步加载映射（仅在 shelves/processes 已就绪时有效）
  void loadReceiveMap()
}
function onReceiveDialogClosed(): void {
  receiveTarget.value = null
  receiveShelf.value = ''
  receiveProcess.value = ''
  autoPass.value = false
  receiveBranch.value = 'production'
}

const receiveBranchLabel = computed(() =>
  receiveBranch.value === 'production'
    ? '进入生产货架继续加工'
    : autoPass.value
      ? '品检 → 通过品检 → 进入待送货'
      : '品检',
)

async function onConfirmReceive(): Promise<void> {
  if (!receiveTarget.value) return
  if (!receiveShelf.value) {
    ElMessage.warning('请选择货架')
    return
  }
  if (receiveBranch.value === 'production' && !receiveProcess.value) {
    ElMessage.warning('生产分支请选择下一道 INHOUSE 工序')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认接收「${receiveTarget.value.drawing_no}」（${receiveBranchLabel.value}）？`,
      '接收外协件',
      { type: 'warning', confirmButtonText: '确认接收', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  receiveSubmitting.value = true
  try {
    if (receiveBranch.value === 'production') {
      await receiveFromOutsource(receiveTarget.value.id, {
        shelf_id: receiveShelf.value,
        next_process_id: receiveProcess.value,
      })
      ElMessage.success('已下发到生产货架')
    } else {
      await receiveFromOutsourceToInspection(receiveTarget.value.id, {
        shelf_id: receiveShelf.value,
        auto_pass_inspection: autoPass.value,
      })
      ElMessage.success(
        autoPass.value
          ? '已送检并自动通过品检 → 待送货'
          : '已送检，等待品检',
      )
    }
    receiveDialogVisible.value = false
    await refreshReceiving()
    // PR-H 2026-07-29：「已接收历史」tab 已移除（功能由 per-company 对账页承担）
  } catch (e) {
    ElMessage.error((e as Error).message ?? '操作失败')
  } finally {
    receiveSubmitting.value = false
  }
}

function onReceivingSearch(): void {
  receivingPage.value = 1
  void refreshReceiving()
}
function onReceivingReset(): void {
  receivingFilter.keyword = ''
  receivingFilter.customer_id = ''
  receivingPage.value = 1
  void refreshReceiving()
}
function onReceivingPageSizeChange(size: number): void {
  receivingPageSize.value = size
  receivingPage.value = 1
  void refreshReceiving()
}

// ============================================================
// 初始化
// PR-H 2026-07-29：「已接收历史」tab 已移除（功能由 per-company 对账页承担）；
// receivedItems / refreshReceived / RECEIVED_STATUSES / goPartDetail 一并删除。
// ============================================================
onMounted(async () => {
  await loadLookups()
  // 默认拉「可发送」；其他 tab 按需 onActivated 时再拉
  await refreshSendable()
  // 预拉一次「待接收」让数字显示在 tab 标题
  void refreshReceiving()
})

// tab 切换时按需拉（避免切换瞬间列表空）
watch(activeTab, async (t) => {
  if (t === 'sendable') void refreshSendable()
  else if (t === 'receiving') void refreshReceiving()
})
</script>

<template>
  <div class="page">
    <el-card shadow="never">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <!-- ====================== Tab 1: 可发送 ====================== -->
        <el-tab-pane name="sendable" label="可发送">
          <!-- 扫码批量发送（PR-I 2026-07-20）：扫序列号自动入队，最后批量提交 -->
          <div class="scan-row">
            <el-input
              v-model="scanInput"
              placeholder="扫码或输入序列号加入发送队列（Enter 入队）"
              clearable
              style="width: 360px"
              @keyup.enter="onScanInputEnter"
              @clear="onScanInputClear"
            >
              <template #prefix>
                <el-icon><Promotion /></el-icon>
              </template>
            </el-input>
            <el-button @click="onScanInputEnter">加入队列</el-button>
            <el-tag v-if="sendQueue.length > 0" type="success" effect="plain" size="small">
              队列 {{ sendQueue.length }} 件
            </el-tag>
            <el-button
              v-if="sendQueue.length > 0"
              link
              size="small"
              @click="clearSendQueue"
            >清空队列</el-button>
          </div>

          <!-- 扫码队列表格 -->
          <el-table
            v-if="sendQueue.length > 0"
            :data="sendQueue"
            stripe
            border
            size="small"
            class="queue-table"
            style="margin-bottom: 12px"
          >
            <el-table-column label="序列号" min-width="110" align="center">
              <template #default="{ row }">
                <span :class="{ muted: !row.part.serial_no }">{{ row.part.serial_no || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="part.drawing_no" label="图号" min-width="120" align="center"/>
            <el-table-column prop="part.name" label="名称" min-width="160" show-overflow-tooltip align="center"/>
            <el-table-column prop="outsource_company_name" label="外协公司" min-width="160" show-overflow-tooltip align="center"/>
            <el-table-column prop="process_name" label="外协工序" min-width="120" align="center"/>
            <el-table-column prop="price" label="单价(元)" min-width="80" align="right" />
            <el-table-column label="状态" min-width="80" align="center">
              <template #default="{ row }">
                <el-tag v-if="row._failed" type="danger" size="small">失败</el-tag>
                <span v-else class="muted">待发</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" min-width="70" fixed="right" align="center">
              <template #default="{ $index }">
                <el-button
                  link
                  type="danger"
                  size="small"
                  @click="removeFromSendQueue($index)"
                >移除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div v-if="sendQueue.length > 0" class="batch-bar">
            <span class="batch-info">已入队 <strong>{{ sendQueue.length }}</strong> 件</span>
            <el-button
              type="primary"
              :loading="batchSending"
              @click="onConfirmBatchSend"
            >
              确认发送 {{ sendQueue.length }} 件
            </el-button>
          </div>

          <div class="filter-row">
            <el-input
              v-model="sendableFilter.keyword"
              placeholder="图号 / 名称 / 序列号"
              clearable
              style="width: 280px"
              @keyup.enter="onSendableSearch"
            />
            <el-select
              v-model="sendableFilter.customer_id"
              clearable
              placeholder="客户（L1）"
              style="width: 220px"
            >
              <el-option
                v-for="c in customers.filter((x) => x.parent_id === null)"
                :key="c.id"
                :label="c.name"
                :value="c.id"
              />
            </el-select>
            <el-button type="primary" @click="onSendableSearch">查询</el-button>
            <el-button @click="onSendableReset">重置</el-button>
            <span v-if="sendableTotal > 0" class="total-hint">共 {{ sendableTotal }} 条</span>
          </div>
          <ResponsiveList
            :items="sendableItems"
            :loading="sendableLoading"
            row-key="part_id"
            :empty-text="sendableError ?? '暂无符合条件的可发送零件'"
            :card-class="(row) => row.is_urgent ? 'rl-card--urgent' : ''"
            stripe
            border
            size="small"
          >
            <el-table-column prop="part_serial_no" label="序列号" min-width="100" align="center"/>
            <el-table-column prop="part_drawing_no" label="图号" min-width="120" align="center">
              <template #default="{ row }">
                <!-- 2026-07-29 PR-fix-0.2.0 批次化：行=批次，图号旁显示批次号提示 -->
                <span>{{ (row as SendableItem).part_drawing_no }}</span>
                <el-tag
                  v-if="(row as SendableItem).batch_no"
                  size="small"
                  type="info"
                  effect="plain"
                  style="margin-left: 4px"
                >
                  批次 {{ (row as SendableItem).batch_no }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="part_name" label="名称" min-width="180" show-overflow-tooltip align="center"/>
            <el-table-column prop="quantity" label="数量" min-width="80" align="right" />
            <el-table-column prop="planned_delivery_date" label="计划交期" min-width="120" align="center"/>
            <el-table-column label="源货架" min-width="80" align="center">
              <template #default="{ row }">
                <el-tag v-if="(row as SendableItem).shelf_code" type="info" size="small">
                  {{ (row as SendableItem).shelf_code }}
                </el-tag>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column label="模式" min-width="90" align="center">
              <template #default="{ row }">
                <el-tag v-if="(row as SendableItem).send_mode === 'DIRECT'" type="success" size="small">免审批</el-tag>
                <el-tag v-else type="warning" size="small">已批报价</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="customer_path" label="客户" min-width="160" show-overflow-tooltip align="center"/>
            <el-table-column label="下一道工序" min-width="140" show-overflow-tooltip align="center">
              <template #default="{ row }">{{ (row as SendableItem).next_process_name || '—' }}</template>
            </el-table-column>
            <el-table-column label="外协公司" min-width="160" show-overflow-tooltip align="center">
              <template #default="{ row }">
                <template v-if="(row as SendableItem).send_mode === 'DIRECT'">
                  {{ (row as DirectOutsourceCandidateItem).company_options.map((c) => c.name).join(' / ') || '—' }}
                </template>
                <template v-else>
                  {{ (row as ApprovedQuoteForSendItem).outsource_company_name || '—' }}
                </template>
              </template>
            </el-table-column>
            <el-table-column label="单价(元)" min-width="100" align="right">
              <template #default="{ row }">
                <template v-if="(row as SendableItem).send_mode === 'DIRECT'">—</template>
                <template v-else>{{ (row as ApprovedQuoteForSendItem).price }}</template>
              </template>
            </el-table-column>
            <el-table-column label="操作" min-width="100" fixed="right" align="center">
              <template #default="{ row }">
                <el-tooltip
                  v-if="!canSend(row as SendableItem)"
                  content="该零件当前状态 / 位置 / 工序不满足发送条件"
                  placement="top"
                >
                  <el-button size="small" disabled>发送</el-button>
                </el-tooltip>
                <el-button
                  v-else
                  size="small"
                  type="primary"
                  @click="openSend(row as SendableItem)"
                >发送</el-button>
              </template>
            </el-table-column>

            <template #card="{ row }">
              <div class="rl-card-head">
                <span class="rl-card-title">{{ (row as SendableItem).part_name || '未命名零件' }}</span>
                <el-tag v-if="(row as SendableItem).is_urgent" type="danger" size="small">加急</el-tag>
                <el-tag v-if="(row as SendableItem).send_mode === 'DIRECT'" type="success" size="small">免审批</el-tag>
                <el-tag v-else type="warning" size="small">已批报价</el-tag>
                <el-tag v-if="(row as SendableItem).source_status === 'PENDING'" type="info" size="small">起始</el-tag>
                <el-tag v-else type="primary" size="small">中间</el-tag>
              </div>
              <div class="rl-card-sub">
                图号 {{ (row as SendableItem).part_drawing_no || '—' }} · 序列号 {{ (row as SendableItem).part_serial_no || '—' }}
              </div>
              <div class="rl-kv">
                <div class="rl-kv__item">
                  <span class="rl-kv__key">数量</span>
                  <span class="rl-kv__val">{{ (row as SendableItem).quantity ?? '—' }}</span>
                </div>
                <div class="rl-kv__item">
                  <span class="rl-kv__key">计划交期</span>
                  <span class="rl-kv__val">{{ (row as SendableItem).planned_delivery_date || '—' }}</span>
                </div>
                <div class="rl-kv__item rl-kv__item--full">
                  <span class="rl-kv__key">客户</span>
                  <span class="rl-kv__val">{{ (row as SendableItem).customer_path || '—' }}</span>
                </div>
                <div class="rl-kv__item">
                  <span class="rl-kv__key">下一道工序</span>
                  <span class="rl-kv__val">{{ (row as SendableItem).next_process_name || '—' }}</span>
                </div>
                <div class="rl-kv__item">
                  <span class="rl-kv__key">单价</span>
                  <span class="rl-kv__val">{{ (row as SendableItem).send_mode === 'DIRECT' ? '—' : `${(row as ApprovedQuoteForSendItem).price} 元` }}</span>
                </div>
                <div class="rl-kv__item rl-kv__item--full">
                  <span class="rl-kv__key">外协公司</span>
                  <span class="rl-kv__val">
                    <template v-if="(row as SendableItem).send_mode === 'DIRECT'">
                      {{ (row as DirectOutsourceCandidateItem).company_options.map((c) => c.name).join(' / ') || '—' }}
                    </template>
                    <template v-else>
                      {{ (row as ApprovedQuoteForSendItem).outsource_company_name || '—' }}
                    </template>
                  </span>
                </div>
              </div>
              <div class="rl-card-actions">
                <el-tooltip
                  v-if="!canSend(row as SendableItem)"
                  content="该零件当前状态 / 位置 / 工序不满足发送条件"
                  placement="top"
                >
                  <span><el-button size="small" disabled>发送</el-button></span>
                </el-tooltip>
                <el-button
                  v-else
                  size="small"
                  type="primary"
                  @click="openSend(row as SendableItem)"
                >发送</el-button>
              </div>
            </template>
          </ResponsiveList>
          <div class="pagination">
            <el-pagination
              v-model:current-page="sendablePage"
              v-model:page-size="sendablePageSize"
              :page-sizes="[20, 50, 100]"
              :total="sendableTotal"
              :layout="paginationLayout"
              :pager-count="isMobile ? 5 : 7"
              background
              size="small"
              @current-change="refreshSendable"
              @size-change="onSendablePageSizeChange"
            />
          </div>
        </el-tab-pane>

        <!-- ====================== Tab 2: 待接收 ====================== -->
        <el-tab-pane name="receiving" label="待接收">
          <div class="filter-row">
            <el-input
              v-model="receivingFilter.keyword"
              placeholder="图号 / 名称 / 序列号"
              clearable
              style="width: 280px"
              @keyup.enter="onReceivingSearch"
            />
            <el-select
              v-model="receivingFilter.customer_id"
              clearable
              placeholder="客户（L1）"
              style="width: 220px"
            >
              <el-option
                v-for="c in customers.filter((x) => x.parent_id === null)"
                :key="c.id"
                :label="c.name"
                :value="c.id"
              />
            </el-select>
            <el-button type="primary" @click="onReceivingSearch">查询</el-button>
            <el-button @click="onReceivingReset">重置</el-button>
            <span v-if="receivingTotal > 0" class="total-hint">共 {{ receivingTotal }} 条</span>
          </div>
          <ResponsiveList
            :items="receivingItems"
            :loading="receivingLoading"
            row-key="id"
            :empty-text="receivingError ?? '暂无待接收的零件'"
            :card-class="(row) => row.is_urgent ? 'rl-card--urgent' : ''"
            stripe
            border
            size="small"
          >
            <el-table-column prop="serial_no" label="序列号" min-width="100" align="center"/>
            <el-table-column prop="drawing_no" label="图号" min-width="120" align="center"/>
            <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip align="center"/>
            <el-table-column label="当前外协公司" min-width="160" show-overflow-tooltip align="center">
              <template #default="{ row }">
                <!-- PartListItem 不含 outsource_company_name 字段（仅 PartOut 有）；
                     用 any cast 读取，service 实际会填上 current_holder_display 但
                     这里直接展示「外协中」即可。 -->
                <el-tag type="warning" size="small">外协中</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="下一道工序" min-width="160" show-overflow-tooltip align="center">
              <template #default="{ row }">
                <span class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="customer_path" label="客户" min-width="180" show-overflow-tooltip align="center"/>
            <el-table-column label="操作" min-width="100" fixed="right" align="center">
              <template #default="{ row }">
                <el-button
                  size="small"
                  type="primary"
                  @click="openReceive(row as unknown as PartListItem)"
                >接收</el-button>
              </template>
            </el-table-column>

            <template #card="{ row }">
              <div class="rl-card-head">
                <span class="rl-card-title">{{ (row as PartListItem).name }}</span>
                <el-tag type="warning" size="small">外协中</el-tag>
              </div>
              <div class="rl-card-sub">
                图号 {{ (row as PartListItem).drawing_no || '—' }} · 序列号 {{ (row as PartListItem).serial_no || '—' }}
              </div>
              <div class="rl-kv">
                <div class="rl-kv__item rl-kv__item--full">
                  <span class="rl-kv__key">客户</span>
                  <span class="rl-kv__val">{{ (row as PartListItem).customer_path || '—' }}</span>
                </div>
                <div class="rl-kv__item">
                  <span class="rl-kv__key">当前状态</span>
                  <span class="rl-kv__val">外协中</span>
                </div>
                <div class="rl-kv__item">
                  <span class="rl-kv__key">下一道工序</span>
                  <span class="rl-kv__val">—</span>
                </div>
              </div>
              <div class="rl-card-actions">
                <el-button
                  size="small"
                  type="primary"
                  @click="openReceive(row as PartListItem)"
                >接收</el-button>
              </div>
            </template>
          </ResponsiveList>
          <div class="pagination">
            <el-pagination
              v-model:current-page="receivingPage"
              v-model:page-size="receivingPageSize"
              :page-sizes="[20, 50, 100]"
              :total="receivingTotal"
              :layout="paginationLayout"
              :pager-count="isMobile ? 5 : 7"
              background
              size="small"
              @current-change="refreshReceiving"
              @size-change="onReceivingPageSizeChange"
            />
          </div>
        </el-tab-pane>

      </el-tabs>
    </el-card>

    <!-- 发送 dialog -->
    <el-dialog
      v-model="sendDialogVisible"
      title="确认发送外协"
      :width="sendDlg.width.value"
      :top="sendDlg.top.value"
    >
      <template v-if="sendTarget">
        <div v-if="sendTarget.send_mode === 'DIRECT'" style="margin-bottom: 12px;">
          <el-tag type="success" size="default">免审批，直接发送</el-tag>
          <span style="margin-left: 8px; color: var(--el-text-color-secondary);">
            无需报价，要求位于绑定了外协工序的货架（当前 C2 等）
          </span>
        </div>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="序列号">{{ sendTarget.part_serial_no }}</el-descriptions-item>
          <el-descriptions-item label="图号">{{ sendTarget.part_drawing_no }}</el-descriptions-item>
          <el-descriptions-item label="名称">{{ sendTarget.part_name }}</el-descriptions-item>
          <el-descriptions-item v-if="sendTarget.send_mode === 'APPROVAL'" label="外协公司">
            {{ sendTarget.outsource_company_name }}
          </el-descriptions-item>
          <el-descriptions-item v-else label="外协公司">
            <el-select v-model="sendSelectedCompanyId" style="width: 100%">
              <el-option
                v-for="opt in sendTarget.company_options"
                :key="opt.id"
                :label="opt.name"
                :value="opt.id"
              />
            </el-select>
          </el-descriptions-item>
          <el-descriptions-item label="外协工序">{{ sendTarget.next_process_name ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="单价">
            {{ sendTarget.send_mode === 'DIRECT' ? '—' : `${sendTarget.price} 元` }}
          </el-descriptions-item>
        </el-descriptions>
      </template>
      <template #footer>
        <el-button @click="sendDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="sendSubmitting" @click="onConfirmSend">
          确认发送
        </el-button>
      </template>
    </el-dialog>

    <!-- 接收 dialog -->
    <el-dialog
      v-model="receiveDialogVisible"
      title="接收外协件"
      :width="receiveDlg.width.value"
      :top="receiveDlg.top.value"
      :close-on-click-modal="false"
      @closed="onReceiveDialogClosed"
    >
      <el-form label-width="120px">
        <el-form-item label="接收分支">
          <el-radio-group v-model="receiveBranch">
            <el-radio value="production">进入生产货架</el-radio>
            <el-radio value="inspection">进入品检货架</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="receiveBranch === 'production' ? '生产货架' : '品检货架'" required>
          <el-select
            v-model="receiveShelf"
            :placeholder="receiveBranch === 'production' ? '生产区' : '品检区'"
            filterable
            style="width: 100%"
          >
            <el-option
              v-for="s in (receiveBranch === 'production' ? filteredProductionShelves : inspectionShelves)"
              :key="s.id"
              :label="`${s.code} — ${s.name}`"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="receiveBranch === 'production'" label="下一道工序" required>
          <el-select v-model="receiveProcess" filterable style="width: 100%">
            <el-option
              v-for="p in filteredInhouseProcesses"
              :key="p.id"
              :label="`${p.code} — ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="receiveDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="receiveSubmitting" @click="onConfirmReceive">
          确认接收（{{ receiveBranchLabel }}）
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style lang="scss" scoped>
.page {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}
.total-hint {
  font-size: 13px;
  color: var(--text-secondary);
  margin-left: auto;
}
// 2026-07-20：扫码批量发送 UI（PR-I）
.scan-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
  border-radius: 6px;
}
.queue-table {
  margin-bottom: 12px;
}
.batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  border-radius: 6px;
}
.batch-bar .batch-info {
  color: #303133;
  font-size: 13px;
}
.batch-bar .batch-info strong {
  color: #67c23a;
  font-weight: 600;
  margin: 0 2px;
}
.muted {
  color: var(--text-secondary);
}
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;

  @include until(sm) {
    justify-content: center;
  }
}
:deep(.el-tabs__content) {
  overflow: visible;
}
:deep(.el-tab-pane) {
  padding: 12px 0 0 0;
}
</style>
