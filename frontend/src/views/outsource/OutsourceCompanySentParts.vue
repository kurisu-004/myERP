<!-- 外协对账一览（2026-07-28 新增）

按外协公司聚合 SENT_TO_OUTSOURCE 事件，列出所有送给该公司的零件 + 当前状态；
文员拿这个表与外协公司发来的对账单核对。
-->
<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import ResponsiveList from '@/components/ResponsiveList.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { listCompanySentParts, getOutsourceCompany } from '@/api/outsource'
import type { OutsourceSentPartItem, OutsourceCompany } from '@/types/outsource'

const route = useRoute()
const router = useRouter()
const { isMobile } = useBreakpoint()

const companyId = computed(() => String(route.params.id ?? ''))
const company = ref<OutsourceCompany | null>(null)
const items = ref<OutsourceSentPartItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)
const filter = reactive({
  keyword: '',
  sent_from: '' as string,  // ISO datetime-local string
  sent_to: '' as string,
})
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

async function loadCompany(): Promise<void> {
  if (!companyId.value) return
  try {
    company.value = await getOutsourceCompany(companyId.value)
  } catch (e) {
    ElMessage.error(`外协公司加载失败：${(e as Error).message}`)
  }
}

async function loadList(): Promise<void> {
  if (!companyId.value) return
  loading.value = true
  error.value = null
  try {
    const r = await listCompanySentParts(companyId.value, {
      keyword: filter.keyword || undefined,
      sent_from: filter.sent_from || undefined,
      sent_to: filter.sent_to || undefined,
      limit: 50, offset: 0,
    })
    items.value = r.items
    total.value = r.total
  } catch (e) {
    items.value = []
    total.value = 0
    error.value = (e as Error).message ?? '加载对账列表失败'
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

function onReset(): void {
  filter.keyword = ''
  filter.sent_from = ''
  filter.sent_to = ''
  void loadList()
}

function onBack(): void {
  void router.push('/outsource/companies')
}

onMounted(() => {
  void loadCompany()
  void loadList()
})

watch(companyId, () => {
  void loadCompany()
  void loadList()
})
</script>

<template>
  <div class="outsource-billing">
    <el-card shadow="never" class="filter-card">
      <div class="header-row">
        <h2 style="margin: 0; font-size: 16px;">
          外协对账：{{ company?.name ?? '...' }}
        </h2>
        <el-button size="small" @click="onBack">返回公司列表</el-button>
      </div>
      <el-form inline>
        <el-form-item label="关键字">
          <el-input v-model="filter.keyword" placeholder="图号 / 名称" clearable style="width: 160px" />
        </el-form-item>
        <el-form-item label="发送时间">
          <el-date-picker
            v-model="filter.sent_from"
            type="datetime"
            placeholder="起点"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 200px"
          />
          <span style="margin: 0 8px;">~</span>
          <el-date-picker
            v-model="filter.sent_to"
            type="datetime"
            placeholder="终点"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 200px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="loadList">查询</el-button>
          <el-button @click="onReset">重置</el-button>
          <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
        </el-form-item>
      </el-form>
    </el-card>

    <ResponsiveList
      :items="items"
      :loading="loading"
      row-key="part_id"
      :empty-text="error ?? '暂无对账记录'"
      stripe
      border
      size="small"
    >
      <el-table-column prop="part_serial_no" label="序列号" min-width="100" align="center"/>
      <el-table-column prop="part_drawing_no" label="图号" min-width="120" align="center"/>
      <el-table-column prop="part_name" label="名称" min-width="160" show-overflow-tooltip align="center"/>
      <el-table-column prop="quantity" label="数量" min-width="80" align="right" />
      <el-table-column label="单价(元)" min-width="100" align="right">
        <template #default="{ row }">
          {{ (row as OutsourceSentPartItem).unit_price ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column label="发送时间" min-width="160" align="center">
        <template #default="{ row }">
          {{ new Date((row as OutsourceSentPartItem).sent_at).toLocaleString() }}
        </template>
      </el-table-column>
      <el-table-column label="回收时间" min-width="160" align="center">
        <template #default="{ row }">
          <template v-if="(row as OutsourceSentPartItem).received_at">
            {{ new Date((row as OutsourceSentPartItem).received_at!).toLocaleString() }}
          </template>
          <span v-else style="color: var(--el-color-warning);">未回收</span>
        </template>
      </el-table-column>
      <el-table-column label="当前状态" min-width="120" align="center">
        <template #default="{ row }">
          {{ (row as OutsourceSentPartItem).current_status }}
        </template>
      </el-table-column>
      <el-table-column label="对账" min-width="80" align="center">
        <template #default="{ row }">
          <el-tag
            v-if="(row as OutsourceSentPartItem).is_billed"
            type="success"
            size="small"
          >已对</el-tag>
          <el-tag v-else type="info" size="small">未对</el-tag>
        </template>
      </el-table-column>

      <template #card="{ row }">
        <div class="rl-card-head">
          <span class="rl-card-title">{{ (row as OutsourceSentPartItem).part_name || '未命名零件' }}</span>
          <el-tag
            v-if="(row as OutsourceSentPartItem).is_billed"
            type="success"
            size="small"
          >已对账</el-tag>
          <el-tag v-else type="info" size="small">未对账</el-tag>
        </div>
        <div class="rl-card-sub">
          图号 {{ (row as OutsourceSentPartItem).part_drawing_no || '—' }} ·
          序列号 {{ (row as OutsourceSentPartItem).part_serial_no || '—' }}
        </div>
        <div class="rl-kv">
          <div class="rl-kv__item">
            <span class="rl-kv__key">数量</span>
            <span class="rl-kv__val">{{ (row as OutsourceSentPartItem).quantity }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">单价</span>
            <span class="rl-kv__val">{{ (row as OutsourceSentPartItem).unit_price ?? '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">发送时间</span>
            <span class="rl-kv__val">
              {{ new Date((row as OutsourceSentPartItem).sent_at).toLocaleString() }}
            </span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">回收时间</span>
            <span class="rl-kv__val">
              <template v-if="(row as OutsourceSentPartItem).received_at">
                {{ new Date((row as OutsourceSentPartItem).received_at!).toLocaleString() }}
              </template>
              <span v-else style="color: var(--el-color-warning);">未回收</span>
            </span>
          </div>
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">当前状态</span>
            <span class="rl-kv__val">{{ (row as OutsourceSentPartItem).current_status }}</span>
          </div>
        </div>
      </template>
    </ResponsiveList>
  </div>
</template>

<style lang="scss" scoped>
.outsource-billing {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.total-hint {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>