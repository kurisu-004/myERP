<!--
  InspectionPending.vue — 品检待办一览（INSPECTION 状态的零件）

  - 顶部：图号/名称搜索 + 手动刷新 + 自动刷新（每 5min）+ 共 N 条
  - 每行两个动作：「品检通过」「指定工序」
  - 指定工序 → 弹出 el-dialog 选择目标 PRODUCTION 货架（el-radio-group）
  - 加急行整行红底 #fde2e2（与 PartsList 同款）
-->
<template>
  <div class="inspection-pending">
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="search.keyword"
          placeholder="图号 / 名称（前缀搜索）"
          clearable
          style="width: 260px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-input
          v-model="search.serialNo"
          placeholder="序列号"
          clearable
          style="width: 180px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-date-picker
          v-model="plannedDateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="~"
          start-placeholder="计划交期起点"
          end-placeholder="计划交期终点"
          unlink-panels
          clearable
          style="width: 280px"
          @change="onSearch"
        />

        <el-button @click="onSearch">
          <el-icon><RefreshLeft /></el-icon>
          <span>刷新</span>
        </el-button>

        <el-checkbox v-model="autoRefresh" @change="onAutoRefreshToggle">
          自动刷新（5min）
        </el-checkbox>

        <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
        <el-tag v-else-if="!loading" type="info" effect="plain" size="small">
          当前无待品检零件
        </el-tag>
      </div>
    </el-card>

    <ResponsiveList
      :items="items"
      :loading="loading"
      row-key="id"
      :empty-text="emptyText"
      :card-class="(row) => (row.is_urgent ? 'rl-card--urgent' : '')"
      stripe
      border
      size="small"
      :row-class-name="rowClassName"
    >
      <template #toolbar>
        <ColumnVisibilityPopover
          :defs="columnDefs"
          :model-value="columnVisibility.currentMap" @update:model-value="columnVisibility.update"
          @reset="columnVisibility.showAll"
        />
      </template>
      <el-table-column
        v-if="columnVisibility.isVisible('serial_no')"
        prop="serial_no"
        label="序列号"
        min-width="110"
        fixed="left"
        show-overflow-tooltip align="center">
        <template #default="{ row }">
          <span :class="{ muted: !row.serial_no }">{{ row.serial_no || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('drawing_no')"
        prop="drawing_no"
        label="图号"
        min-width="130"
        fixed="left"
        show-overflow-tooltip align="center"/>

      <el-table-column
        v-if="columnVisibility.isVisible('name')"
        prop="name"
        label="名称"
        min-width="200"
        show-overflow-tooltip align="center">
        <template #default="{ row }">
          <router-link :to="`/parts/${row.id}`" class="name-link">
            {{ row.name }}
          </router-link>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('batch_label')"
        label="批次" min-width="100" align="center"
      >
        <template #default="{ row }">
          <span class="batch-label">{{ (row as RowState).batch_label || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('quantity')"
        prop="quantity" label="批次量" min-width="80" align="right" />

      <el-table-column
        v-if="columnVisibility.isVisible('planned_delivery_date')"
        prop="planned_delivery_date"
        label="计划交期"
        min-width="120" align="center"/>

      <el-table-column
        v-if="columnVisibility.isVisible('system_delivery_date')"
        prop="system_delivery_date"
        label="系统交期"
        min-width="120"
        align="center"
      >
        <template #default="{ row }">
          <span :class="{ muted: !row.system_delivery_date }">
            {{ row.system_delivery_date || '—' }}
          </span>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('customer')"
        label="客户" min-width="180" show-overflow-tooltip align="center"
      >
        <template #default="{ row }">
          <span v-if="row.customer_path">{{ row.customer_path }}</span>
          <span v-else-if="row.customer_name" class="muted">{{ row.customer_name }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('shelf_code')"
        label="品检货架" min-width="150" show-overflow-tooltip align="center"
      >
        <template #default="{ row }">
          <span v-if="row.shelf_code">品检 {{ row.shelf_code }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column label="操作" min-width="220" fixed="right" align="center">
        <template #default="{ row }">
          <el-button
            link
            type="success"
            size="small"
            :loading="row._passing"
            @click="onPass(row as RowState)"
          >品检通过</el-button>
          <el-button
            link
            type="warning"
            size="small"
            @click="openFailDialog(row as RowState)"
          >指定工序</el-button>
          <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">详情</el-button>
        </template>
      </el-table-column>

      <!-- 手机卡片 -->
      <template #card="{ row }">
        <div class="rl-card-head">
          <router-link :to="`/parts/${row.id}`" class="rl-card-title name-link">
            {{ row.name }}
          </router-link>
        </div>
        <div class="rl-card-sub">
          图号 {{ row.drawing_no || '—' }} · 序列号 {{ row.serial_no || '—' }}
        </div>
        <div class="rl-kv">
          <div class="rl-kv__item">
            <span class="rl-kv__key">批次</span>
            <span class="rl-kv__val">{{ (row as RowState).batch_label || '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">批次量</span>
            <span class="rl-kv__val">{{ row.quantity }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">计划交期</span>
            <span class="rl-kv__val">{{ row.planned_delivery_date || '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">系统交期</span>
            <span class="rl-kv__val">{{ row.system_delivery_date || '—' }}</span>
          </div>
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">客户</span>
            <span class="rl-kv__val">
              {{ row.customer_path || row.customer_name || '—' }}
            </span>
          </div>
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">品检货架</span>
            <span class="rl-kv__val">
              {{ row.shelf_code ? `品检 ${row.shelf_code}` : '—' }}
            </span>
          </div>
        </div>
        <div class="rl-card-actions">
          <el-button
            link
            type="success"
            size="small"
            :loading="row._passing"
            @click="onPass(row as RowState)"
          >品检通过</el-button>
          <el-button
            link
            type="warning"
            size="small"
            @click="openFailDialog(row as RowState)"
          >指定工序</el-button>
          <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">详情</el-button>
        </div>
      </template>
    </ResponsiveList>

    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[20, 50, 100]"
        :total="total"
        :layout="paginationLayout"
        :pager-count="isMobile ? 5 : 7"
        background
        size="small"
        @current-change="fetchList"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- 品检通过对话框（2026-07-29：带数量；部分通过后端先拆再过） -->
    <el-dialog
      v-model="passDialogVisible"
      title="品检通过"
      :width="passDlg.width.value"
      :fullscreen="passDlg.fullscreen.value"
      :close-on-click-modal="false"
      @closed="onPassDialogClosed"
    >
      <div v-if="passTarget" class="fail-summary">
        <div><strong>流水号：</strong>{{ passTarget.serial_no || '—' }}</div>
        <div><strong>批次：</strong>{{ passTarget.batch_label || '—' }}</div>
        <div><strong>名称：</strong>{{ passTarget.name }}</div>
      </div>
      <el-form label-width="96px" style="margin-top: 12px">
        <el-form-item label="通过数量" required>
          <el-input-number
            v-model="passQty"
            :min="1"
            :max="passTarget?.quantity"
            :precision="0"
            style="width: 160px"
          />
          <span v-if="passTarget" class="muted" style="margin-left: 8px">
            / {{ passTarget.quantity }}
          </span>
        </el-form-item>
        <el-alert
          v-if="passTarget && passQty && passQty < passTarget.quantity"
          type="info"
          :closable="false"
          :title="`部分通过：剩余 ${passTarget.quantity - passQty} 件将留在品检状态`"
          show-icon
        />
      </el-form>
      <template #footer>
        <el-button @click="passDialogVisible = false">取消</el-button>
        <el-button
          type="success"
          :loading="!!passTarget?._passing"
          :disabled="!passQty"
          @click="onPassConfirm"
        >确认通过</el-button>
      </template>
    </el-dialog>

    <!-- 指定工序对话框：先选下一道工序，再选目标生产货架（按 shelf↔process 映射过滤） -->
    <el-dialog
      v-model="failDialogVisible"
      title="指定工序 — 选择下一道工序 + 目标生产货架"
      :width="failDlg.width.value"
      :top="failDlg.top.value"
      :fullscreen="failDlg.fullscreen.value"
      :close-on-click-modal="false"
      @closed="onFailDialogClosed"
    >
      <div v-if="failTarget" class="fail-summary">
        <div><strong>流水号：</strong>{{ failTarget.serial_no || '—' }}</div>
        <div><strong>批次：</strong>{{ failTarget.batch_label || '—' }}</div>
        <div><strong>图号：</strong>{{ failTarget.drawing_no }}</div>
        <div><strong>名称：</strong>{{ failTarget.name }}</div>
      </div>

      <el-form label-width="96px" style="margin-top: 12px">
        <el-form-item label="数量" required>
          <el-input-number
            v-model="failQty"
            :min="1"
            :max="failTarget?.quantity"
            :precision="0"
            style="width: 160px"
          />
          <span v-if="failTarget" class="muted" style="margin-left: 8px">
            / {{ failTarget.quantity }}
          </span>
        </el-form-item>

        <el-form-item label="下一道工序" required>
          <el-select
            v-model="failProcessId"
            placeholder="请先选择下一道工序"
            filterable
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="p in filteredProcesses"
              :key="p.id"
              :value="String(p.id)"
              :label="`${p.code} — ${p.name}`"
            >
              {{ p.code }} — {{ p.name }}
              <el-tag v-if="p.category === 'OUTSOURCE'" type="warning" size="small" effect="plain" class="opt-tag">
                外协
              </el-tag>
            </el-option>
          </el-select>
        </el-form-item>

        <el-form-item label="目标生产货架" required>
          <el-select
            v-model="failShelfId"
            placeholder="先选工序；货架候选按映射过滤"
            filterable
            clearable
            style="width: 100%"
            :disabled="!failProcessId"
          >
            <el-option
              v-for="s in filteredProductionShelves"
              :key="s.id"
              :value="String(s.id)"
              :label="`${s.code} — ${s.name}`"
              :disabled="!s.is_active"
            >
              {{ s.code }} — {{ s.name }}
              <span v-if="!s.is_active" class="muted">（已停用）</span>
            </el-option>
            <template #empty>
              <span class="muted">
                {{
                  failProcessId
                    ? '当前工序未映射到任何生产货架，请先在「货架管理 → 工序映射」配置'
                    : '请先选择下一道工序'
                }}
              </span>
            </template>
          </el-select>
        </el-form-item>

        <el-form-item label="品检备注">
          <el-input
            v-model="failNote"
            type="textarea"
            :rows="3"
            :maxlength="500"
            show-word-limit
            placeholder="不合格原因 / 返修要点（写入事件历史，工人领取时可见）"
          />
        </el-form-item>

        <el-alert
          type="info"
          :closable="false"
          title="指定工序后零件回到「在生产货架上」状态，下一道工序与备注已写入事件历史；工人领取时可在卡片上看到备注。"
          show-icon
        />
      </el-form>

      <template #footer>
        <el-button @click="failDialogVisible = false">取消</el-button>
        <el-button
          type="warning"
          :loading="failSubmitting"
          :disabled="!failProcessId || !failShelfId"
          @click="onFailConfirm"
        >确认指定工序</el-button>
      </template>
    </el-dialog>

    <!-- 2026-08-04：扫码命中 INSPECTION 行的二选一对话框（点按钮复用原 pass/fail dialog） -->
    <el-dialog
      v-model="scanChooserOpen"
      title="扫码命中 - 选择动作"
      width="420"
      :close-on-click-modal="false"
      append-to-body
    >
      <div v-if="scanChooserRow" class="fail-summary">
        <div><strong>流水号：</strong>{{ scanChooserRow.serial_no || '—' }}</div>
        <div><strong>批次：</strong>{{ scanChooserRow.batch_label || '—' }}</div>
        <div><strong>图号：</strong>{{ scanChooserRow.drawing_no }}</div>
        <div><strong>名称：</strong>{{ scanChooserRow.name }}</div>
        <div><strong>数量：</strong>{{ scanChooserRow.quantity }}</div>
      </div>
      <template #footer>
        <el-button @click="scanChooserOpen = false">取消</el-button>
        <el-button type="warning" @click="onScanChooserFail">指定下一工序</el-button>
        <el-button type="success" @click="onScanChooserPass">品检通过</el-button>
      </template>
    </el-dialog>

    <!-- 2026-08-12 PR-I-scan-inspect：扫码快捷品检弹窗（PENDING / PROGRAMMING / IN_PROCESS+ON_SHELF → INSPECTION → PASS/FAIL） -->
    <el-dialog
      v-model="scanInspectDialogVisible"
      title="扫码快捷品检 — 选择通过 / 打回"
      :width="scanInspectDlg.width.value"
      :top="scanInspectDlg.top.value"
      :fullscreen="scanInspectDlg.fullscreen.value"
      :close-on-click-modal="false"
      append-to-body
      @closed="onScanInspectDialogClosed"
    >
      <div v-if="scanInspectRow" class="fail-summary">
        <div><strong>流水号：</strong>{{ scanInspectRow.serial_no || '—' }}</div>
        <div><strong>批次：</strong>{{ scanInspectRow.batch_label || '—' }}</div>
        <div><strong>图号：</strong>{{ scanInspectRow.drawing_no }}</div>
        <div><strong>名称：</strong>{{ scanInspectRow.name }}</div>
        <div>
          <strong>当前状态：</strong>
          <el-tag size="small" :type="scanInspectRow.status === 'PENDING' ? 'info' : 'primary'">
            {{ scanInspectRow.status === 'PENDING' ? '待下发' : scanInspectRow.status === 'PROGRAMMING' ? '编程中' : '生产中' }}
          </el-tag>
        </div>
      </div>

      <el-form label-width="96px" style="margin-top: 12px">
        <el-form-item label="品检架" required>
          <el-select
            v-model="scanInspectShelfId"
            placeholder="请选择目标品检架"
            filterable
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="s in inspectionShelves"
              :key="s.id"
              :value="String(s.id)"
              :label="`${s.code} — ${s.name}`"
            >
              {{ s.code }} — {{ s.name }}
            </el-option>
          </el-select>
        </el-form-item>

        <el-form-item label="数量">
          <el-input-number
            v-model="scanInspectQty"
            :min="1"
            :max="scanInspectRow?.quantity"
            :precision="0"
            style="width: 160px"
          />
          <span v-if="scanInspectRow" class="muted" style="margin-left: 8px">
            / {{ scanInspectRow.quantity }}
          </span>
        </el-form-item>

        <el-form-item label="品检动作" required>
          <el-radio-group v-model="scanInspectDecision">
            <el-radio value="PASS">品检通过（PASS）</el-radio>
            <el-radio value="FAIL">打回生产架（FAIL）</el-radio>
          </el-radio-group>
        </el-form-item>

        <template v-if="scanInspectDecision === 'FAIL'">
          <el-form-item label="下一道工序" required>
            <el-select
              v-model="scanInspectProcessId"
              placeholder="请先选择下一道工序"
              filterable
              clearable
              style="width: 100%"
            >
              <el-option
                v-for="p in scanInspectFilteredProcesses"
                :key="p.id"
                :value="String(p.id)"
                :label="`${p.code} — ${p.name}`"
              >
                {{ p.code }} — {{ p.name }}
                <el-tag v-if="p.category === 'OUTSOURCE'" type="warning" size="small" effect="plain" class="opt-tag">
                  外协
                </el-tag>
              </el-option>
            </el-select>
          </el-form-item>

          <el-form-item label="目标生产架" required>
            <el-select
              v-model="scanInspectShelfIdFail"
              placeholder="先选工序；货架候选按映射过滤"
              filterable
              clearable
              style="width: 100%"
              :disabled="!scanInspectProcessId"
            >
              <el-option
                v-for="s in scanInspectFilteredProductionShelves"
                :key="s.id"
                :value="String(s.id)"
                :label="`${s.code} — ${s.name}`"
                :disabled="!s.is_active"
              >
                {{ s.code }} — {{ s.name }}
                <span v-if="!s.is_active" class="muted">（已停用）</span>
              </el-option>
              <template #empty>
                <span class="muted">
                  {{
                    scanInspectProcessId
                      ? '当前工序未映射到任何生产货架，请先在「货架管理 → 工序映射」配置'
                      : '请先选择下一道工序'
                  }}
                </span>
              </template>
            </el-select>
          </el-form-item>

          <el-form-item label="品检备注">
            <el-input
              v-model="scanInspectNote"
              type="textarea"
              :rows="3"
              :maxlength="500"
              show-word-limit
              placeholder="不合格原因 / 返修要点（写入事件历史，工人领取时可见）"
            />
          </el-form-item>
        </template>

        <el-alert
          type="info"
          :closable="false"
          title="快捷品检将一次性把零件搬到品检架，再按上面选择的动作（PASS → READY_TO_SHIP / FAIL → 回到生产架并指定下一道工序）完成流转。"
          show-icon
        />
      </el-form>

      <template #footer>
        <el-button @click="scanInspectDialogVisible = false">取消</el-button>
        <el-button
          :type="scanInspectDecision === 'PASS' ? 'success' : 'warning'"
          :loading="scanInspectSubmitting"
          :disabled="!scanInspectShelfId
            || (scanInspectDecision === 'FAIL'
              && (!scanInspectShelfIdFail || !scanInspectProcessId))"
          @click="onScanInspectConfirm"
        >确认品检</el-button>
      </template>
    </el-dialog>

    <!-- 2026-08-04：扫码命中同一 serial 多批次时复用报工台 BatchPickerDialog -->
    <BatchPickerDialog
      v-model="showBatchPicker"
      :code="batchPickerCode"
      :rows="batchPickerRows"
      @pick="onBatchPicked"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshLeft, Search } from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useDialogSize } from '@/composables/useDialogSize'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import {
  findAllByCode,
  findPartBySerialAndPrompt,
} from '@/utils/scanHelpers'
import {
  failInspection,
  getPartBySerial,
  listInspectionBatches,
  passInspection,
  scanInspect,
  type PartItem,
} from '@/api/parts'
import { listShelves } from '@/api/shelves'
import { listProcesses } from '@/api/process'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import { useListStatePersist } from '@/composables/useListFilterPersist'
import type { Shelf } from '@/types/shelf'
import type { Process } from '@/types/process'
import BatchPickerDialog from '@/views/scan/components/BatchPickerDialog.vue'

// ============ 状态 ============
interface RowState extends PartItem {
  _passing?: boolean
}
const items = ref<RowState[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(20)

const search = reactive({ keyword: '', serialNo: '' })
const plannedDateRange = ref<[string, string] | null>(null)

const emptyText = computed(() => errorMsg.value ?? '暂无待品检零件')

const { isMobile } = useBreakpoint()
// 手机上分页收窄为 prev/pager/next，桌面保留完整布局
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

function rowClassName({ row }: { row: RowState }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

async function fetchList(): Promise<void> {
  loading.value = true
  errorMsg.value = null
  try {
    // 2026-07-29 批次级：行=批次（quantity 为批次量，操作回传 batch_id）
    const resp = await listInspectionBatches({
      keyword: search.keyword.trim() || undefined,
      serial_no: search.serialNo.trim() || undefined,
      planned_delivery_date_from: plannedDateRange.value?.[0],
      planned_delivery_date_to: plannedDateRange.value?.[1],
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    items.value = resp.items
    total.value = resp.total
  } catch (e) {
    items.value = []
    total.value = 0
    errorMsg.value = (e as Error).message ?? '查询失败'
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  page.value = 1
  fetchList()
}

function onPageSizeChange(): void {
  page.value = 1
  fetchList()
}

// ============ 自动刷新 ============
const autoRefresh = ref(false)
let autoRefreshTimer: number | null = null

function onAutoRefreshToggle(val: string | number | boolean): void {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
  if (val) {
    autoRefreshTimer = window.setInterval(() => {
      fetchList()
    }, 300_000)
  }
}

// ============ 筛选状态持久化 ============
const { restore: restoreInspectionFilter, clear: clearInspectionFilter } = useListStatePersist(
  'inspection_pending',
  { search, pageSize, autoRefresh },
  { exclude: new Set(['page']) },
)

// ============ 列可见性 ============
// 「操作」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'serial_no', label: '序列号' },
  { key: 'drawing_no', label: '图号' },
  { key: 'name', label: '名称' },
  { key: 'batch_label', label: '批次' },
  { key: 'quantity', label: '批次量' },
  { key: 'planned_delivery_date', label: '计划交期' },
  { key: 'system_delivery_date', label: '系统交期' },
  { key: 'customer', label: '客户' },
  { key: 'shelf_code', label: '品检货架' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'inspection_pending' })

onBeforeUnmount(() => {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer)
  }
  // 2026-08-04：扫码订阅退订
  unsubInspectionScan()
})

// ============ 2026-08-04：扫码枪扫描序列号 → 二选一弹框 / 报工台风格位置提示 ============
//
// 扫码命中 INSPECTION 行（serial_no || drawing_no 在当前列表里匹配且状态=INSPECTION）：
//   - 1 条命中 → 弹「品检通过 / 指定下一工序」二选一对话框
//   - 多条命中（同 serial 不同 batch）→ 弹 BatchPickerDialog 选具体批次再走二选一
//   - 0 命中（零件不在 INSPECTION 状态或根本不存在）→ 走 findPartBySerialAndPrompt
//     显示当前位置 / 持有人 / 状态 / 下一工序，提示「该零件不在本工序」。
// 已有 dialog 显示时不抢流程。
const scanChooserOpen = ref(false)
const scanChooserRow = ref<RowState | null>(null)
const showBatchPicker = ref(false)
const batchPickerCode = ref('')
const batchPickerRows = ref<PartItem[]>([])

async function onInspectionScan(rawCode: string): Promise<void> {
  const code = rawCode.trim()
  if (!code) return
  // 已有 dialog 在显示时不抢流程
  if (
    passDialogVisible.value ||
    failDialogVisible.value ||
    scanChooserOpen.value ||
    scanInspectDialogVisible.value
  ) {
    return
  }
  // 快速路径：在当前已加载列表里按 serial_no || drawing_no 匹配（不限状态，按命中的状态分流）
  const matches = findAllByCode(
    items.value as unknown as PartItem[],
    code,
  )
  if (matches.length === 0) {
    // 0 命中 — PENDING / PROGRAMMING / IN_PROCESS+ON_SHELF 永远不会出现在 inspection 一览；
    // 调 getPartBySerial 按 serial_no 查任意状态零件，再按状态路由。
    try {
      const part = await getPartBySerial(code)
      routeScannedPart(part)
    } catch {
      // 404 / 网络错误 → 走 helper 显示「未找到」warning + 位置提示
      await findPartBySerialAndPrompt(code)
    }
    return
  }
  if (matches.length > 1) {
    // 多批次命中 — 复用报工台 BatchPickerDialog
    batchPickerCode.value = code
    batchPickerRows.value = matches
    showBatchPicker.value = true
    return
  }
  // 单条命中 — 按状态路由
  routeScannedPart(matches[0])
}

// 统一扫码路由：列表命中 / getPartBySerial fallback / BatchPicker 三处共用
// - INSPECTION → 原有二选一弹窗（点按钮复用原 pass/fail dialog）
// - PENDING / PROGRAMMING / IN_PROCESS+PRODUCTION_SHELF → 扫码快捷品检（新弹窗）
// - 其他（IN_PROCESS+WORKER / READY_TO_SHIP / DELIVERED / REPAIRING / OUTSOURCE）→ 降级提示
function routeScannedPart(part: PartItem): void {
  const row = part as unknown as RowState
  if (part.status === 'INSPECTION') {
    scanChooserRow.value = row
    scanChooserOpen.value = true
  } else if (
    part.status === 'PENDING' ||
    part.status === 'PROGRAMMING' ||
    (part.status === 'IN_PROCESS' && part.location === 'PRODUCTION_SHELF')
  ) {
    openScanInspectDialog(row)
  } else {
    void findPartBySerialAndPrompt(part.serial_no ?? part.drawing_no ?? '')
  }
}

function onScanChooserPass(): void {
  const row = scanChooserRow.value
  scanChooserOpen.value = false
  scanChooserRow.value = null
  if (row) onPass(row)  // 复用现有 onPass：弹原 pass dialog
}

function onScanChooserFail(): void {
  const row = scanChooserRow.value
  scanChooserOpen.value = false
  scanChooserRow.value = null
  if (row) void openFailDialog(row)  // 复用现有 openFailDialog
}

function onBatchPicked(p: PartItem): void {
  showBatchPicker.value = false
  routeScannedPart(p)
}

const { onScan } = useBarcodeScanner()
const unsubInspectionScan = onScan((code) => { void onInspectionScan(code) })

// ============ 品检通过（2026-07-29：带数量，部分通过先拆再过）============
const passDlg = useDialogSize({ desktopWidth: 420 })
const passDialogVisible = ref(false)
const passTarget = ref<RowState | null>(null)
const passQty = ref<number | undefined>(undefined)

function onPass(row: RowState): void {
  passTarget.value = row
  passQty.value = row.quantity
  passDialogVisible.value = true
}

function onPassDialogClosed(): void {
  passTarget.value = null
  passQty.value = undefined
}

async function onPassConfirm(): Promise<void> {
  const row = passTarget.value
  if (!row || !passQty.value) return
  row._passing = true
  try {
    await passInspection(row.id, {
      batch_id: row.batch_id ?? null,
      quantity: passQty.value,
    })
    ElMessage.success(
      `零件 ${row.serial_no || row.drawing_no} 品检通过 × ${passQty.value}`,
    )
    passDialogVisible.value = false
    await fetchList()
  } catch (e) {
    ElMessage.error(`品检通过失败：${(e as Error).message}`)
  } finally {
    row._passing = false
  }
}

// ============ 指定工序对话框 ============
// 2026-07-21 改：先选下一道工序，再选目标生产货架（按 shelf↔process 映射过滤）。
// 同时支持可选「品检备注」，写入 t_part_event.note，事件历史与工人领取卡片均可见。
const failDlg = useDialogSize({ desktopWidth: 520 })
const failDialogVisible = ref(false)
const failTarget = ref<RowState | null>(null)
const failProcessId = ref<string>('')
const failShelfId = ref<string>('')
const failNote = ref<string>('')
const failQty = ref<number | undefined>(undefined)
const failSubmitting = ref(false)
const productionShelves = ref<Shelf[]>([])
const processes = ref<Process[]>([])
const inspectionShelves = ref<Shelf[]>([])  // 2026-08-12 PR-I-scan-inspect：扫码快捷品检品检架选项
// 指定工序默认走 INHOUSE 工序（外协工序走 send_to_outsource 路径）；
// 不强制过滤 category，避免业务上「品检后直接外协返修」分支被锁死。
const {
  filteredShelves: filteredProductionShelves,
  filteredProcesses,
  load: loadShelfProcessMap,
} = useShelfProcessFilter(
  productionShelves,
  processes,
  computed({
    get: () => failShelfId.value || null,
    set: (v) => { failShelfId.value = v ?? '' },
  }),
  computed({
    get: () => failProcessId.value || null,
    set: (v) => { failProcessId.value = v ?? '' },
  }),
)

async function loadProductionShelves(): Promise<void> {
  try {
    const resp = await listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 })
    productionShelves.value = resp.items
  } catch (e) {
    ElMessage.error(`加载生产货架失败：${(e as Error).message}`)
    productionShelves.value = []
  }
}

async function loadProcesses(): Promise<void> {
  try {
    const resp = await listProcesses({ limit: 200 })
    processes.value = resp.items
  } catch (e) {
    ElMessage.error(`加载工序失败：${(e as Error).message}`)
    processes.value = []
  }
}

// ============ 2026-08-12 PR-I-scan-inspect：扫码快捷品检 ============
// 命中 PENDING / PROGRAMMING / IN_PROCESS+PRODUCTION_SHELF 时弹本对话框，
// 一步完成：搬到品检架 + 通过品检 / 指定下一工序。
const scanInspectDlg = useDialogSize({ desktopWidth: 520 })
const scanInspectDialogVisible = ref(false)
const scanInspectRow = ref<RowState | null>(null)
const scanInspectShelfId = ref<string>('')         // 目标品检架
const scanInspectProcessId = ref<string>('')       // 下一道工序（仅 FAIL）
const scanInspectShelfIdFail = ref<string>('')     // 目标生产架（仅 FAIL）
const scanInspectNote = ref<string>('')
const scanInspectQty = ref<number | undefined>(undefined)
const scanInspectDecision = ref<'PASS' | 'FAIL'>('PASS')
const scanInspectSubmitting = ref(false)

// 复用 fail 弹窗的 shelf/process 双向过滤
const {
  filteredShelves: scanInspectFilteredProductionShelves,
  filteredProcesses: scanInspectFilteredProcesses,
} = useShelfProcessFilter(
  productionShelves,
  processes,
  computed({
    get: () => scanInspectShelfIdFail.value || null,
    set: (v) => { scanInspectShelfIdFail.value = v ?? '' },
  }),
  computed({
    get: () => scanInspectProcessId.value || null,
    set: (v) => { scanInspectProcessId.value = v ?? '' },
  }),
)

async function loadInspectionShelves(): Promise<void> {
  try {
    const resp = await listShelves({ zone: 'INSPECTION', is_active: true, limit: 200 })
    inspectionShelves.value = resp.items
  } catch (e) {
    ElMessage.error(`加载品检货架失败：${(e as Error).message}`)
    inspectionShelves.value = []
  }
}

async function openScanInspectDialog(row: RowState): Promise<void> {
  scanInspectRow.value = row
  scanInspectShelfId.value = ''
  scanInspectShelfIdFail.value = ''
  scanInspectProcessId.value = ''
  scanInspectNote.value = ''
  scanInspectQty.value = row.quantity
  scanInspectDecision.value = 'PASS'
  scanInspectDialogVisible.value = true
  // 三个候选数据源并发加载（inspectionShelves / productionShelves / processes）
  await Promise.all([
    inspectionShelves.value.length === 0 ? loadInspectionShelves() : Promise.resolve(),
    productionShelves.value.length === 0 ? loadProductionShelves() : Promise.resolve(),
    processes.value.length === 0 ? loadProcesses() : Promise.resolve(),
  ])
  void loadShelfProcessMap()
}

function onScanInspectDialogClosed(): void {
  scanInspectRow.value = null
  scanInspectShelfId.value = ''
  scanInspectShelfIdFail.value = ''
  scanInspectProcessId.value = ''
  scanInspectNote.value = ''
  scanInspectQty.value = undefined
  scanInspectDecision.value = 'PASS'
}

async function onScanInspectConfirm(): Promise<void> {
  const row = scanInspectRow.value
  if (!row) return
  if (!scanInspectShelfId.value) {
    ElMessage.warning('请选择品检架')
    return
  }
  if (
    scanInspectDecision.value === 'FAIL'
    && (!scanInspectShelfIdFail.value || !scanInspectProcessId.value)
  ) {
    ElMessage.warning('打回生产架时，目标货架与下一道工序必填')
    return
  }
  scanInspectSubmitting.value = true
  try {
    await scanInspect(row.id, {
      target_inspection_shelf_id: scanInspectShelfId.value,
      decision: scanInspectDecision.value,
      shelf_id: scanInspectShelfIdFail.value || undefined,
      next_process_id: scanInspectProcessId.value || undefined,
      note: scanInspectNote.value.trim() || null,
      batch_id: row.batch_id ?? null,
      quantity: scanInspectQty.value ?? null,
    })
    ElMessage.success(
      scanInspectDecision.value === 'PASS'
        ? `零件 ${row.serial_no || row.drawing_no} 快捷品检通过`
        : `零件 ${row.serial_no || row.drawing_no} 已快捷打回`,
    )
    scanInspectDialogVisible.value = false
    await fetchList()
  } catch (e) {
    ElMessage.error(`快捷品检失败：${(e as Error).message}`)
  } finally {
    scanInspectSubmitting.value = false
  }
}

async function openFailDialog(row: RowState): Promise<void> {
  failTarget.value = row
  failProcessId.value = ''
  failShelfId.value = ''
  failNote.value = ''
  failQty.value = row.quantity
  failDialogVisible.value = true
  await Promise.all([
    productionShelves.value.length === 0 ? loadProductionShelves() : Promise.resolve(),
    processes.value.length === 0 ? loadProcesses() : Promise.resolve(),
  ])
  // shelves/processes 加载完后异步拉映射；映射未到位前 filteredXxx 走兜底全量
  void loadShelfProcessMap()
}

function onFailDialogClosed(): void {
  failTarget.value = null
  failProcessId.value = ''
  failShelfId.value = ''
  failNote.value = ''
  failQty.value = undefined
}

async function onFailConfirm(): Promise<void> {
  if (!failTarget.value || !failProcessId.value || !failShelfId.value) return
  const row = failTarget.value
  const shelfCode =
    productionShelves.value.find((s) => String(s.id) === failShelfId.value)?.code ?? ''
  const processCode =
    processes.value.find((p) => String(p.id) === failProcessId.value)?.code ?? ''
  try {
    await ElMessageBox.confirm(
      `确认指定工序「${row.name}」（${row.serial_no || row.drawing_no}）到生产货架 ${shelfCode}，下一道工序 ${processCode}？`,
      '指定工序',
      { type: 'warning', confirmButtonText: '确认指定工序', cancelButtonText: '取消' },
    )
  } catch {
    return  // 用户取消
  }
  failSubmitting.value = true
  try {
    await failInspection(row.id, {
      shelf_id: failShelfId.value,
      next_process_id: failProcessId.value,
      note: failNote.value.trim() || null,
      batch_id: row.batch_id ?? null,
      quantity: failQty.value ?? null,
    })
    ElMessage.success(
      `零件 ${row.serial_no || row.drawing_no} 已指定下一道工序 ${processCode}，放到生产货架 ${shelfCode}`,
    )
    failDialogVisible.value = false
    await fetchList()
  } catch (e) {
    ElMessage.error(`指定工序失败：${(e as Error).message}`)
  } finally {
    failSubmitting.value = false
  }
}

onMounted(() => {
  // 先尝试恢复 localStorage 中的搜索条件 / 分页大小 / 自动刷新
  const persisted = restoreInspectionFilter()
  if (persisted) {
    if (persisted.search) Object.assign(search, persisted.search)
    if (typeof persisted.pageSize === 'number') pageSize.value = persisted.pageSize
    if (typeof persisted.autoRefresh === 'boolean') {
      autoRefresh.value = persisted.autoRefresh
      if (autoRefresh.value) {
        // 重新挂载定时器
        onAutoRefreshToggle(true)
      }
    }
  }
  fetchList()
})
</script>

<style lang="scss" scoped>
.inspection-pending {
  padding: 0;
}
.filter-card {
  margin-bottom: 12px;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.total-hint {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 13px;
}
.sheet-wrapper {
  background: #fff;
  border-radius: 6px;
  padding: 8px 0;
}
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;

  @include until(sm) {
    justify-content: center;
  }
}
.name-link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.name-link:hover {
  text-decoration: underline;
}
.muted {
  color: var(--text-secondary);
}
:deep(.row-urgent) {
  background: #fde2e2 !important;
}
:deep(.row-urgent td) {
  background: #fde2e2 !important;
}
.fail-summary {
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 4px;
  padding: 10px 14px;
  line-height: 1.8;
  font-size: 13px;
}
.opt-tag {
  margin-left: 6px;
}

.batch-label {
  font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace;
  font-weight: 600;
}
</style>