<!--
  送货单详情（PR-G 2026-07-22 新增）

  形态对齐 frontend/src/views/parts/PartDetail.vue
  不同点：本页主操作是「添加零件」「移除零件」「提交」「撤回」「软删」
  以及「扫码领取」链接（仅 SUBMITTED 状态跳司机扫码台）。
-->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Van } from '@element-plus/icons-vue'

import {
  addParts,
  getNote,
  listNoteEvents,
  removeParts,
  recallNote,
  softDeleteNote,
  submitNote,
} from '@/api/deliveryNote'
import {
  DELIVERY_NOTE_STATUS_LABEL,
  DELIVERY_NOTE_STATUS_TAG,
  type DeliveryNoteDetailOut,
  type DeliveryNoteEventOut,
  type DeliveryNoteStatus,
} from '@/types/deliveryNote'
import {
  canAddRemoveParts,
  canRecall,
  canSoftDelete,
  canSubmit,
} from '@/utils/deliveryNotePermissions'
import { getPartBySerial } from '@/api/parts'
import { useAuthSession } from '@/composables/useAuthSession'

const route = useRoute()
const router = useRouter()
const { hasRole } = useAuthSession()

const role = computed(() => ({
  MANAGER: hasRole('MANAGER'),
  CLERK: hasRole('CLERK'),
}))

const note = ref<DeliveryNoteDetailOut | null>(null)
const events = ref<DeliveryNoteEventOut[]>([])
const loading = ref(false)

async function fetchDetail() {
  const id = route.params.id as string
  if (!id) return
  loading.value = true
  try {
    note.value = await getNote(id)
    events.value = await listNoteEvents(id)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(fetchDetail)
watch(() => route.params.id, fetchDetail)

// ============================================================
// 状态机迁移操作
// ============================================================
async function onSubmit() {
  if (!note.value) return
  try {
    await ElMessageBox.confirm(
      `确认提交 ${note.value.delivery_note_no}？`,
      '提交送货单', { type: 'warning' },
    )
  } catch { return }
  try {
    await submitNote(note.value.id, { version: note.value.version })
    ElMessage.success('已提交')
    fetchDetail()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '提交失败')
  }
}

async function onRecall() {
  if (!note.value) return
  try {
    await ElMessageBox.confirm(
      `确认撤回 ${note.value.delivery_note_no}？`,
      '撤回送货单', { type: 'warning' },
    )
  } catch { return }
  try {
    await recallNote(note.value.id, { version: note.value.version })
    ElMessage.success('已撤回')
    fetchDetail()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '撤回失败')
  }
}

async function onSoftDelete() {
  if (!note.value) return
  try {
    await ElMessageBox.confirm(
      `确认删除 ${note.value.delivery_note_no}？关联零件会解除。`,
      '删除送货单', { type: 'warning' },
    )
  } catch { return }
  try {
    await softDeleteNote(note.value.id, { version: note.value.version })
    ElMessage.success('已删除')
    router.push('/delivery-notes')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

function gotoPickup() {
  if (!note.value) return
  router.push(`/scan/delivery-note-pickup/${note.value.id}`)
}

// ============================================================
// 添加零件对话框：按 serial 一行一条输入 → 解析 part_id → add_parts
// ============================================================
const addDialogOpen = ref(false)
const serialsInput = ref('')
const addBusy = ref(false)
const addErrors = ref<string[]>([])

function openAdd() {
  serialsInput.value = ''
  addErrors.value = []
  addDialogOpen.value = true
}

async function submitAdd() {
  if (!note.value) return
  const serials = serialsInput.value
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (!serials.length) {
    ElMessage.warning('请输入至少一个序列号')
    return
  }
  addBusy.value = true
  addErrors.value = []
  const partIds: string[] = []
  for (const s of serials) {
    try {
      const p = await getPartBySerial(s)
      partIds.push(p.id)
    } catch (e) {
      addErrors.value.push(`未识别序列号「${s}」：${(e as Error).message}`)
    }
  }
  if (!partIds.length) {
    addBusy.value = false
    return
  }
  try {
    await addParts(note.value.id, {
      part_ids: partIds, version: note.value.version,
    })
    ElMessage.success(`已添加 ${partIds.length} 件`)
    addDialogOpen.value = false
    fetchDetail()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '添加失败')
  } finally {
    addBusy.value = false
  }
}

// ============================================================
// 移除选定零件
// ============================================================
const selectedItemIds = ref<string[]>([])
async function onRemoveSelected() {
  if (!note.value) return
  if (!selectedItemIds.value.length) {
    ElMessage.warning('请勾选要移除的零件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认移除选中的 ${selectedItemIds.value.length} 件零件？`,
      '移除零件', { type: 'warning' },
    )
  } catch { return }
  try {
    await removeParts(note.value.id, {
      part_ids: selectedItemIds.value,
      version: note.value.version,
    })
    ElMessage.success('已移除')
    selectedItemIds.value = []
    fetchDetail()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '移除失败')
  }
}

// ============================================================
// 派生
// ============================================================
const canAdd = computed(() => note.value && canAddRemoveParts(note.value.status, role.value))
const canEdit = computed(() => canAdd.value)
</script>

<template>
  <div v-loading="loading" class="delivery-note-detail">
    <template v-if="note">
      <el-page-header @back="$router.push('/delivery-notes')" class="page-header">
        <template #content>
          <span class="page-title">
            {{ note.delivery_note_no }}
            <el-tag
              :type="DELIVERY_NOTE_STATUS_TAG[note.status] || 'info'"
              size="small"
              effect="plain"
            >
              {{ DELIVERY_NOTE_STATUS_LABEL[note.status] }}
            </el-tag>
          </span>
        </template>
      </el-page-header>

      <el-card shadow="never" class="info-card">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="单号">{{ note.delivery_note_no }}</el-descriptions-item>
          <el-descriptions-item label="客户">
            {{ note.customer_path ?? note.customer_name ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="零件数">{{ note.part_count }}</el-descriptions-item>
          <el-descriptions-item label="提交时间">
            {{ note.submitted_at ? new Date(note.submitted_at).toLocaleString() : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="领取时间">
            {{ note.picked_up_at ? new Date(note.picked_up_at).toLocaleString() : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="司机">
            {{ note.driver_worker_name ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="备注" :span="3">
            {{ note.note || '—' }}
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never" class="line-items-card">
        <template #header>
          <div class="card-header">
            <span>零件列表 ({{ note.line_items.length }})</span>
            <div class="actions">
              <el-button
                v-if="canAdd"
                type="primary"
                size="small"
                @click="openAdd"
              >
                添加零件
              </el-button>
              <el-button
                v-if="canEdit && selectedItemIds.length"
                type="danger"
                size="small"
                @click="onRemoveSelected"
              >
                移除选中 ({{ selectedItemIds.length }})
              </el-button>
            </div>
          </div>
        </template>
        <el-table
          :data="note.line_items"
          stripe
          border
          height="500"
          @selection-change="(rows: any[]) => selectedItemIds = rows.map(r => r.id)"
        >
          <el-table-column
            v-if="canEdit"
            type="selection"
            width="50"
            :selectable="() => true"
          />
          <el-table-column type="index" label="#" width="60" />
          <el-table-column prop="serial_no" label="序列号" width="160" />
          <el-table-column prop="drawing_no" label="图号" width="160" />
          <el-table-column prop="name" label="名称" min-width="200" />
          <el-table-column prop="quantity" label="数量" width="80" align="center" />
          <el-table-column label="紧急" width="80" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.is_urgent" type="danger" size="small">急</el-tag>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="120" />
        </el-table>
      </el-card>

      <el-card shadow="never" class="actions-card">
        <template #header>
          <span>状态操作</span>
        </template>
        <el-space wrap>
          <el-button
            v-if="note.status === 'DRAFT' && (role.MANAGER || role.CLERK)"
            type="primary"
            @click="onSubmit"
          >
            提交（DRAFT → SUBMITTED）
          </el-button>
          <el-button
            v-if="note.status === 'SUBMITTED' && (role.MANAGER || role.CLERK)"
            type="warning"
            @click="onRecall"
          >
            撤回（SUBMITTED → DRAFT）
          </el-button>
          <el-button
            v-if="note.status === 'SUBMITTED'"
            type="danger"
            @click="gotoPickup"
          >
            <el-icon><Van /></el-icon>
            扫码领取
          </el-button>
          <el-button
            v-if="canSoftDelete(note.status, role)"
            type="danger"
            plain
            @click="onSoftDelete"
          >
            删除草稿
          </el-button>
        </el-space>
      </el-card>

      <el-card shadow="never" class="events-card">
        <template #header><span>事件流</span></template>
        <el-timeline>
          <el-timeline-item
            v-for="e in events"
            :key="e.id"
            :timestamp="e.created_at ? new Date(e.created_at).toLocaleString() : ''"
          >
            <strong>{{ e.event_type }}</strong>
            <span v-if="e.from_status && e.to_status">
              ({{ e.from_status }} → {{ e.to_status }})
            </span>
            <div v-if="e.note" class="event-note">{{ e.note }}</div>
            <div v-if="e.scanned_count != null" class="event-note">
              已扫 {{ e.scanned_count }} / 共 {{ e.expected_count }}
            </div>
          </el-timeline-item>
        </el-timeline>
      </el-card>
    </template>

    <!-- 添加零件对话框 -->
    <el-dialog v-model="addDialogOpen" title="添加零件（按序列号；多件用空格/逗号/回车分隔）" width="520px">
      <el-input
        v-model="serialsInput"
        type="textarea"
        :rows="6"
        placeholder="F1234&#10;F1235&#10;L2001"
      />
      <div v-if="addErrors.length" class="add-errors">
        <div v-for="(msg, idx) in addErrors" :key="idx" class="add-error">
          {{ msg }}
        </div>
      </div>
      <template #footer>
        <el-button @click="addDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="addBusy" @click="submitAdd">
          添加
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.delivery-note-detail { padding: 16px; }
.page-header { margin-bottom: 16px; }
.page-title { display: flex; align-items: center; gap: 12px; font-size: 18px; font-weight: 600; }
.info-card, .line-items-card, .actions-card, .events-card { margin-bottom: 16px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.actions { display: flex; gap: 8px; }
.event-note { font-size: 13px; color: #666; margin-top: 4px; }
.add-errors { margin-top: 8px; max-height: 120px; overflow-y: auto; }
.add-error { color: #d9534f; font-size: 13px; }
</style>
