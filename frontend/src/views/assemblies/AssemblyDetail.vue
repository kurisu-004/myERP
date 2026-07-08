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
            <el-button @click="$router.push('/assemblies')">
              <el-icon><Back /></el-icon>
              <span>返回列表</span>
            </el-button>
            <!-- 取消（CLERK+）：PENDING/IN_PROCESS 可触发 -->
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

      <el-descriptions :column="3" border>
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
      :files="detail?.files ?? []"
      owner-type="assembly"
      :owner-id="assemblyId"
      :show-upload="true"
      :show-delete="true"
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
      <el-table
        :data="detail?.children ?? []"
        border
        stripe
        size="small"
        :row-class-name="childRowClass"
      >
        <el-table-column type="index" label="#" width="50" />
        <el-table-column label="序列号" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.serial_no" type="success" size="small" effect="dark">
              {{ row.serial_no }}
            </el-tag>
            <span v-else class="muted">未分配</span>
          </template>
        </el-table-column>
        <el-table-column label="图号" width="160">
          <template #default="{ row }">
            <el-link type="primary" @click="$router.push(`/parts/${row.id}`)">
              {{ row.drawing_no }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="quantity" label="数量" width="70" align="right" />
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="partStatusTagType(row.status)" size="small">
              {{ partStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="计划交期" width="120">
          <template #default="{ row }">{{ row.planned_delivery_date }}</template>
        </el-table-column>
        <el-table-column label="加急" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_urgent" type="danger" size="small" effect="dark">加急</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 取消 / 删除 共用确认对话框 -->
    <el-dialog
      v-model="confirmVisible"
      :title="confirmAction === 'cancel' ? '取消装配件' : '删除装配件'"
      width="480px"
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

    <!-- 添加子件对话框 -->
    <el-dialog
      v-model="addChildVisible"
      title="添加子件"
      width="480px"
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
import { Back, CircleClose, Delete, Plus, Upload } from '@element-plus/icons-vue'
import FileListCard from '@/components/FileListCard.vue'
import {
  addAssemblyChild,
  cancelAssembly,
  getAssembly,
  softDeleteAssembly,
  uploadAssemblyPdf,
} from '@/api/assembly'
import {
  ASSEMBLY_STATUS_LABEL,
  ASSEMBLY_STATUS_TAG_TYPE,
  type AssemblyDetail,
  type AssemblyStatus,
} from '@/types/assembly'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  type OrderStatus,
} from '@/types/parts'
import { useAuthSession } from '@/composables/useAuthSession'

const route = useRoute()
const router = useRouter()
const { hasRole } = useAuthSession()

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
      router.push('/assemblies')
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