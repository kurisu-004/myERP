<!-- 外协发送列表页（2026-07-16 新增）

列出至少有一条 APPROVED 报价，且零件状态满足「PENDING 或
(IN_PROCESS + PRODUCTION_SHELF + 下一道=OUTSOURCE)」的零件。
发送按钮按前端 eligibility 规则激活 / 禁用；点击后弹 dialog 确认 → 调
sendPartToOutsource。复用 PartDetail.vue 的发送 dialog 形态。
-->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listApprovedForSend } from '@/api/outsource'
import { listCustomers, type Customer } from '@/api/customer'
import { sendToOutsource as sendPartToOutsource, type SendToOutsourcePayload } from '@/api/parts'
import type { ApprovedQuoteForSendItem } from '@/types/outsource'

const loading = ref(false)
const items = ref<ApprovedQuoteForSendItem[]>([])
const total = ref(0)
const query = reactive({
  keyword: '',
  customer_id: '',
  limit: 20,
  offset: 0,
})
const customers = ref<Customer[]>([])
const dialogOpen = ref(false)
const target = ref<ApprovedQuoteForSendItem | null>(null)
const submitting = ref(false)

async function refresh(): Promise<void> {
  loading.value = true
  try {
    const r = await listApprovedForSend({
      keyword: query.keyword || undefined,
      customer_id: query.customer_id || undefined,
      limit: query.limit,
      offset: query.offset,
    })
    items.value = r.items
    total.value = r.total
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载列表失败')
  } finally {
    loading.value = false
  }
}

/** 前端按钮 enabled 规则（与服务端 send_to_outsource 防御闸一致）。 */
function canSend(item: ApprovedQuoteForSendItem): boolean {
  if (!item.next_process_id) return false
  // 服务端返回的项都已经 satisfy；这里做 UI 微反馈。
  return item.status_label === 'sendable'
}

function openSend(item: ApprovedQuoteForSendItem): void {
  if (!canSend(item)) {
    ElMessage.warning('该零件当前状态不满足发送条件')
    return
  }
  target.value = item
  dialogOpen.value = true
}

async function onConfirmSend(): Promise<void> {
  if (!target.value) return
  try {
    await ElMessageBox.confirm(
      `确认把「${target.value.part_drawing_no}」发送到「${target.value.outsource_company_name}」？`,
      '发送外协',
      { type: 'warning' },
    )
  } catch {
    return
  }
  submitting.value = true
  try {
    const payload: SendToOutsourcePayload = {
      outsource_company_id: target.value.outsource_company_id,
      next_process_id: target.value.process_id,
    }
    await sendPartToOutsource(target.value.part_id, payload)
    ElMessage.success('已发送至外协')
    dialogOpen.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '发送失败')
  } finally {
    submitting.value = false
  }
}

async function loadLookups(): Promise<void> {
  try {
    customers.value = await listCustomers()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '客户列表加载失败')
  }
}

onMounted(async () => {
  await loadLookups()
  await refresh()
})

function onSearch(): void {
  query.offset = 0
  void refresh()
}
function onReset(): void {
  query.keyword = ''
  query.customer_id = ''
  query.offset = 0
  void refresh()
}
</script>

<template>
  <div class="page">
    <el-card class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="query.keyword"
          placeholder="图号 / 名称 / 序列号"
          clearable
          style="width: 280px"
          @keyup.enter="onSearch"
        />
        <el-select
          v-model="query.customer_id"
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
        <el-button type="primary" @click="onSearch">查询</el-button>
        <el-button @click="onReset">重置</el-button>
      </div>
    </el-card>

    <el-card>
      <el-table v-loading="loading" :data="items" stripe border>
        <el-table-column prop="part_serial_no" label="序列号" width="100" />
        <el-table-column prop="part_drawing_no" label="图号" width="120" />
        <el-table-column prop="part_name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="quantity" label="数量" width="80" align="right" />
        <el-table-column label="计划交期" width="120">
          <template #default="{ row }">{{ row.planned_delivery_date }}</template>
        </el-table-column>
        <el-table-column label="加急" width="60" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_urgent" type="danger" size="small">加急</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="customer_path" label="客户" min-width="160" show-overflow-tooltip />
        <el-table-column label="下一道工序" width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.next_process_name || '—' }}</template>
        </el-table-column>
        <el-table-column prop="outsource_company_name" label="外协公司" width="160" show-overflow-tooltip />
        <el-table-column label="单价(元)" width="100" align="right">
          <template #default="{ row }">{{ row.price }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-tooltip
              v-if="!canSend(row as unknown as ApprovedQuoteForSendItem)"
              content="该零件当前状态 / 位置 / 工序不满足发送条件（仅 PENDING 或 IN_PROCESS + PRODUCTION_SHELF + 下一道=OUTSOURCE）"
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
        v-model:current-page="query.offset"
        :total="total"
        :page-size="query.limit"
        layout="total, prev, pager, next, jumper"
        @current-change="refresh"
      />
    </el-card>

    <el-dialog v-model="dialogOpen" title="确认发送外协" width="520">
      <el-descriptions v-if="target" :column="1" border>
        <el-descriptions-item label="序列号">{{ target.part_serial_no }}</el-descriptions-item>
        <el-descriptions-item label="图号">{{ target.part_drawing_no }}</el-descriptions-item>
        <el-descriptions-item label="名称">{{ target.part_name }}</el-descriptions-item>
        <el-descriptions-item label="外协公司">{{ target.outsource_company_name }}</el-descriptions-item>
        <el-descriptions-item label="外协工序">{{ target.process_name }}</el-descriptions-item>
        <el-descriptions-item label="单价">{{ target.price }} 元</el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="onConfirmSend">
          确认发送
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
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
}
</style>
