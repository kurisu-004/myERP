<!-- 外协发送/接收页（2026-07-16 新增；合并原 OutsourceSendList + OutsourceReceiveList）

3 个 tab：
  - 可发送（默认）：列出至少有一条 APPROVED 报价的「可发送」零件
  - 待接收：列出 status=OUTSOURCE 的零件，等回收
  - 已接收历史：列出 status=IN_PROCESS/INSPECTION/READY_TO_SHIP/DELIVERED/COMPLETED
    的全部零件，前端按 part.events 是否含 SENT_TO_OUTSOURCE 过滤「曾外协过」
    （更稳的方案是后端新增 GET /parts?has_outsource_history=true，本期先用前端 events 过滤）

URL ?tab=sendable|receiving|received 记忆上次选择；初次进入默认 可发送。
-->
<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listApprovedForSend } from '@/api/outsource'
import { listCustomers, type Customer } from '@/api/customer'
import { listShelves } from '@/api/shelves'
import type { Shelf as ShelfItem } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import type { Process } from '@/types/process'
import {
  receiveFromOutsource,
  receiveFromOutsourceToInspection,
  sendToOutsource as sendPartToOutsource,
  type SendToOutsourcePayload,
  listParts,
} from '@/api/parts'
import type { ApprovedQuoteForSendItem } from '@/types/outsource'
import type { PartListItem } from '@/types/parts'

type TabName = 'sendable' | 'receiving' | 'received'

const route = useRoute()
const router = useRouter()

// ============================================================
// Tab 状态（URL ?tab= 同步）
// ============================================================
function readTabFromQuery(): TabName {
  const t = route.query.tab
  if (t === 'receiving' || t === 'received') return t
  return 'sendable'
}
const activeTab = ref<TabName>(readTabFromQuery())

watch(() => route.query.tab, (q) => {
  if (q === 'sendable' || q === 'receiving' || q === 'received') {
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
// Tab 1：可发送
// ============================================================
const sendableItems = ref<ApprovedQuoteForSendItem[]>([])
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
    const r = await listApprovedForSend({
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
const sendTarget = ref<ApprovedQuoteForSendItem | null>(null)
const sendSubmitting = ref(false)

function canSend(item: ApprovedQuoteForSendItem): boolean {
  // 服务端返回的项都已经 satisfy；这里走 status_label 防越界
  return item.status_label === 'sendable'
}
function openSend(item: ApprovedQuoteForSendItem): void {
  if (!canSend(item)) {
    ElMessage.warning('该零件当前状态不满足发送条件')
    return
  }
  sendTarget.value = item
  sendDialogVisible.value = true
}
async function onConfirmSend(): Promise<void> {
  if (!sendTarget.value) return
  try {
    await ElMessageBox.confirm(
      `确认把「${sendTarget.value.part_drawing_no}」发送到「${sendTarget.value.outsource_company_name}」？`,
      '发送外协',
      { type: 'warning', confirmButtonText: '确认发送', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  sendSubmitting.value = true
  try {
    const payload: SendToOutsourcePayload = {
      outsource_company_id: sendTarget.value.outsource_company_id,
      next_process_id: sendTarget.value.process_id,
    }
    await sendPartToOutsource(sendTarget.value.part_id, payload)
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
    // 接收成功后该零件应出现在「已接收历史」tab
    void refreshReceived()
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
// Tab 3：已接收历史
// ============================================================
const receivedItems = ref<PartListItem[]>([])
const receivedTotal = ref(0)
const receivedLoading = ref(false)
const receivedError = ref<string | null>(null)
const receivedFilter = reactive({ keyword: '', customer_id: '' })
const receivedPage = ref(1)
const receivedPageSize = ref(20)

// 「已接收历史」=「曾外协过」+ 已离开 OUTSOURCE（不等于 PENDING/OUTSOURCE/PROGRAMMING/CANCELLED）
// 与 refreshReceiving 对称：一次 listParts + 服务端分页，无 N+1。
const RECEIVED_STATUSES = [
  'IN_PROCESS',
  'INSPECTION',
  'READY_TO_SHIP',
  'DELIVERED',
  'COMPLETED',
] as const

async function refreshReceived(): Promise<void> {
  receivedLoading.value = true
  receivedError.value = null
  try {
    const r = await listParts({
      statuses: [...RECEIVED_STATUSES],
      keyword: receivedFilter.keyword || undefined,
      customer_id: receivedFilter.customer_id || undefined,
      has_outsource_history: true,
      limit: receivedPageSize.value,
      offset: (receivedPage.value - 1) * receivedPageSize.value,
    })
    receivedItems.value = r.items
    receivedTotal.value = r.total
  } catch (e) {
    receivedItems.value = []
    receivedTotal.value = 0
    receivedError.value = (e as Error).message ?? '加载已接收历史失败'
    ElMessage.error(receivedError.value)
  } finally {
    receivedLoading.value = false
  }
}

function onReceivedSearch(): void {
  receivedPage.value = 1
  void refreshReceived()
}
function onReceivedReset(): void {
  receivedFilter.keyword = ''
  receivedFilter.customer_id = ''
  receivedPage.value = 1
  void refreshReceived()
}
function onReceivedPageSizeChange(size: number): void {
  receivedPageSize.value = size
  receivedPage.value = 1
  void refreshReceived()
}

function goPartDetail(row: PartListItem): void {
  router.push(`/parts/${row.id}`)
}

// ============================================================
// 初始化
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
  else if (t === 'received') void refreshReceived()
})
</script>

<template>
  <div class="page">
    <el-card shadow="never">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <!-- ====================== Tab 1: 可发送 ====================== -->
        <el-tab-pane name="sendable" label="可发送">
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
          <el-table
            v-loading="sendableLoading"
            :data="sendableItems"
            stripe
            border
            size="small"
            :empty-text="sendableError ?? '暂无符合条件的可发送零件'"
          >
            <el-table-column prop="part_serial_no" label="序列号" width="100" />
            <el-table-column prop="part_drawing_no" label="图号" width="120" />
            <el-table-column prop="part_name" label="名称" min-width="180" show-overflow-tooltip />
            <el-table-column prop="quantity" label="数量" width="80" align="right" />
            <el-table-column prop="planned_delivery_date" label="计划交期" width="120" />
            <el-table-column label="加急" width="60" align="center">
              <template #default="{ row }">
                <el-tag v-if="(row as ApprovedQuoteForSendItem).is_urgent" type="danger" size="small">加急</el-tag>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column prop="customer_path" label="客户" min-width="160" show-overflow-tooltip />
            <el-table-column label="下一道工序" width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ (row as ApprovedQuoteForSendItem).next_process_name || '—' }}</template>
            </el-table-column>
            <el-table-column prop="outsource_company_name" label="外协公司" width="160" show-overflow-tooltip />
            <el-table-column label="单价(元)" width="100" align="right">
              <template #default="{ row }">{{ (row as ApprovedQuoteForSendItem).price }}</template>
            </el-table-column>
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="{ row }">
                <el-tooltip
                  v-if="!canSend(row as unknown as ApprovedQuoteForSendItem)"
                  content="该零件当前状态 / 位置 / 工序不满足发送条件"
                  placement="top"
                >
                  <el-button size="small" disabled>发送</el-button>
                </el-tooltip>
                <el-button
                  v-else
                  size="small"
                  type="primary"
                  @click="openSend(row as unknown as ApprovedQuoteForSendItem)"
                >发送</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-model:current-page="sendablePage"
            v-model:page-size="sendablePageSize"
            :page-sizes="[20, 50, 100]"
            :total="sendableTotal"
            layout="total, sizes, prev, pager, next, jumper"
            background
            size="small"
            @current-change="refreshSendable"
            @size-change="onSendablePageSizeChange"
          />
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
          <el-table
            v-loading="receivingLoading"
            :data="receivingItems"
            stripe
            border
            size="small"
            :empty-text="receivingError ?? '暂无待接收的零件'"
          >
            <el-table-column prop="serial_no" label="序列号" width="100" />
            <el-table-column prop="drawing_no" label="图号" width="120" />
            <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
            <el-table-column label="当前外协公司" width="160" show-overflow-tooltip>
              <template #default="{ row }">
                <!-- PartListItem 不含 outsource_company_name 字段（仅 PartOut 有）；
                     用 any cast 读取，service 实际会填上 current_holder_display 但
                     这里直接展示「外协中」即可。 -->
                <el-tag type="warning" size="small">外协中</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="下一道工序" width="160" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="customer_path" label="客户" min-width="180" show-overflow-tooltip />
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="{ row }">
                <el-button
                  size="small"
                  type="primary"
                  @click="openReceive(row as unknown as PartListItem)"
                >接收</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-model:current-page="receivingPage"
            v-model:page-size="receivingPageSize"
            :page-sizes="[20, 50, 100]"
            :total="receivingTotal"
            layout="total, sizes, prev, pager, next, jumper"
            background
            size="small"
            @current-change="refreshReceiving"
            @size-change="onReceivingPageSizeChange"
          />
        </el-tab-pane>

        <!-- ====================== Tab 3: 已接收历史 ====================== -->
        <el-tab-pane name="received" label="已接收历史">
          <div class="filter-row">
            <el-input
              v-model="receivedFilter.keyword"
              placeholder="图号 / 名称 / 序列号"
              clearable
              style="width: 280px"
              @keyup.enter="onReceivedSearch"
            />
            <el-select
              v-model="receivedFilter.customer_id"
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
            <el-button type="primary" @click="onReceivedSearch">查询</el-button>
            <el-button @click="onReceivedReset">重置</el-button>
            <span v-if="receivedTotal > 0" class="total-hint">共 {{ receivedTotal }} 条</span>
          </div>
          <el-table
            v-loading="receivedLoading"
            :data="receivedItems"
            stripe
            border
            size="small"
            :empty-text="receivedError ?? '暂无已接收的零件'"
          >
            <el-table-column prop="serial_no" label="序列号" width="100" />
            <el-table-column prop="drawing_no" label="图号" width="120" />
            <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ (row as PartListItem).status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="customer_path" label="客户" min-width="180" show-overflow-tooltip />
            <el-table-column prop="planned_delivery_date" label="计划交期" width="120" />
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  size="small"
                  @click="goPartDetail(row as unknown as PartListItem)"
                >详情</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-model:current-page="receivedPage"
            v-model:page-size="receivedPageSize"
            :page-sizes="[20, 50, 100]"
            :total="receivedTotal"
            layout="total, sizes, prev, pager, next, jumper"
            background
            size="small"
            @current-change="refreshReceived"
            @size-change="onReceivedPageSizeChange"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 发送 dialog -->
    <el-dialog v-model="sendDialogVisible" title="确认发送外协" width="520">
      <el-descriptions v-if="sendTarget" :column="1" border>
        <el-descriptions-item label="序列号">{{ sendTarget.part_serial_no }}</el-descriptions-item>
        <el-descriptions-item label="图号">{{ sendTarget.part_drawing_no }}</el-descriptions-item>
        <el-descriptions-item label="名称">{{ sendTarget.part_name }}</el-descriptions-item>
        <el-descriptions-item label="外协公司">{{ sendTarget.outsource_company_name }}</el-descriptions-item>
        <el-descriptions-item label="外协工序">{{ sendTarget.process_name }}</el-descriptions-item>
        <el-descriptions-item label="单价">{{ sendTarget.price }} 元</el-descriptions-item>
      </el-descriptions>
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
      width="560"
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
            :placeholder="receiveBranch === 'production' ? '选 PRODUCTION 区' : '选 INSPECTION 区'"
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
        <el-form-item v-if="receiveBranch === 'production'" label="下一道 INHOUSE" required>
          <el-select v-model="receiveProcess" filterable style="width: 100%">
            <el-option
              v-for="p in filteredInhouseProcesses"
              :key="p.id"
              :label="`${p.code} — ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="receiveBranch === 'inspection'">
          <el-checkbox v-model="autoPass">
            通过品检后自动进入待送货
            <small>（勾选后连发 pass_inspection：OUTSOURCE → INSPECTION → READY_TO_SHIP）</small>
          </el-checkbox>
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
:deep(.el-tabs__content) {
  overflow: visible;
}
:deep(.el-tab-pane) {
  padding: 12px 0 0 0;
}
</style>
