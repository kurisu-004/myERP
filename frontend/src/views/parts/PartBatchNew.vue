<!--
  PartBatchNew.vue

  /parts/new  批量新建零件页（与 /parts 并列）。

  流程：
  1. 点空白区 / 「+ 添加零件」 → 弹出 Dialog 填写一条零件（含图纸上传）
  2. Dialog 确定 → 校验通过后入队到「待新增零件」表
  3. 点表格中任意行 → 弹出只读预览 Dialog（含图纸预览）
  4. 全部填好 → 点底部「提交 N 条」 → POST /api/v1/parts/batch（multipart）
  5. 成功 → 清空列表 + 跳回 /parts；失败 → 弹窗列出失败行

  2026-07-09 起：图纸在提交时通过 multipart/form-data 一起上行到后端
  （`data` JSON 字符串 + `files` PDF 数组，按 items 下标对齐）。
-->

<template>
  <div class="batch-new">
    <p class="hint">
      点击下方空白区域或「+ 添加零件」按钮，逐条录入零件信息（含图纸），最后统一提交。
    </p>

    <el-card shadow="never" class="staging-card">
      <div class="staging-header">
        <div class="staging-title-wrap">
          <h3 class="staging-title">待新增零件</h3>
          <span class="staging-count">共 {{ staged.length }} 条</span>
        </div>
        <div class="staging-header-actions">
          <el-button @click="router.push('/parts/new/bid-import')">
            <el-icon><Document /></el-icon>
            <span>从应标 Excel 导入</span>
          </el-button>
          <el-button type="primary" @click="openAddDialog">
            <el-icon><Plus /></el-icon>
            <span>添加零件</span>
          </el-button>
        </div>
      </div>

      <!-- 空态：点空白处打开 dialog -->
      <div
        v-if="staged.length === 0"
        class="empty-zone"
        @click="openAddDialog"
      >
        <el-icon :size="64" color="#c0c4cc"><DocumentAdd /></el-icon>
        <p class="empty-primary">暂无待新增零件</p>
        <p class="empty-sub">点击此处或右上角「+ 添加零件」开始添加</p>
      </div>

      <!-- 列表态 -->
      <el-table
        v-else
        :data="staged"
        border
        stripe
        size="small"
        :row-class-name="rowClassName"
        @row-click="onRowPreview"
      >
        <el-table-column type="index" label="#" width="50" />
        <el-table-column label="图号" width="130">
          <template #default="{ row }">
            <el-button
              v-if="(row as StagedEntry).drawingUrl"
              link type="primary" size="small"
              @click.stop="openDrawingPreview(row as StagedEntry)"
            >
              {{ row.drawingNo }}
            </el-button>
            <span v-else class="mono">{{ row.drawingNo }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="quantity" label="数量" width="70" align="right" />
        <el-table-column label="申请人" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.applicantName || '—' }}</template>
        </el-table-column>
        <el-table-column label="客户" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.customerLabel || '—' }}</template>
        </el-table-column>
        <el-table-column prop="plannedDeliveryDate" label="计划交期" width="120" />
        <el-table-column label="加急" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.isUrgent" type="danger" size="small" effect="dark">加急</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click.stop="onRowPreview(row as StagedEntry)">查看</el-button>
            <el-button link type="danger" size="small" @click.stop="onRemoveRow((row as StagedEntry).uid)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="staging-footer">
        <el-button :disabled="staged.length === 0 || submitting" @click="onClearAll">
          清空
        </el-button>
        <el-button
          type="primary"
          :loading="submitting"
          :disabled="staged.length === 0"
          @click="onSubmit"
        >
          <el-icon><Upload /></el-icon>
          <span>提交 {{ staged.length }} 条</span>
        </el-button>
      </div>
    </el-card>

    <!-- 添加 / 编辑 Dialog -->
    <el-dialog
      v-model="addDialogVisible"
      :title="editingUid ? '编辑零件' : '添加零件'"
      width="900px"
      :close-on-click-modal="false"
      @closed="onDialogClosed"
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="100px"
        label-position="right"
      >
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="图号" prop="drawingNo">
              <el-input v-model="form.drawingNo" placeholder="例如：LT39822" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="名称" prop="name">
              <el-input v-model="form.name" placeholder="请输入品名 / 零件名称" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="客户" prop="customerId">
              <el-cascader
                v-model="form.customerId"
                :options="customerTree"
                :props="{ value: 'id', label: 'name', children: 'children', checkStrictly: true, emitPath: false }"
                placeholder="选择一级 / 二级客户"
                style="width: 100%"
                clearable
                @change="onCustomerChange"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="申请人" prop="applicantName">
              <el-autocomplete
                v-model="form.applicantName"
                value-key="name"
                :fetch-suggestions="querySearch"
                :trigger-on-focus="true"
                :debounce="0"
                :loading="applicantLoading"
                :disabled="!form.customerId"
                placeholder="选择或输入申请人姓名（不在表中则提交时自动新增）"
                style="width: 100%"
                clearable
                @select="onApplicantSelect"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="数量" prop="quantity">
              <el-input-number v-model="form.quantity" :min="1" :step="1" controls-position="right" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="加急">
              <el-switch v-model="form.isUrgent" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="请购日期" prop="requestDate">
              <el-date-picker
                v-model="form.requestDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="请选择"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="计划交期" prop="plannedDeliveryDate">
              <el-date-picker
                v-model="form.plannedDeliveryDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="请选择"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <!-- 送货单字段（PR-F 2026-07-17） -->
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="订单号">
              <el-input v-model="form.orderNo" placeholder="如 6200037950（可选）" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="系统交期">
              <el-date-picker
                v-model="form.systemDeliveryDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="订单方系统内部交期（可选）"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="备注">
          <el-input v-model="form.note" placeholder="文员手填备注（可选，送货单可见）" />
        </el-form-item>

        <el-form-item label="图纸">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            :on-change="onDrawingChange"
            :on-remove="onDrawingRemoveUpload"
            :before-upload="beforeDrawingUpload"
            accept=".pdf"
          >
            <el-button>
              <el-icon><Upload /></el-icon>
              <span>{{ form.drawingName ? '更换图纸' : '选择图纸' }}</span>
            </el-button>
          </el-upload>
          <div v-if="form.drawingName" class="drawing-info">
            <el-icon><Picture /></el-icon>
            <span class="drawing-name">{{ form.drawingName }}</span>
            <el-button link type="danger" size="small" @click="onDrawingRemove">移除</el-button>
          </div>
          <p class="form-hint">仅支持 PDF；提交时自动随表图号列点击预览（待新增一览 → 点图号）。</p>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="addDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="dialogSubmitting" @click="onAddConfirm">
          {{ editingUid ? '保存到列表' : '加入列表' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 图纸 PDF 预览 Dialog -->
    <el-dialog
      v-model="drawingPreviewVisible"
      :title="`图纸预览 — ${drawingPreviewRow?.drawingNo ?? ''}`"
      fullscreen
      destroy-on-close
      @closed="onDrawingPreviewClosed"
    >
      <PdfViewer
        v-if="drawingPreviewRow?.drawingUrl"
        :url="drawingPreviewRow.drawingUrl"
        :page="1"


      />
    </el-dialog>

    <!-- 预览 Dialog（只读，全屏，含图纸预览） -->
    <el-dialog v-model="previewDialogVisible" title="预览零件" fullscreen>
      <el-descriptions v-if="previewing" :column="2" border>
        <el-descriptions-item label="图号">{{ previewing.drawingNo }}</el-descriptions-item>
        <el-descriptions-item label="名称">{{ previewing.name }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ previewing.applicantName || '—' }}</el-descriptions-item>
        <el-descriptions-item label="客户">{{ previewing.customerLabel || '—' }}</el-descriptions-item>
        <el-descriptions-item label="数量">{{ previewing.quantity }}</el-descriptions-item>
        <el-descriptions-item label="加急">
          <el-tag v-if="previewing.isUrgent" type="danger" size="small" effect="dark">加急</el-tag>
          <span v-else class="muted">否</span>
        </el-descriptions-item>
        <el-descriptions-item label="请购日期">{{ previewing.requestDate }}</el-descriptions-item>
        <el-descriptions-item label="计划交期">{{ previewing.plannedDeliveryDate }}</el-descriptions-item>
        <el-descriptions-item label="图纸" :span="2">
          <PdfViewer
            v-if="previewing.drawingUrl"
            :url="previewing.drawingUrl"
            :page="1"


          />
          <span v-else class="muted">未上传</span>
        </el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="previewDialogVisible = false">关闭</el-button>
        <el-button type="primary" @click="onEditFromPreview">编辑此条</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElMessage,
  ElMessageBox,
  type FormInstance,
  type FormRules,
  type UploadFile,
} from 'element-plus'
import { DocumentAdd, Picture, Plus, Upload } from '@element-plus/icons-vue'
import PdfViewer from '@/components/PdfViewer.vue'
import { batchCreateParts, type PartBatchFilePayload, type PartCreatePayload } from '@/api/parts'
import { listCustomers, type Customer } from '@/api/customer'
import { createApplicant } from '@/api/applicant'
import { useApplicantSearch } from '@/composables/useApplicantSearch'

const router = useRouter()

// ============ 客户树 ============
const customers = ref<Customer[]>([])
const customerTree = computed(() => {
  const roots = customers.value.filter((c) => c.parent_id === null)
  return roots.map((r) => ({
    id: r.id,
    name: r.name,
    children: customers.value
      .filter((c) => c.parent_id === r.id)
      .map((c) => ({ id: c.id, name: c.name })),
  }))
})

/** 把 cascader 选中的客户 id（可能是叶子）解析到所属的一级客户 id。
 * 入参 / 出参都是雪花 ID 字符串（CLAUDE.md §3）。
 */
function resolveRootCustomerId(pickedId: string | null): string | null {
  if (pickedId === null || pickedId === undefined || pickedId === '') return null
  const picked = customers.value.find((c) => c.id === pickedId)
  if (!picked) return null
  if (picked.parent_id === null) return picked.id
  return picked.parent_id
}

async function loadCustomers(): Promise<void> {
  try {
    customers.value = await listCustomers()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '客户列表加载失败')
  }
}

onMounted(() => {
  void loadCustomers()
})

// ============ 申请人候选（composable：只在客户切换时拉一次） ============
const {
  applicants: applicantCandidates,
  loading: applicantLoading,
  loadForCustomer: loadApplicantsForCustomer,
  querySearch,
} = useApplicantSearch({ resolveRootCustomerId })

async function onCustomerChange(pickedId: unknown): Promise<void> {
  // cascader emitPath:false → string id；但 Element Plus 类型声明是 CascaderValue
  const raw = Array.isArray(pickedId) ? pickedId[pickedId.length - 1] : pickedId
  const idStr = raw === null || raw === undefined ? '' : String(raw)
  form.applicantId = null
  form.applicantName = ''
  await loadApplicantsForCustomer(idStr || null)
}

function onApplicantSelect(item: Record<string, unknown>): void {
  form.applicantId = String(item.id)
  // form.applicantName 由 v-model 自动同步为 item.name，无需手动设
}

// ============ 待新增列表 ============
interface StagedEntry {
  uid: string
  drawingNo: string
  name: string
  applicantName: string
  applicantId: string | null
  customerId: string | null
  customerLabel: string
  quantity: number
  isUrgent: boolean
  requestDate: string
  plannedDeliveryDate: string
  /** PR-F 2026-07-17：送货单字段 */
  orderNo: string | null
  systemDeliveryDate: string | null
  note: string | null
  drawingFile: File | null
  drawingName: string | null
  drawingUrl: string | null
}

const staged = ref<StagedEntry[]>([])

function makeUid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `uid-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function revokeEntryUrls(entry: StagedEntry): void {
  if (entry.drawingUrl) {
    try { URL.revokeObjectURL(entry.drawingUrl) } catch { /* ignore */ }
  }
}

function beforeDrawingUpload(rawFile: File & { name?: string }): boolean {
  // 仅接受 PDF（2026-07-07 起与服务端 drawing.py upload_to_part 同步）。
  // el-upload 的 before-upload 返回 false 会阻止 on-change 触发；
  // 返回 true 走 on-change（兜底再校验一次）。
  if (!rawFile?.name?.toLowerCase().endsWith('.pdf')) {
    ElMessage.error('图纸必须是 .pdf 后缀')
    return false
  }
  return true
}

// ============ Dialog 表单 ============
interface FormState {
  drawingNo: string
  name: string
  applicantName: string
  applicantId: string | null
  customerId: string | null
  quantity: number
  isUrgent: boolean
  requestDate: string
  plannedDeliveryDate: string
  /** PR-F 2026-07-17：送货单字段 */
  orderNo: string | null
  systemDeliveryDate: string | null
  note: string | null
  drawingFile: File | null
  drawingName: string | null
  drawingUrl: string | null
}

const formRef = ref<FormInstance>()
const addDialogVisible = ref(false)
const dialogSubmitting = ref(false)
const editingUid = ref<string | null>(null)

/** PDF 弹窗预览（图号列点击触发） */
const drawingPreviewVisible = ref(false)
const drawingPreviewRow = ref<StagedEntry | null>(null)
function openDrawingPreview(row: StagedEntry): void {
  drawingPreviewRow.value = row
  drawingPreviewVisible.value = true
}
function onDrawingPreviewClosed(): void {
  drawingPreviewRow.value = null
}

/** 把「今天」格式化成 YYYY-MM-DD 字符串。 */
function todayIso(): string {
  const d = new Date()
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

const initialForm = (): FormState => ({
  drawingNo: '',
  name: '',
  applicantName: '',
  applicantId: null,
  customerId: null,
  quantity: 1,
  isUrgent: false,
  requestDate: todayIso(),
  plannedDeliveryDate: '',
  orderNo: null,
  systemDeliveryDate: null,
  note: null,
  drawingFile: null,
  drawingName: null,
  drawingUrl: null,
})

const form = reactive<FormState>(initialForm())

/**
 * 保持 applicantId 与 applicantName 一致：
 * - 用户从下拉挑了某人：applicantName = item.name，applicantId = item.id（@select 设）
 * - 用户清空 / 继续打字改了名字：当前 applicantId 已不再指向同名 → 清掉
 *   → 让 onSubmit 走「自动新增」分支（PartBatchNew.vue:onSubmit 内 createApplicant 段）。
 * - onEditFromPreview 反填 staged row 时若 applicantId 已 stale，watcher 也自愈。
 *
 * 注意：本 watcher 必须在 const form 声明之后注册 —— watch 的 getter 在 setup
 * 阶段就会同步执行一次以注册 reactive 依赖，提前引用 form 会触发 TDZ。
 */
watch(
  () => form.applicantName,
  (next) => {
    const currentId = form.applicantId
    if (currentId === null) return
    const matched = applicantCandidates.value.find((a) => a.id === currentId)
    if (matched && matched.name === next) return
    form.applicantId = null
  },
)

const rules: FormRules = {
  drawingNo: [{ required: true, message: '请输入图号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  customerId: [
    {
      required: true,
      validator: (_rule, value, callback) => {
        // cascader emitPath:false 返回选中节点的 id，来自 Customer.id（string）
        if (value === null || value === undefined || value === '') {
          callback(new Error('请选择客户'))
          return
        }
        const c = customers.value.find((x) => String(x.id) === String(value))
        if (!c) {
          callback(new Error('客户不存在'))
          return
        }
        callback()
      },
      trigger: 'change',
    },
  ],
  quantity: [{ required: true, message: '请输入数量', trigger: 'blur' }],
  requestDate: [{ required: true, message: '请选择请购日期', trigger: 'change' }],
  plannedDeliveryDate: [{ required: true, message: '请选择计划交期', trigger: 'change' }],
}

function openAddDialog(): void {
  editingUid.value = null
  Object.assign(form, initialForm())
  // 申请人候选由 onCustomerChange 在客户变更时刷新；openAddDialog
  // 调 initialForm() 把 customerId 置空，所以这里无需再清缓存。
  addDialogVisible.value = true
}

function onDrawingChange(uploadFile: UploadFile): void {
  // 替换旧文件 → 撤销旧 URL
  if (form.drawingUrl) {
    try { URL.revokeObjectURL(form.drawingUrl) } catch { /* ignore */ }
  }
  form.drawingFile = uploadFile.raw ?? null
  form.drawingName = uploadFile.name
  form.drawingUrl = uploadFile.raw ? URL.createObjectURL(uploadFile.raw) : null
}
function onDrawingRemoveUpload(): void {
  // el-upload 自带 remove 按钮触发（这里 on-remove 没绑在按钮上，留作 hook）
  onDrawingRemove()
}
function onDrawingRemove(): void {
  if (form.drawingUrl) {
    try { URL.revokeObjectURL(form.drawingUrl) } catch { /* ignore */ }
  }
  form.drawingFile = null
  form.drawingName = null
  form.drawingUrl = null
}

function findCustomerLabel(id: string | null): string {
  if (id === null) return ''
  const c = customers.value.find((x) => x.id === id)
  if (!c) return ''
  return c.parent_name ? `${c.parent_name} / ${c.name}` : c.name
}

async function onAddConfirm(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  // cascader value → customerId（Customer.id 为 string，emitPath:false 返回 string）
  const rawId = form.customerId
  if (rawId === null || rawId === '') {
    ElMessage.error('请选择客户')
    return
  }
  // customerId 是雪花 ID 字符串（CLAUDE.md §3），不再转 Number。
  // 校验非空：cascader emitPath:false 返 string id，空串说明未选。
  if (!rawId) {
    ElMessage.error('请选择客户')
    return
  }
  // 申请人必填：要么选了已有 applicantId，要么输了字符串（自动新增）
  const applicantName = form.applicantName.trim()
  if (!applicantName) {
    ElMessage.error('请选择或输入申请人')
    return
  }
  dialogSubmitting.value = true
  try {
    const entry: StagedEntry = {
      uid: editingUid.value ?? makeUid(),
      drawingNo: form.drawingNo.trim(),
      name: form.name.trim(),
      applicantName,
      applicantId: form.applicantId,
      customerId: rawId,
      customerLabel: findCustomerLabel(rawId),
      quantity: form.quantity,
      isUrgent: form.isUrgent,
      requestDate: form.requestDate,
      plannedDeliveryDate: form.plannedDeliveryDate,
      orderNo: form.orderNo || null,
      systemDeliveryDate: form.systemDeliveryDate || null,
      note: form.note || null,
      drawingFile: form.drawingFile,
      drawingName: form.drawingName,
      drawingUrl: form.drawingUrl,
    }

    if (editingUid.value) {
      // 编辑模式：找到旧条目，先释放旧 URL，再替换
      const idx = staged.value.findIndex((s) => s.uid === editingUid.value)
      if (idx >= 0) {
        revokeEntryUrls(staged.value[idx])
        staged.value.splice(idx, 1, entry)
      }
    } else {
      // 新增：原 dialog 的 url 转交给 entry（已经放进 entry），把 form 上的 url 置空避免 onClosed 重复释放
      form.drawingUrl = null
      form.drawingFile = null
      form.drawingName = null
      staged.value.push(entry)
    }
    addDialogVisible.value = false
    ElMessage.success(editingUid.value ? '已更新到列表' : '已加入待新增列表')
  } finally {
    dialogSubmitting.value = false
  }
}

function onDialogClosed(): void {
  // 仅在「取消」关闭时表单上仍残留 url 才需要回收；onAddConfirm 成功后已把 url 转交
  if (form.drawingUrl) {
    try { URL.revokeObjectURL(form.drawingUrl) } catch { /* ignore */ }
  }
  formRef.value?.clearValidate()
  Object.assign(form, initialForm())
  editingUid.value = null
}

// ============ 行操作：查看 / 删除 / 编辑 ============
const previewDialogVisible = ref(false)
const previewing = ref<StagedEntry | null>(null)

function onRowPreview(row: StagedEntry): void {
  previewing.value = row
  previewDialogVisible.value = true
}

function onEditFromPreview(): void {
  const target = previewing.value
  if (!target) return
  previewDialogVisible.value = false
  // 把目标 entry 的字段塞回 form
  editingUid.value = target.uid
  Object.assign(form, {
    drawingNo: target.drawingNo,
    name: target.name,
    applicantName: target.applicantName,
    applicantId: target.applicantId,
    customerId: target.customerId,
    quantity: target.quantity,
    isUrgent: target.isUrgent,
    requestDate: target.requestDate,
    plannedDeliveryDate: target.plannedDeliveryDate,
    orderNo: target.orderNo,
    systemDeliveryDate: target.systemDeliveryDate,
    note: target.note,
    drawingFile: target.drawingFile,
    drawingName: target.drawingName,
    drawingUrl: target.drawingUrl,
  })
  addDialogVisible.value = true
  // 标记该 entry 的 url 已被 dialog 接管；切到 list 时不再 revoke 它
  // 简化处理：编辑模式下，旧 url 仍属于 entry；编辑确认时 onAddConfirm 会先 revokeEntryUrls(staged[idx])，避免泄漏
  target.drawingUrl = null
  target.drawingFile = null
  target.drawingName = null
  // 同步刷新 rootCustomerId 与申请人候选（让下拉带回原选项）
  if (target.customerId) {
    void loadApplicantsForCustomer(target.customerId)
  }
}

function onRemoveRow(uid: string): void {
  const idx = staged.value.findIndex((s) => s.uid === uid)
  if (idx < 0) return
  revokeEntryUrls(staged.value[idx])
  staged.value.splice(idx, 1)
}

function onClearAll(): void {
  ElMessageBox.confirm(`确认清空 ${staged.value.length} 条待新增记录？此操作无法撤销。`, '提示', {
    confirmButtonText: '清空',
    cancelButtonText: '取消',
    type: 'warning',
  })
    .then(() => {
      staged.value.forEach(revokeEntryUrls)
      staged.value = []
    })
    .catch(() => undefined)
}

function rowClassName({ row }: { row: unknown }): string {
  const r = row as StagedEntry
  return r.isUrgent ? 'row-urgent' : ''
}

// ============ 提交 ============
const submitting = ref(false)

async function onSubmit(): Promise<void> {
  if (staged.value.length === 0) {
    ElMessage.warning('没有可提交的待新增零件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将向服务端提交 ${staged.value.length} 条新零件，提交后系统按客户自动分配序列号。是否继续？`,
      '确认提交',
      { confirmButtonText: '提交', cancelButtonText: '取消', type: 'info' },
    )
  } catch {
    return
  }
  submitting.value = true
  try {
    // 1) 先为每条 entry 处理 applicant_id：未选现有申请人的 → 按客户解析一级
    //    后调 createApplicant 自动新增。
    for (const s of staged.value) {
      if (s.applicantId) continue
      if (!s.applicantName.trim() || !s.customerId) continue
      const rootId = resolveRootCustomerId(s.customerId)
      if (rootId === null) continue
      const created = await createApplicant({
        name: s.applicantName.trim(),
        customer_id: String(rootId),
      })
      s.applicantId = created.id
    }

    // 2) 构造批量 payload
    const items: PartCreatePayload[] = staged.value.map((s) => ({
      name: s.name,
      drawing_no: s.drawingNo,
      applicant_name: s.applicantName,
      // applicant_id 雪花 ID 19 位 → 必须用字符串，避免 JS Number 精度丢失
      applicant_id: s.applicantId,
      quantity: s.quantity,
      request_date: s.requestDate,
      planned_delivery_date: s.plannedDeliveryDate,
      is_urgent: s.isUrgent,
      /** PR-F 2026-07-17：送货单字段 */
      order_no: s.orderNo,
      system_delivery_date: s.systemDeliveryDate,
      note: s.note,
      // customer_id 雪花 ID 字符串（CLAUDE.md §3）
      customer_id: s.customerId!,
    }))
    // 2026-07-09 起：图纸走 multipart，与 items 按下标对齐。
    // drawingFile 为 null → 该行不上传图纸（后端按 None 处理）。
    const files: (PartBatchFilePayload | null)[] = staged.value.map((s) =>
      s.drawingFile
        ? {
            data: s.drawingFile,
            filename: s.drawingName ?? 'drawing.pdf',
            contentType: 'application/pdf',
          }
        : null,
    )
    const res = await batchCreateParts(items, files)
    if (res.failed.length > 0) {
      const sample = res.failed
        .slice(0, 5)
        .map((f) => `第 ${f.index + 1} 行：${f.message}`)
        .join('\n')
      const more = res.failed.length > 5 ? `\n...还有 ${res.failed.length - 5} 行失败` : ''
      ElMessageBox.alert(
        `服务端拒绝了 ${res.failed.length} 行：\n${sample}${more}`,
        '部分行未通过',
        { type: 'warning' },
      )
      return
    }
    // 释放所有 blob URL
    staged.value.forEach(revokeEntryUrls)
    staged.value = []
    ElMessage.success(`成功新建 ${res.created.length} 条零件`)
    // 跳到零件一览并筛选「待生产」，便于核对刚添加的零件
    router.push({ path: '/parts', query: { status: 'PENDING' } })
  } catch (e) {
    ElMessage.error((e as Error).message ?? '提交失败')
  } finally {
    submitting.value = false
  }
}

onBeforeUnmount(() => {
  staged.value.forEach(revokeEntryUrls)
})
</script>

<style lang="scss" scoped>
.batch-new {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.hint {
  color: var(--text-secondary);
  font-size: 13px;
  margin: 0;
  padding: 0 4px;
}

.staging-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
  }
}

.staging-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.staging-header-actions {
  display: flex;
  gap: 8px;
}
.staging-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.staging-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary);
}
.staging-count {
  color: var(--text-secondary);
  font-size: 13px;
}

.empty-zone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 64px 16px;
  background: #fafbfc;
  border: 1px dashed var(--border-color);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;

  &:hover {
    background: #f0f7ff;
    border-color: var(--primary-color);
  }
}
.empty-primary {
  margin: 12px 0 4px;
  font-size: 15px;
  color: var(--text-primary);
}
.empty-sub {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.staging-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border-color);
}

.amount {
  font-size: 18px;
  font-weight: 700;
  color: var(--primary-color);
  font-variant-numeric: tabular-nums;
}
.hint-inline {
  margin-left: 12px;
  color: var(--text-secondary);
  font-size: 12px;
}

.drawing-info {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  color: var(--text-regular);
  font-size: 13px;
}
.drawing-name {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.drawing-preview {
  margin-top: 8px;
}
.form-hint {
  margin: 6px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.muted {
  color: var(--text-secondary);
}

:deep(.row-urgent) {
  background-color: #fdf6ec !important;
}
</style>