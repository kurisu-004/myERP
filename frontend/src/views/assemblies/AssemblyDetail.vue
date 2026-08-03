<!--
  AssemblyDetail.vue

  /assemblies/:id — 装配件详情页。

  区块：
  - 装配件信息（总图图号、名称、客户、日期、加急、状态、子零件数）
  - 图纸 / 文件列表（FileListCard）
  - 子零件表格（每个零件可点击 → 跳到 /parts/:id）

  数据来源：GET /api/v1/assemblies/:id
-->
<template>
  <div class="assembly-detail" v-loading="loading">
    <el-card v-if="detail" shadow="never" class="info-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">
            装配件详情
            <el-tag :type="statusTagType(detail.assembly.status)" size="small" effect="dark">
              {{ statusLabel(detail.assembly.status) }}
            </el-tag>
          </span>
          <div class="card-actions">
            <el-button @click="$router.push('/parts')">
              <el-icon><Back /></el-icon>
              <span>返回列表</span>
            </el-button>
            <!-- 编辑元数据（CLERK+；终态由后端拒绝） -->
            <el-button
              v-if="canEditContent"
              type="primary"
              plain
              @click="openEditDialog"
            >
              <el-icon><Edit /></el-icon>
              <span>编辑元数据</span>
            </el-button>
            <!-- 取消（CLERK+）：非终态（PENDING / IN_PROCESS / INSPECTION / READY_TO_SHIP / DELIVERED）可触发 -->
            <el-button
              v-if="canCancel"
              type="warning"
              plain
              :disabled="!detail.assembly.serial_no"
              @click="openConfirmDialog('cancel')"
            >
              <el-icon><CircleClose /></el-icon>
              <span>取消装配件</span>
            </el-button>
            <!-- 删除（MANAGER-only）：不论状态都可触发 -->
            <el-button
              v-if="canDelete"
              type="danger"
              plain
              :disabled="!detail.assembly.serial_no"
              @click="openConfirmDialog('delete')"
            >
              <el-icon><Delete /></el-icon>
              <span>删除装配件</span>
            </el-button>
          </div>
        </div>
      </template>

      <el-descriptions :column="descCol" border>
        <el-descriptions-item label="序列号">
          <span v-if="detail.assembly.serial_no" class="mono">{{ detail.assembly.serial_no }}</span>
          <el-tag v-else size="small" type="info" effect="plain">暂无（旧数据）</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="总图图号">
          <span class="mono">{{ detail.assembly.drawing_no }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="装配体名称">
          {{ detail.assembly.name }}
        </el-descriptions-item>
        <el-descriptions-item label="客户">
          {{ detail.assembly.customer_path || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="申请人">
          {{ detail.assembly.applicant_name || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="子零件数">
          <el-tag type="info" size="small" effect="plain">
            {{ detail.assembly.child_count }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="加急">
          <el-tag v-if="detail.assembly.is_urgent" type="danger" size="small" effect="dark">加急</el-tag>
          <span v-else class="muted">否</span>
        </el-descriptions-item>
        <el-descriptions-item label="请购日期">
          {{ detail.assembly.request_date }}
        </el-descriptions-item>
        <el-descriptions-item label="计划交期">
          {{ detail.assembly.planned_delivery_date }}
        </el-descriptions-item>
        <el-descriptions-item label="实际送货">
          {{ detail.assembly.actual_delivery_date || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="创建时间">
          {{ formatDateTime(detail.assembly.created_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="更新时间">
          {{ formatDateTime(detail.assembly.updated_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="ID">
          <span class="mono">{{ detail.assembly.id }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <FileListCard
      :files="masterFiles"
      owner-type="assembly"
      :owner-id="assemblyId"
      :show-upload="false"
      :show-delete="false"
      kind="ASSEMBLY_MASTER"
      @refresh="fetchData"
    />

    <!-- 上传总装 PDF：仅当装配体当前没有 master + 没有子件时才可上传 -->
    <el-card v-if="canUploadTotalPdf" shadow="never" class="upload-pdf-card">
      <div class="upload-pdf-row">
        <div class="upload-pdf-hint">
          <el-icon><Upload /></el-icon>
          <span>
            该装配体暂无总装 PDF。
            <strong>上传 PDF 后系统会自动按页拆分子件</strong>（第 1 页 = 总装图，第 2..N 页 = 子件 01、02…）。
          </span>
        </div>
        <el-upload
          :show-file-list="false"
          :auto-upload="false"
          :on-change="onUploadTotalPdf"
          accept=".pdf"
        >
          <el-button type="primary" :loading="uploadingPdf">
            <el-icon><Upload /></el-icon>
            <span>上传总装 PDF（自动拆分子件）</span>
          </el-button>
        </el-upload>
      </div>
    </el-card>

    <el-card shadow="never" class="children-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">
            子零件
            <el-tag v-if="detail" type="info" size="small" effect="plain">
              {{ detail.children.length }} 个
            </el-tag>
          </span>
          <div class="card-actions">
            <el-button type="primary" plain @click="openAddChildDialog" :disabled="!canAddChild">
              <el-icon><Plus /></el-icon>
              <span>添加子件</span>
            </el-button>
          </div>
        </div>
      </template>
      <ResponsiveList
        :items="detail?.children ?? []"
        row-key="id"
        empty-text="暂无子零件"
        :card-class="(row) => (row.is_urgent ? 'rl-card--urgent' : '')"
        border
        stripe
        size="small"
        :row-class-name="childRowClass"
      >
        <el-table-column type="index" label="#" width="50" />
        <el-table-column label="序列号" min-width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.serial_no" type="success" size="small" effect="dark">
              {{ row.serial_no }}
            </el-tag>
            <span v-else class="muted">未分配</span>
          </template>
        </el-table-column>
        <el-table-column label="图号" min-width="160" align="center">
          <template #default="{ row }">
            <el-link
              v-if="childDrawingMap[row.id]"
              type="primary"
              @click="onChildDrawingClick(row, childDrawingMap[row.id]!)"
            >
              {{ row.drawing_no }}
            </el-link>
            <span v-else class="mono">{{ row.drawing_no }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip align="center"/>
        <el-table-column prop="quantity" label="数量" min-width="70" align="right" />
        <el-table-column label="状态" min-width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="partStatusTagType(row.status)" size="small">
              {{ partStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="计划交期" min-width="120" align="center">
          <template #default="{ row }">{{ row.planned_delivery_date }}</template>
        </el-table-column>
        <el-table-column label="所在位置" min-width="160" align="center">
          <template #default="{ row }">
            <span v-if="row.current_holder_display">{{ row.current_holder_display }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="80" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">
              详情
            </el-button>
          </template>
        </el-table-column>

        <!-- 手机卡片 -->
        <template #card="{ row }">
          <div class="rl-card-head">
            <span class="rl-card-title">
              <el-link
                v-if="childDrawingMap[row.id]"
                type="primary"
                @click="onChildDrawingClick(row, childDrawingMap[row.id]!)"
              >
                {{ row.drawing_no }}
              </el-link>
              <span v-else>{{ row.drawing_no }}</span>
            </span>
            <el-tag :type="partStatusTagType(row.status)" size="small">
              {{ partStatusLabel(row.status) }}
            </el-tag>
          </div>
          <div class="rl-card-sub">
            {{ row.name }}
          </div>
          <div class="rl-kv">
            <div class="rl-kv__item">
              <span class="rl-kv__key">序列号</span>
              <span class="rl-kv__val">
                <el-tag v-if="row.serial_no" type="success" size="small" effect="dark">
                  {{ row.serial_no }}
                </el-tag>
                <span v-else class="muted">未分配</span>
              </span>
            </div>
            <div class="rl-kv__item">
              <span class="rl-kv__key">数量</span>
              <span class="rl-kv__val">{{ row.quantity }}</span>
            </div>
            <div class="rl-kv__item">
              <span class="rl-kv__key">计划交期</span>
              <span class="rl-kv__val">{{ row.planned_delivery_date || '—' }}</span>
            </div>
            <div class="rl-kv__item rl-kv__item--full">
              <span class="rl-kv__key">所在位置</span>
              <span class="rl-kv__val">{{ row.current_holder_display || '—' }}</span>
            </div>
          </div>
          <div class="rl-card-actions">
            <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">
              详情
            </el-button>
          </div>
        </template>
      </ResponsiveList>
    </el-card>

    <!-- 编辑元数据对话框（CLERK + MANAGER） -->
    <el-dialog
      v-model="editVisible"
      title="编辑装配件元数据"
      :width="editDlg.width.value"
      :top="editDlg.top.value"
      :fullscreen="editDlg.fullscreen.value"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form
        ref="editFormRef"
        :model="editForm"
        :rules="editRules"
        label-width="100px"
      >
        <el-form-item label="总图图号" prop="drawing_no">
          <el-input v-model="editForm.drawing_no" placeholder="例如：E42FX1020107101" />
        </el-form-item>
        <el-form-item label="装配体名称" prop="name">
          <el-input v-model="editForm.name" placeholder="例如：精研挡料座" />
        </el-form-item>
        <el-form-item label="客户" prop="customer_id">
          <el-select
            v-model="editForm.customer_id"
            filterable
            placeholder="选择二级客户"
            style="width: 100%"
          >
            <el-option
              v-for="c in leafCustomers"
              :key="c.id"
              :label="customerOptionLabel(c)"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="申请人">
          <el-autocomplete
            v-model="editForm.applicant_name"
            :fetch-suggestions="queryApplicants"
            placeholder="输入或选择申请人"
            value-key="name"
            style="width: 100%"
            @select="onApplicantSelected"
          />
          <!-- applicant_id 留作隐藏 / 调试用；选 applicant 时自动同步 -->
        </el-form-item>
        <el-form-item label="请购日期" prop="request_date">
          <el-date-picker
            v-model="editForm.request_date"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="计划交期" prop="planned_delivery_date">
          <el-date-picker
            v-model="editForm.planned_delivery_date"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="实际送货">
          <el-date-picker
            v-model="editForm.actual_delivery_date"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="加急" prop="is_urgent">
          <el-switch v-model="editForm.is_urgent" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="editSubmitting"
          @click="onEditSubmit"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 取消 / 删除 共用确认对话框 -->
    <el-dialog
      v-model="confirmVisible"
      :title="confirmAction === 'cancel' ? '取消装配件' : '删除装配件'"
      :width="confirmDlg.width.value"
      :top="confirmDlg.top.value"
      :fullscreen="confirmDlg.fullscreen.value"
      :close-on-click-modal="false"
    >
      <p class="confirm-hint">
        {{
          confirmAction === 'cancel'
            ? `确认取消装配件「${detail?.assembly.name ?? ''}」？将级联取消 ${detail?.assembly.child_count ?? 0} 个非终态子件。`
            : `确认删除装配件「${detail?.assembly.name ?? ''}」？将级联软删 ${detail?.assembly.child_count ?? 0} 个子零件 + 全部关联文件。`
        }}
      </p>
      <el-form label-width="96px" style="margin-top: 16px">
        <el-form-item label="序列号">
          <el-input
            v-model="confirmSerial"
            :placeholder="`请输入装配件序列号：${detail?.assembly.serial_no ?? ''}`"
            clearable
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="confirmVisible = false">取消</el-button>
        <el-button
          :type="confirmAction === 'cancel' ? 'warning' : 'danger'"
          :loading="confirmSubmitting"
          :disabled="!confirmSerial.trim()"
          @click="onConfirmSubmit"
        >
          {{ confirmAction === 'cancel' ? '确认取消' : '确认删除' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 子件图号直接预览 PDF（全屏） -->
    <el-dialog
      v-model="drawingPreviewVisible"
      :title="drawingPreviewFile?.original_filename ?? '图纸预览'"
      fullscreen
      :close-on-click-modal="false"
      destroy-on-close
      @closed="onDrawingPreviewClosed"
    >
      <PdfViewer
        v-if="drawingPreviewFile && drawingPreviewBlobUrl"
        :url="drawingPreviewBlobUrl"
        :page="1"


      />
      <div v-else class="loading-tip">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>加载中…</span>
      </div>
    </el-dialog>

    <!-- 添加子件对话框 -->
    <el-dialog
      v-model="addChildVisible"
      title="添加子件"
      :width="addChildDlg.width.value"
      :top="addChildDlg.top.value"
      :fullscreen="addChildDlg.fullscreen.value"
      :close-on-click-modal="false"
    >
      <p class="confirm-hint">
        为本装配体添加一个子件。如需为该子件上传 PDF，请到子件详情页使用「上传图纸」按钮。
      </p>
      <el-form ref="addChildFormRef" :model="addChildForm" :rules="addChildRules" label-width="96px">
        <el-form-item label="图号" prop="drawing_no">
          <el-input v-model="addChildForm.drawing_no" placeholder="例如：E42FX1020107101-1" />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="addChildForm.name" placeholder="例如：基础板" />
        </el-form-item>
        <el-form-item label="数量" prop="quantity">
          <el-input-number v-model="addChildForm.quantity" :min="1" :step="1" controls-position="right" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addChildVisible = false">取消</el-button>
        <el-button type="primary" :loading="addChildSubmitting" @click="onAddChildSubmit">
          添加
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ElMessage,
  type FormInstance,
  type FormRules,
  type UploadFile,
} from 'element-plus'
import { Back, CircleClose, Delete, Edit, Loading, Plus, Upload } from '@element-plus/icons-vue'
import FileListCard from '@/components/FileListCard.vue'
import PdfViewer from '@/components/PdfViewer.vue'
import {
  addAssemblyChild,
  cancelAssembly,
  getAssembly,
  softDeleteAssembly,
  updateAssembly,
  uploadAssemblyPdf,
} from '@/api/assembly'
import { api } from '@/api/http'
import { listCustomers } from '@/api/customer'
import type { PartFileItem } from '@/types/part_file'
import { useApplicantSearch } from '@/composables/useApplicantSearch'
import {
  ASSEMBLY_STATUS_LABEL,
  ASSEMBLY_STATUS_TAG_TYPE,
  type AssemblyDetail,
  type AssemblyStatus,
  type AssemblyUpdatePayload,
} from '@/types/assembly'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  type OrderStatus,
} from '@/types/parts'
import { useAuthSession } from '@/composables/useAuthSession'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'
import ResponsiveList from '@/components/ResponsiveList.vue'

const route = useRoute()
const router = useRouter()
const { hasRole } = useAuthSession()

// ============ 响应式 ============
const { isMobile, isTablet } = useBreakpoint()
const descCol = computed(() => (isMobile.value ? 1 : isTablet.value ? 2 : 3))

// 各 dialog 独立的响应式宽度（保留桌面固定 px）
const editDlg = useDialogSize({ desktopWidth: 640, fullscreenOnMobile: true })
const confirmDlg = useDialogSize({ desktopWidth: 480, fullscreenOnMobile: true })
const addChildDlg = useDialogSize({ desktopWidth: 480, fullscreenOnMobile: true })

// 权限：取消 / 添加子件 / 上传 PDF = CLERK+；删除 = MANAGER-only。
const canCancel = computed(
  () => hasRole('CLERK') || hasRole('MANAGER'),
)
const canDelete = computed(() => hasRole('MANAGER'))
const canEditContent = computed(
  () => hasRole('CLERK') || hasRole('MANAGER'),
)

const detail = ref<AssemblyDetail | null>(null)
const loading = ref(false)

const assemblyId = computed<string>(() => {
  const raw = route.params.id
  return String(Array.isArray(raw) ? raw[0] : raw ?? '')
})

/** 状态 → 中文 label / el-tag type（与装配体一览同款）。 */
function statusLabel(s: AssemblyStatus | string): string {
  return ASSEMBLY_STATUS_LABEL[s as AssemblyStatus] ?? String(s)
}
function statusTagType(s: AssemblyStatus | string): 'info' | 'warning' | 'success' | 'danger' | 'primary' {
  return ASSEMBLY_STATUS_TAG_TYPE[s as AssemblyStatus] ?? 'info'
}
function partStatusLabel(s: OrderStatus): string {
  return ORDER_STATUS_LABEL[s] ?? s
}
function partStatusTagType(s: OrderStatus): 'success' | 'warning' | 'info' | 'danger' | 'primary' {
  return ORDER_STATUS_TAG_TYPE[s] ?? 'info'
}

/** 是否可上传总装 PDF：当前无 master 且无子件。 */
const canUploadTotalPdf = computed(() => {
  if (!canEditContent.value) return false
  if (!detail.value) return false
  if (detail.value.assembly.serial_no) return false  // 已经分过 serial 不能再上传
  if (detail.value.files.length > 0) return false
  if (detail.value.children.length > 0) return false
  return detail.value.assembly.status === 'PENDING'
})

/** 是否可添加子件：装配体未终态。 */
const canAddChild = computed(() => {
  if (!canEditContent.value) return false
  if (!detail.value) return false
  return detail.value.assembly.status === 'PENDING'
})

function childRowClass({ row }: { row: { is_urgent: boolean } }): string {
  return row.is_urgent ? 'row-urgent' : ''
}

function formatDateTime(iso: string): string {
  if (!iso) return '—'
  try {
    return iso.replace('T', ' ').slice(0, 19)
  } catch {
    return iso
  }
}

// ===== 图纸卡片过滤 + 子件图号图号直接预览（2026-07-11 接入） =====
/** 总装图（kind=ASSEMBLY_MASTER）—— FileListCard 只显示 master。 */
const masterFiles = computed<PartFileItem[]>(
  () => (detail.value?.files ?? []).filter((f) => f.kind === 'ASSEMBLY_MASTER') as PartFileItem[],
)

/** 子件 → DRAWING 映射（按 owner_id 索引；多文件取最新一条）。 */
const childDrawingMap = computed<Record<string, PartFileItem>>(() => {
  const m: Record<string, PartFileItem> = {}
  for (const f of detail.value?.files ?? []) {
    if (f.kind === 'DRAWING' && f.owner_id) {
      // repository 端 list_for_assembly 已按 id DESC，单 key 直接覆盖即可（最新一条胜）
      m[f.owner_id] = f as PartFileItem
    }
  }
  return m
})

/** 子件图号点击 → 全屏 PDF 预览（不走详情页）。 */
const drawingPreviewVisible = ref(false)
const drawingPreviewFile = ref<PartFileItem | null>(null)
const drawingPreviewBlobUrl = ref('')

async function onChildDrawingClick(
  _row: unknown,
  drawing: PartFileItem,
): Promise<void> {
  drawingPreviewFile.value = drawing
  drawingPreviewVisible.value = true
  try {
    const resp = await api.get<Blob>(`/files/${drawing.id}/content`, {
      responseType: 'blob',
    })
    if (drawingPreviewBlobUrl.value) {
      URL.revokeObjectURL(drawingPreviewBlobUrl.value)
    }
    drawingPreviewBlobUrl.value = URL.createObjectURL(resp.data)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载图纸失败')
    drawingPreviewVisible.value = false
  }
}

function onDrawingPreviewClosed(): void {
  if (drawingPreviewBlobUrl.value) {
    URL.revokeObjectURL(drawingPreviewBlobUrl.value)
  }
  drawingPreviewBlobUrl.value = ''
  drawingPreviewFile.value = null
}

async function fetchData(): Promise<void> {
  if (!assemblyId.value) return
  loading.value = true
  try {
    detail.value = await getAssembly(assemblyId.value)
  } catch (e) {
    detail.value = null
    ElMessage.error((e as Error).message ?? '加载装配件详情失败')
  } finally {
    loading.value = false
  }
}

// ===== 取消 / 删除 确认对话框（共用，confirmAction 区分） =====
const confirmVisible = ref(false)
const confirmAction = ref<'cancel' | 'delete'>('cancel')
const confirmSerial = ref('')
const confirmSubmitting = ref(false)

function openConfirmDialog(action: 'cancel' | 'delete'): void {
  confirmAction.value = action
  confirmSerial.value = ''
  confirmVisible.value = true
}

async function onConfirmSubmit(): Promise<void> {
  if (!detail.value) return
  const a = detail.value.assembly
  if (!a.serial_no) {
    ElMessage.warning('该装配体暂无序列号，无法执行此操作')
    confirmVisible.value = false
    return
  }
  if (confirmSerial.value.trim() !== a.serial_no) {
    ElMessage.error('序列号不匹配')
    return
  }
  confirmSubmitting.value = true
  try {
    if (confirmAction.value === 'cancel') {
      const updated = await cancelAssembly(a.id)
      ElMessage.success('已取消装配件')
      detail.value = updated
    } else {
      await softDeleteAssembly(a.id)
      ElMessage.success('已删除')
      router.push('/parts')
    }
    confirmVisible.value = false
  } catch (e) {
    ElMessage.error((e as Error).message ?? '操作失败')
  } finally {
    confirmSubmitting.value = false
  }
}

// ===== 上传总装 PDF（拆页 + 自动创建子件） =====
const uploadingPdf = ref(false)

async function onUploadTotalPdf(uploadFile: UploadFile): Promise<void> {
  if (!uploadFile.raw) return
  if (!uploadFile.name.toLowerCase().endsWith('.pdf')) {
    ElMessage.error('总装文件必须是 .pdf 后缀')
    return
  }
  if (!assemblyId.value) return
  uploadingPdf.value = true
  try {
    const updated = await uploadAssemblyPdf(assemblyId.value, uploadFile.raw)
    detail.value = updated
    ElMessage.success(
      `上传成功：自动创建 ${updated.children.length} 个子件`,
    )
  } catch (e) {
    ElMessage.error((e as Error).message ?? '上传总装 PDF 失败')
  } finally {
    uploadingPdf.value = false
  }
}

// ===== 添加子件对话框 =====
const addChildVisible = ref(false)
const addChildSubmitting = ref(false)
const addChildFormRef = ref<FormInstance>()
const addChildForm = reactive({
  drawing_no: '',
  name: '',
  quantity: 1,
})
const addChildRules: FormRules = {
  drawing_no: [{ required: true, message: '请输入图号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
}

function openAddChildDialog(): void {
  addChildForm.drawing_no = ''
  addChildForm.name = ''
  addChildForm.quantity = 1
  addChildVisible.value = true
}

async function onAddChildSubmit(): Promise<void> {
  if (!addChildFormRef.value || !assemblyId.value) return
  try {
    await addChildFormRef.value.validate()
  } catch {
    return
  }
  addChildSubmitting.value = true
  try {
    await addAssemblyChild(assemblyId.value, {
      drawing_no: addChildForm.drawing_no.trim(),
      name: addChildForm.name.trim(),
      quantity: addChildForm.quantity,
    })
    ElMessage.success('子件已添加')
    addChildVisible.value = false
    await fetchData()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '添加子件失败')
  } finally {
    addChildSubmitting.value = false
  }
}

// ===== 编辑元数据对话框（CLERK + MANAGER；终态由后端 BIZ_INVALID_TRANSITION 拦截） =====
const editVisible = ref(false)
const editSubmitting = ref(false)
const editFormRef = ref<FormInstance>()
// 表单本地状态：所有可空字段用空字符串占位，避免与 el-* 组件的 v-model
// （只接受 string / number / undefined）类型冲突；提交前把空字符串转回 null。
interface EditFormShape {
  drawing_no: string
  name: string
  customer_id: string
  applicant_name: string
  applicant_id: string
  request_date: string
  planned_delivery_date: string
  actual_delivery_date: string
  is_urgent: boolean
}
const editForm = reactive<EditFormShape>({
  drawing_no: '',
  name: '',
  customer_id: '',
  applicant_name: '',
  applicant_id: '',
  request_date: '',
  planned_delivery_date: '',
  actual_delivery_date: '',
  is_urgent: false,
})
const editRules: FormRules = {
  drawing_no: [{ required: true, message: '请输入总图图号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入装配体名称', trigger: 'blur' }],
  customer_id: [{ required: true, message: '请选择客户', trigger: 'change' }],
}

/** 装配体当前一级客户下拉（仅叶子）；从 listCustomers 派生。 */
const leafCustomers = ref<Array<{ id: string; name: string; parent_name: string | null }>>([])
async function loadLeafCustomers(): Promise<void> {
  const all = await listCustomers()
  leafCustomers.value = all
    .filter((c) => c.parent_id !== null)
    .map((c) => ({ id: c.id, name: c.name, parent_name: c.parent_name }))
}
function customerOptionLabel(c: { name: string; parent_name: string | null }): string {
  return c.parent_name ? `${c.parent_name} / ${c.name}` : c.name
}

/** 申请人搜索：装配体一级客户下全集，按子串过滤。 */
const { querySearch: queryApplicants } = useApplicantSearch({
  // 装配体的当前 customer_id 必须先存在（resolve 到一级客户）；但编辑流
  // 中 customer 也允许改，所以这里 fallback：先用当前客户拉一次，没选好客户
  // 就返回空列表。详情页加载时已锁定 customer。
  resolveRootCustomerId: () => null,
})

function onApplicantSelected(applicant: { id?: string; name?: string }): void {
  if (applicant?.id != null) {
    ;(editForm as { applicant_id?: string | null }).applicant_id = String(applicant.id)
  }
}

async function openEditDialog(): Promise<void> {
  if (!detail.value) return
  const a = detail.value.assembly
  Object.assign(editForm, {
    drawing_no: a.drawing_no,
    name: a.name,
    customer_id: a.customer_id,
    applicant_name: a.applicant_name ?? '',
    applicant_id: '',
    request_date: a.request_date,
    planned_delivery_date: a.planned_delivery_date,
    actual_delivery_date: a.actual_delivery_date ?? '',
    is_urgent: a.is_urgent,
  })
  editVisible.value = true
  try {
    await loadLeafCustomers()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载客户列表失败')
  }
}

async function onEditSubmit(): Promise<void> {
  if (!editFormRef.value || !assemblyId.value) return
  try {
    await editFormRef.value.validate()
  } catch {
    return
  }
  editSubmitting.value = true
  try {
    // 把表单内部空字符串 / falsy 还原成 payload schema 的 null / undefined 语义。
    const payload: AssemblyUpdatePayload = {
      drawing_no: editForm.drawing_no,
      name: editForm.name,
      customer_id: editForm.customer_id,
      applicant_name: editForm.applicant_name || null,
      applicant_id: editForm.applicant_id || null,
      request_date: editForm.request_date,
      planned_delivery_date: editForm.planned_delivery_date,
      actual_delivery_date: editForm.actual_delivery_date || null,
      is_urgent: editForm.is_urgent,
    }
    const updated = await updateAssembly(assemblyId.value, payload)
    detail.value = updated
    ElMessage.success('已保存')
    editVisible.value = false
  } catch (e) {
    ElMessage.error((e as Error).message ?? '保存失败')
  } finally {
    editSubmitting.value = false
  }
}

watch(() => route.params.id, fetchData)
onMounted(fetchData)
</script>

<style lang="scss" scoped>
.assembly-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.info-card,
.children-card,
.upload-pdf-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
  }
}
.upload-pdf-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.upload-pdf-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-regular);
  font-size: 13px;
  flex: 1;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.card-actions {
  display: flex;
  gap: 8px;
}
.mono {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: var(--text-regular);
}
.muted {
  color: var(--text-secondary);
}
:deep(.el-table__row.row-urgent) > td.el-table__cell {
  background-color: #fde2e2 !important;
}
:deep(.el-table__row.row-urgent:hover > td.el-table__cell) {
  background-color: #fbcaca !important;
}
</style>