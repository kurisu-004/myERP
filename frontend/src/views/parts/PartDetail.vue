<!--
  PartDetail.vue

  /parts/:id  零件详情页。
  - 信息卡：支持点击「编辑」切换内联编辑模式
  - 页面底部放置「取消订单」「删除」按钮，需输入流水号确认
-->
<template>
  <div class="part-detail">
    <!-- 信息卡 -->
    <el-card shadow="never" class="info-card" v-loading="infoLoading">
      <template v-if="part">
        <template v-if="editing">
          <el-descriptions :column="3" border>
            <el-descriptions-item label="序列号">
              <span v-if="part.serial_no" class="mono">{{ part.serial_no }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="图号">
              <el-input v-model="form.drawing_no" size="small" />
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTagType(part.status)" effect="plain" size="small">
                {{ statusLabel(part.status) }}
              </el-tag>
            </el-descriptions-item>

            <el-descriptions-item label="名称" :span="3">
              <el-input v-model="form.name" size="small" />
            </el-descriptions-item>

            <el-descriptions-item label="数量">
              <el-input-number v-model="form.quantity" :min="1" size="small" style="width:100%" />
            </el-descriptions-item>
            <el-descriptions-item label="加急">
              <el-switch v-model="form.is_urgent" active-text="加急" />
            </el-descriptions-item>
            <el-descriptions-item label="客户">
              <span v-if="part.customer_path">{{ part.customer_path }}</span>
              <span v-else-if="part.customer_name">{{ part.customer_name }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>

            <el-descriptions-item label="计划交期">
              <el-date-picker v-model="form.planned_delivery_date" type="date" size="small" style="width:100%" />
            </el-descriptions-item>
            <el-descriptions-item label="实际送货">
              <el-date-picker v-model="form.actual_delivery_date" type="date" size="small" style="width:100%" />
            </el-descriptions-item>
            <el-descriptions-item label="单据 ID">#{{ part.id }}</el-descriptions-item>
          </el-descriptions>

          <div class="edit-actions">
            <el-button @click="onCancelEdit">取消</el-button>
            <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
          </div>
        </template>

        <template v-else>
          <el-descriptions :column="3" border>
            <el-descriptions-item label="序列号">
              <span v-if="part.serial_no" class="mono">{{ part.serial_no }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="图号">{{ part.drawing_no }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTagType(part.status)" effect="plain" size="small">
                {{ statusLabel(part.status) }}
              </el-tag>
            </el-descriptions-item>

            <el-descriptions-item label="名称" :span="3">{{ part.name }}</el-descriptions-item>

            <el-descriptions-item label="数量">{{ part.quantity }}</el-descriptions-item>
            <el-descriptions-item label="加急">
              <el-tag v-if="part.is_urgent" type="danger" effect="dark" size="small">加急</el-tag>
              <span v-else class="muted">否</span>
            </el-descriptions-item>
            <el-descriptions-item label="客户">
              <span v-if="part.customer_path">{{ part.customer_path }}</span>
              <span v-else-if="part.customer_name">{{ part.customer_name }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>

            <el-descriptions-item label="计划交期">{{ part.planned_delivery_date }}</el-descriptions-item>
            <el-descriptions-item label="实际送货">
              <span v-if="part.actual_delivery_date">{{ part.actual_delivery_date }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="单据 ID">#{{ part.id }}</el-descriptions-item>
          </el-descriptions>

          <div class="edit-actions">
            <el-button type="primary" plain @click="onStartEdit">编辑</el-button>
          </div>
        </template>
      </template>
    </el-card>

    <!-- 条形码（仅当存在 serial_no 时显示） -->
    <el-card v-if="part && part.serial_no" shadow="never" class="barcode-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">
            <el-icon><PriceTag /></el-icon>
            <span>序列号条码</span>
          </span>
          <span class="mono serial-label">{{ part.serial_no }}</span>
        </div>
      </template>
      <div class="barcode-wrap">
        <Barcode :value="part.serial_no" format="CODE39" :height="80" :width="2" />
      </div>
    </el-card>

    <!-- 所属装配件（仅子零件） -->
    <el-card
      v-if="part && part.assembly_id != null"
      shadow="never"
      class="assembly-card"
      v-loading="assemblyLoading"
    >
      <template #header>
        <div class="card-header">
          <span class="card-title">
            <el-icon><Connection /></el-icon>
            <span>所属装配件</span>
          </span>
          <el-button
            link
            type="primary"
            size="small"
            @click="$router.push(`/assemblies/${part.assembly_id}`)"
          >
            查看装配件详情
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </template>
      <el-descriptions v-if="assemblyDetail" :column="3" border>
        <el-descriptions-item label="总图图号">
          <span class="mono">{{ assemblyDetail.assembly.drawing_no }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="装配体名称">
          {{ assemblyDetail.assembly.name }}
        </el-descriptions-item>
        <el-descriptions-item label="装配件状态">
          <el-tag :type="assemblyDetail.assembly.status === 'COMPLETED' ? 'success' : 'info'" size="small">
            {{ assemblyDetail.assembly.status }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="客户">
          {{ assemblyDetail.assembly.customer_path || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="子零件数">
          <el-tag type="info" size="small" effect="plain">
            {{ assemblyDetail.assembly.child_count }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="计划交期">
          {{ assemblyDetail.assembly.planned_delivery_date }}
        </el-descriptions-item>
      </el-descriptions>

      <div v-if="assemblyDetail" class="siblings">
        <div class="siblings-title">兄弟零件（点击跳转）</div>
        <div class="siblings-grid">
          <el-tag
            v-for="sib in assemblyDetail.children"
            :key="sib.id"
            :type="sib.id === part!.id ? 'primary' : 'info'"
            :effect="sib.id === part!.id ? 'dark' : 'plain'"
            class="sibling-chip"
            @click="$router.push(`/parts/${sib.id}`)"
          >
            <span class="sib-serial">{{ sib.serial_no || '—' }}</span>
            <span class="sib-name">{{ sib.drawing_no }}</span>
            <span class="sib-label">{{ sib.name }}</span>
          </el-tag>
        </div>
      </div>
    </el-card>

    <!-- 图纸 / 文件 -->
    <FileListCard
      :files="files"
      owner-type="part"
      :owner-id="partId"
      :show-upload="true"
      :show-delete="true"
      @refresh="fetchFiles"
    />

    <!-- 历史记录 -->
    <el-card shadow="never" class="history-card" v-loading="eventsLoading">
      <template #header>
        <div class="card-header">
          <span class="card-title">历史记录</span>
          <span v-if="events" class="event-count">共 {{ events.length }} 条</span>
        </div>
      </template>

      <div v-if="events && events.length > 0" class="timeline">
        <el-timeline>
          <el-timeline-item
            v-for="evt in events"
            :key="evt.id"
            :timestamp="formatDateTime(evt.created_at)"
            placement="top"
            :type="eventTagType(evt.event_type)"
            :hollow="evt.event_type !== 'CREATED'"
          >
            <div class="event-card">
              <div class="event-line-1">
                <el-tag :type="eventTagType(evt.event_type)" effect="dark" size="small">
                  {{ eventLabel(evt.event_type) }}
                </el-tag>
                <span v-if="evt.worker_name" class="worker-name">
                  <el-icon><User /></el-icon>
                  {{ evt.worker_name }}
                </span>
              </div>
              <div v-if="evt.from_status || evt.to_status" class="event-line-2">
                <span v-if="evt.from_status" class="status-pill">
                  {{ statusLabelOf(evt.from_status) }}
                </span>
                <el-icon v-if="evt.from_status && evt.to_status" class="arrow"><Right /></el-icon>
                <span v-if="evt.to_status" class="status-pill">
                  {{ statusLabelOf(evt.to_status) }}
                </span>
              </div>
              <div v-if="evt.drawing_code || evt.badge_code" class="event-line-3">
                <span v-if="evt.drawing_code">
                  <span class="meta-label">图纸</span>
                  <span class="meta-value">{{ evt.drawing_code }}</span>
                </span>
                <span v-if="evt.badge_code">
                  <span class="meta-label">工牌</span>
                  <span class="meta-value">{{ evt.badge_code }}</span>
                </span>
              </div>
              <div v-if="evt.note" class="event-note">备注：{{ evt.note }}</div>
            </div>
          </el-timeline-item>
        </el-timeline>
      </div>
      <el-empty v-else description="暂无历史记录" />
    </el-card>

    <!-- 底部操作：取消订单 / 删除 -->
    <el-card shadow="never" class="bottom-actions" v-if="part">
      <div class="action-row">
        <el-button
          v-if="part.status !== 'CANCELLED' && part.status !== 'COMPLETED'"
          type="warning"
          @click="onCancelOrder"
        >取消订单</el-button>
        <el-button type="danger" @click="onDeletePart">删除</el-button>
      </div>
    </el-card>

    <!-- 取消 / 删除确认对话框 -->
    <el-dialog v-model="confirmVisible" :title="confirmTitle" width="420px">
      <div class="confirm-body">
        <p class="confirm-hint">{{ confirmHint }}</p>
        <el-form label-width="80px">
          <el-form-item label="流水号">
            <el-input
              v-model="confirmSerialNo"
              placeholder="请输入该零件的流水号以确认"
              clearable
            />
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="confirmVisible = false">取消</el-button>
        <el-button
          :type="confirmAction === 'cancel' ? 'warning' : 'danger'"
          :loading="confirmSubmitting"
          :disabled="!confirmSerialNo.trim()"
          @click="onConfirmAction"
        >确认{{ confirmAction === 'cancel' ? '取消' : '删除' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Connection, PriceTag, Right, User } from '@element-plus/icons-vue'
import FileListCard from '@/components/FileListCard.vue'
import Barcode from '@/components/Barcode.vue'
import {
  cancelPart,
  getPart,
  listPartEvents,
  softDeletePart,
  updatePart,
  type PartItem,
  type PartEvent,
  type PartUpdatePayload,
} from '@/api/parts'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  PART_EVENT_LABEL,
  PART_EVENT_TAG_TYPE,
  type OrderStatus,
  type PartEventType,
} from '@/types/parts'
import { getAssemblyForPart } from '@/api/assembly'
import type { AssemblyDetail } from '@/types/assembly'
import type { DrawingFileItem } from '@/types/file'
import { listPartFiles } from '@/api/assembly'

const route = useRoute()
const router = useRouter()
const partId = ref<string>(String(route.params.id ?? ''))

// ============ 数据 ============
const part = ref<PartItem | null>(null)
const events = ref<PartEvent[] | null>(null)
const files = ref<DrawingFileItem[]>([])
const assemblyDetail = ref<AssemblyDetail | null>(null)
const infoLoading = ref(false)
const eventsLoading = ref(false)
const filesLoading = ref(false)
const assemblyLoading = ref(false)

// ============ 编辑模式 ============
const editing = ref(false)
const saving = ref(false)
const form = reactive({
  name: '',
  drawing_no: '',
  quantity: 1,
  is_urgent: false,
  planned_delivery_date: '',
  actual_delivery_date: '' as string | null,
})

function onStartEdit(): void {
  if (!part.value) return
  form.name = part.value.name
  form.drawing_no = part.value.drawing_no
  form.quantity = part.value.quantity
  form.is_urgent = part.value.is_urgent
  form.planned_delivery_date = part.value.planned_delivery_date
  form.actual_delivery_date = part.value.actual_delivery_date
  editing.value = true
}

function onCancelEdit(): void {
  editing.value = false
}

async function onSave(): Promise<void> {
  saving.value = true
  try {
    const payload: PartUpdatePayload = {
      name: form.name.trim(),
      drawing_no: form.drawing_no.trim(),
      quantity: form.quantity,
      is_urgent: form.is_urgent,
      planned_delivery_date: form.planned_delivery_date,
      actual_delivery_date: form.actual_delivery_date || null,
    }
    part.value = await updatePart(partId.value, payload)
    ElMessage.success('保存成功')
    editing.value = false
  } catch (e) {
    ElMessage.error((e as Error).message ?? '保存失败')
  } finally {
    saving.value = false
  }
}

// ============ 取消 / 删除 ============
const confirmVisible = ref(false)
const confirmAction = ref<'cancel' | 'delete'>('cancel')
const confirmSerialNo = ref('')
const confirmSubmitting = ref(false)

const confirmTitle = computed(() =>
  confirmAction.value === 'cancel' ? '取消订单' : '删除零件'
)

const confirmHint = computed(() => {
  const base = confirmAction.value === 'cancel'
    ? '取消后订单将变为 CANCELLED 状态，流水号将被释放。'
    : '删除后将软删除该零件记录。'
  return `${base}\n请输入该零件的流水号以确认操作。`
})

function onCancelOrder(): void {
  confirmAction.value = 'cancel'
  confirmSerialNo.value = ''
  confirmVisible.value = true
}

function onDeletePart(): void {
  confirmAction.value = 'delete'
  confirmSerialNo.value = ''
  confirmVisible.value = true
}

async function onConfirmAction(): Promise<void> {
  const expected = part.value?.serial_no
  if (!expected) {
    ElMessage.error('该零件无流水号，无法执行此操作')
    return
  }
  if (confirmSerialNo.value.trim() !== expected) {
    ElMessage.error('流水号不匹配，请重新输入')
    return
  }
  confirmSubmitting.value = true
  try {
    if (confirmAction.value === 'cancel') {
      await cancelPart(partId.value)
      ElMessage.success('已取消')
      confirmVisible.value = false
      await fetchPart()
      void fetchEvents()
    } else {
      await softDeletePart(partId.value)
      ElMessage.success('已删除')
      confirmVisible.value = false
      router.push('/parts')
    }
  } catch (e) {
    ElMessage.error((e as Error).message ?? '操作失败')
  } finally {
    confirmSubmitting.value = false
  }
}

function statusLabel(s: OrderStatus): string {
  return ORDER_STATUS_LABEL[s] ?? s
}
function statusTagType(s: OrderStatus): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return ORDER_STATUS_TAG_TYPE[s] ?? 'info'
}
function statusLabelOf(s: string | null | undefined): string {
  if (!s) return ''
  return ORDER_STATUS_LABEL[s as OrderStatus] ?? s
}
function eventLabel(t: string): string {
  return PART_EVENT_LABEL[t as PartEventType] ?? t
}
function eventTagType(t: string): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return PART_EVENT_TAG_TYPE[t as PartEventType] ?? 'info'
}
function formatDateTime(iso: string): string {
  if (!iso) return ''
  const [d, t] = iso.split('T')
  if (!d) return iso
  if (!t) return d
  return `${d} ${t.slice(0, 5)}`
}

// ============ 拉取 ============
async function fetchPart(): Promise<void> {
  infoLoading.value = true
  try {
    part.value = await getPart(partId.value)
  } catch (e) {
    part.value = null
    ElMessage.error((e as Error).message ?? '加载零件失败')
  } finally {
    infoLoading.value = false
  }
}

async function fetchEvents(): Promise<void> {
  eventsLoading.value = true
  try {
    events.value = await listPartEvents(partId.value)
  } catch (e) {
    events.value = null
    ElMessage.error((e as Error).message ?? '加载历史记录失败')
  } finally {
    eventsLoading.value = false
  }
}

async function fetchFiles(): Promise<void> {
  filesLoading.value = true
  try {
    files.value = await listPartFiles(partId.value)
  } catch (e) {
    files.value = []
    ElMessage.error((e as Error).message ?? '加载文件列表失败')
  } finally {
    filesLoading.value = false
  }
}

async function fetchAssembly(): Promise<void> {
  if (!part.value || part.value.assembly_id == null) {
    assemblyDetail.value = null
    return
  }
  assemblyLoading.value = true
  try {
    assemblyDetail.value = await getAssemblyForPart(part.value.id)
  } catch (e) {
    assemblyDetail.value = null
    ElMessage.error((e as Error).message ?? '加载装配件信息失败')
  } finally {
    assemblyLoading.value = false
  }
}

onMounted(() => {
  void fetchPart()
  void fetchEvents()
  void fetchFiles()
})

watch(
  () => route.params.id,
  async (id) => {
    const s = String(id ?? '')
    if (!s) return
    partId.value = s
    editing.value = false
    assemblyDetail.value = null
    files.value = []
    await fetchPart()
    void fetchEvents()
    void fetchFiles()
    void fetchAssembly()
  },
)

watch(
  () => part.value?.assembly_id,
  () => {
    void fetchAssembly()
  },
)
</script>

<style lang="scss" scoped>
.part-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.edit-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-weight: 600;
  color: var(--text-primary);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.event-count {
  color: var(--text-secondary);
  font-size: 13px;
}

.muted {
  color: var(--text-secondary);
}
.mono {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}

.barcode-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
    display: flex;
    justify-content: center;
  }
  .serial-label {
    font-weight: 600;
    color: var(--primary-color);
  }
  .barcode-wrap {
    background: #fff;
    padding: 8px 12px;
  }
}

.assembly-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
  }
}
.siblings {
  margin-top: 16px;
}
.siblings-title {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}
.siblings-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.sibling-chip {
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 4px;
  transition: transform 0.15s;
  &:hover {
    transform: translateY(-1px);
  }
}
.sib-serial {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-weight: 600;
}
.sib-name {
  color: var(--text-secondary);
  font-size: 12px;
}
.sib-label {
  font-size: 12px;
}

.bottom-actions {
  .action-row {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }
}

.confirm-body {
  .confirm-hint {
    white-space: pre-line;
    color: var(--text-secondary);
    font-size: 13px;
    margin-bottom: 16px;
  }
}

.history-card {
  .timeline {
    padding: 8px 0;
  }
  .event-card {
    background: #fff;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    padding: 10px 14px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .event-line-1 {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .worker-name {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--text-primary);
    font-size: 13px;
  }
  .event-line-2 {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--text-regular);
    font-size: 13px;
  }
  .status-pill {
    padding: 1px 8px;
    border-radius: 10px;
    background: #f0f2f5;
    color: var(--text-primary);
    font-size: 12px;
  }
  .arrow {
    color: var(--text-secondary);
  }
  .event-line-3 {
    display: flex;
    gap: 16px;
    font-size: 12px;
    color: var(--text-secondary);
  }
  .meta-label {
    margin-right: 4px;
    color: var(--text-secondary);
  }
  .meta-value {
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    color: var(--text-primary);
  }
  .event-note {
    color: var(--text-regular);
    font-size: 13px;
    background: #fdf6ec;
    padding: 4px 8px;
    border-radius: 4px;
  }
}
</style>
