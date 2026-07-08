<!--
  AssemblyCreate.vue

  /assemblies/new — 装配体创建页。

  流程：
  1. 用户先填装配件基础信息（总图图号、名称、客户、日期）
  2. 上传总装 PDF
  3. 在「子零件」表格里填 N 行（drawing_no / 名称 / 数量 / 单价 / PDF 页码）
  4. 点提交 → POST /api/v1/assemblies multipart
  5. 成功 → 跳到 /assemblies/:id；失败 → 行内 ElMessage 错误

  关键约束（与后端 AssemblyService 对齐）：
  - customer_id 可填一级或二级客户节点（前端用 cascader 选择）
  - 可选上传总装 PDF：上传后系统按页自动生成子件草稿（每页 = 1 个子件）；
    子件数 = PDF 页数 - 1 锁定，不可手动增删（避免与 PDF 页错位）。
  - 不上传 PDF：创建空装配体，提交后到详情页用「添加子件 / 上传总装 PDF」补充。
-->
<template>
  <div class="assembly-create">
    <p class="hint">
      创建一个新装配件。可选上传总装 PDF 自动按页生成子件，也可不传直接建空装配体（到详情页再补）。
    </p>

    <el-card shadow="never" class="form-card" v-loading="loading">
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="120px"
        label-position="right"
      >
        <div class="section-title">装配件信息</div>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="总图图号" prop="drawing_no">
              <el-input
                v-model="form.drawing_no"
                placeholder="例如：E42FX1020107101"
                clearable
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="装配体名称" prop="name">
              <el-input
                v-model="form.name"
                placeholder="例如：精研挡料座"
                clearable
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="客户" prop="customer_id">
              <el-cascader
                v-model="form.customer_id"
                :options="customerTree"
                :props="{
                  value: 'id',
                  label: 'name',
                  children: 'children',
                  checkStrictly: true,
                  emitPath: false,
                }"
                placeholder="选择一级 / 二级客户"
                style="width: 100%"
                clearable
                @change="onCustomerChange"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="申请人">
              <el-autocomplete
                v-model="form.applicant_name"
                value-key="name"
                :fetch-suggestions="querySearch"
                :trigger-on-focus="true"
                :debounce="0"
                :loading="applicantLoading"
                :disabled="!form.customer_id"
                placeholder="选择或输入申请人姓名（不在表中则提交时自动新增）"
                style="width: 100%"
                clearable
                @select="onApplicantSelect"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="请购日期" prop="request_date">
              <el-date-picker
                v-model="form.request_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="请选择"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="计划交期" prop="planned_delivery_date">
              <el-date-picker
                v-model="form.planned_delivery_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="请选择"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="加急">
              <el-switch v-model="form.is_urgent" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="总装 PDF">
          <el-upload
            :show-file-list="false"
            :auto-upload="false"
            :on-change="onPdfChange"
            accept=".pdf"
          >
            <el-button>
              <el-icon><Upload /></el-icon>
              <span>{{ pdfName ? '更换 PDF' : '选择 PDF（可选）' }}</span>
            </el-button>
          </el-upload>
          <div v-if="pdfName" class="pdf-info">
            <el-icon><Document /></el-icon>
            <span class="pdf-name">{{ pdfName }}</span>
            <span class="pdf-size">{{ formatSize(pdfSize) }}</span>
            <el-button link type="primary" size="small" :disabled="!pdfBlobUrl" @click="onPreviewMaster">
              预览总装图
            </el-button>
            <el-button link type="danger" size="small" @click="onPdfRemove">
              移除
            </el-button>
          </div>
          <p class="form-hint">
            上传后系统自动按页拆分子件（第 1 页 = 总装图，第 2..N 页 = 子件 01、02…）。
            不上传则创建空装配体，到详情页补充。
          </p>
        </el-form-item>

        <div v-if="pdfBlobUrl" class="section-title">
          <span>子零件清单</span>
          <span class="section-sub">
            共 {{ pageCount - 1 }} 条 · 总装 {{ pageCount }} 页（与 PDF 一一对应）
          </span>
        </div>

        <el-table
          v-if="pdfBlobUrl"
          :data="form.children"
          border
          size="small"
          empty-text="PDF 解析中…"
        >
          <el-table-column type="index" label="#" width="50" />
          <el-table-column label="图号" min-width="140">
            <template #default="{ row, $index }">
              <el-form-item
                :prop="`children.${$index}.drawing_no`"
                :rules="childRules.drawing_no"
                :show-message="false"
                style="margin-bottom: 0"
              >
                <el-input v-model="row.drawing_no" size="small" />
              </el-form-item>
            </template>
          </el-table-column>
          <el-table-column label="名称" min-width="160">
            <template #default="{ row, $index }">
              <el-form-item
                :prop="`children.${$index}.name`"
                :rules="childRules.name"
                :show-message="false"
                style="margin-bottom: 0"
              >
                <el-input v-model="row.name" size="small" />
              </el-form-item>
            </template>
          </el-table-column>
          <el-table-column label="数量" width="90">
            <template #default="{ row }">
              <el-input-number
                v-model="row.quantity"
                :min="1"
                :step="1"
                size="small"
                controls-position="right"
                style="width: 100%"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="center" fixed="right">
            <template #default="{ row, $index }">
              <el-button link type="primary" size="small" @click="onPreviewChild(row, $index)">
                预览图纸
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-form>

      <div class="form-footer">
        <el-button @click="onCancel">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="onSubmit">
          <el-icon><Check /></el-icon>
          <span>提交创建</span>
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElMessage,
  ElMessageBox,
  type FormInstance,
  type FormItemRule,
  type FormRules,
  type UploadFile,
} from 'element-plus'
import { Check, Document, Upload } from '@element-plus/icons-vue'
import { listCustomers, type Customer } from '@/api/customer'
import { createAssembly } from '@/api/assembly'
import { createApplicant } from '@/api/applicant'
import { useApplicantSearch } from '@/composables/useApplicantSearch'
import { countPdfPages } from '@/composables/usePdfPageCount'

const router = useRouter()

// ============ 客户级联 ============
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
onMounted(async () => {
  try {
    customers.value = await listCustomers()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '客户列表加载失败')
  }
})

/** cascader 选中的客户 id（雪花 ID 字符串）→ 所属一级客户 id（同雪花 ID 字符串）。 */
function resolveRootCustomerId(pickedId: string | null): string | null {
  if (pickedId === null || pickedId === undefined || pickedId === '') return null
  const picked = customers.value.find((c) => c.id === String(pickedId))
  if (!picked) return null
  if (picked.parent_id === null) return picked.id
  return picked.parent_id
}

// ============ 申请人候选（composable：只在客户切换时拉一次） ============
const {
  applicants: applicantCandidates,
  loading: applicantLoading,
  loadForCustomer: loadApplicantsForCustomer,
  querySearch,
} = useApplicantSearch({ resolveRootCustomerId })

async function onCustomerChange(pickedId: unknown): Promise<void> {
  const raw = Array.isArray(pickedId) ? pickedId[pickedId.length - 1] : pickedId
  const idStr = raw === null || raw === undefined ? '' : String(raw)
  form.applicant_id = null
  form.applicant_name = ''
  await loadApplicantsForCustomer(idStr || null)
}

function onApplicantSelect(item: Record<string, unknown>): void {
  form.applicant_id = String(item.id)
  // form.applicant_name 由 v-model 自动同步为 item.name，无需手动设
}

// ============ 表单状态 ============
interface ChildRow {
  drawing_no: string
  name: string
  quantity: number
}

interface FormState {
  drawing_no: string
  name: string
  applicant_name: string
  // applicant_id 用字符串承载雪花 ID（避免 JS Number 精度丢失）；
  // 见 CLAUDE.md「雪花 ID 溢出」一节。
  applicant_id: string | null
  // customer_id 雪花 ID 字符串（CLAUDE.md §3）
  customer_id: string | null
  request_date: string
  planned_delivery_date: string
  is_urgent: boolean
  /**
   * 子零件清单。挂在 form 上是因为 el-form-item 的 :prop="children.0.drawing_no"
   * 路径要相对 form 才能被 formRef.validate() 校验到。
   * 仅在用户上传总装 PDF 后才填充；不传 PDF 时为空数组 → 后端创建空装配体。
   */
  children: ChildRow[]
}

function todayIso(): string {
  const d = new Date()
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

const formRef = ref<FormInstance>()
const form = reactive<FormState>({
  drawing_no: '',
  name: '',
  applicant_name: '',
  applicant_id: null,
  customer_id: null,
  request_date: todayIso(),
  planned_delivery_date: '',
  is_urgent: false,
  children: [],
})

/**
 * 保持 applicant_id 与 applicant_name 一致：
 * - 用户从下拉挑了某人：applicant_name = item.name，applicant_id = item.id（@select 设）
 * - 用户清空 / 继续打字改了名字：当前 applicant_id 已不再指向同名 → 清掉
 *   → 让 onSubmit 走「自动新增」分支（AssemblyCreate.vue:onSubmit 内 createApplicant 段）。
 *
 * 注意：本 watcher 必须在 const form 声明之后注册 —— watch 的 getter 在 setup
 * 阶段就会同步执行一次以注册 reactive 依赖，提前引用 form 会触发 TDZ。
 */
watch(
  () => form.applicant_name,
  (next) => {
    const currentId = form.applicant_id
    if (currentId === null) return
    const matched = applicantCandidates.value.find((a) => a.id === currentId)
    if (matched && matched.name === next) return
    form.applicant_id = null
  },
)

const loading = ref(false)
const submitting = ref(false)

// ============ PDF 上传（可选） ============
const pdfFile = ref<File | null>(null)
const pdfName = ref<string>('')
const pdfSize = ref<number>(0)
/** 本地 PDF 的 blob URL，用于「预览总装图 / 预览图纸」按钮。 */
const pdfBlobUrl = ref<string | null>(null)
/** PDF 总页数（page 1 = 总图，page 2..N = 子件 1..N-1）。 */
const pageCount = ref(0)

function makeAutoChild(seq: number): ChildRow {
  return {
    drawing_no: String(seq).padStart(2, '0'),  // "01", "02", ...
    name: `子零件${String(seq).padStart(2, '0')}`,
    quantity: 1,
  }
}

function rebuildChildren(total: number): void {
  const n = Math.max(0, total - 1)
  form.children = Array.from({ length: n }, (_, i) => makeAutoChild(i + 1))
}

async function onPdfChange(uploadFile: UploadFile): Promise<void> {
  if (!uploadFile.raw) return
  if (!uploadFile.name.toLowerCase().endsWith('.pdf')) {
    ElMessage.error('总装文件必须是 .pdf 后缀')
    return
  }
  // 若已编辑过子零件草稿，弹 confirm 防误操作
  const isDirty = form.children.some(
    (c) => !c.name.startsWith('子零件') || c.quantity !== 1,
  )
  if (isDirty) {
    try {
      await ElMessageBox.confirm(
        '更换 PDF 将清空当前子零件草稿，是否继续？',
        '更换 PDF',
        { type: 'warning' },
      )
    } catch {
      return   // 用户取消
    }
  }
  // 释放旧 blob URL
  if (pdfBlobUrl.value) URL.revokeObjectURL(pdfBlobUrl.value)
  pdfBlobUrl.value = URL.createObjectURL(uploadFile.raw)
  pdfFile.value = uploadFile.raw
  pdfName.value = uploadFile.name
  pdfSize.value = uploadFile.size ?? 0
  await recountPages()
}

async function recountPages(): Promise<void> {
  if (!pdfFile.value) {
    pageCount.value = 0
    form.children = []
    return
  }
  try {
    pageCount.value = await countPdfPages(pdfFile.value)
    rebuildChildren(pageCount.value)
  } catch (e) {
    ElMessage.error('PDF 解析失败：' + ((e as Error).message ?? String(e)))
    pageCount.value = 0
    form.children = []
  }
}

function onPdfRemove(): void {
  if (pdfBlobUrl.value) URL.revokeObjectURL(pdfBlobUrl.value)
  pdfBlobUrl.value = null
  pdfFile.value = null
  pdfName.value = ''
  pdfSize.value = 0
  pageCount.value = 0
  form.children = []
}

onBeforeUnmount(() => {
  if (pdfBlobUrl.value) URL.revokeObjectURL(pdfBlobUrl.value)
})

function onPreviewChild(_row: unknown, idx: number): void {
  if (!pdfBlobUrl.value) return
  // 该子件在原 PDF 中的页码 = idx + 2（page 1 是总装图）
  const page = idx + 2
  window.open(`${pdfBlobUrl.value}#page=${page}&toolbar=0`, '_blank', 'noopener')
}

function onPreviewMaster(): void {
  if (!pdfBlobUrl.value) return
  window.open(`${pdfBlobUrl.value}#page=1&toolbar=0`, '_blank', 'noopener')
}

function formatSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(2)} MB`
}

// ============ 校验规则 ============
const rules: FormRules = {
  drawing_no: [{ required: true, message: '请输入总图图号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入装配体名称', trigger: 'blur' }],
  customer_id: [
    {
      required: true,
      validator: (_r, value, cb) => {
        // cascader emitPath:false 返回选中节点的 id，来自 Customer.id（string）
        if (value === null || value === undefined || value === '') {
          cb(new Error('请选择客户'))
          return
        }
        const c = customers.value.find((x) => String(x.id) === String(value))
        if (!c) {
          cb(new Error('客户不存在'))
          return
        }
        cb()
      },
      trigger: 'change',
    },
  ],
  request_date: [{ required: true, message: '请选择请购日期', trigger: 'change' }],
  planned_delivery_date: [{ required: true, message: '请选择计划交期', trigger: 'change' }],
}

const childRules: Record<string, FormItemRule[]> = {
  drawing_no: [{ required: true, message: '请输入图号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
}

function onCancel(): void {
  router.push('/assemblies')
}

async function onSubmit(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    ElMessage.error('表单校验未通过，请检查输入')
    return
  }

  submitting.value = true
  loading.value = true
  try {
    // 若申请人未选现有但有姓名，按一级客户自动新增。
    let resolvedApplicantId: string | null = form.applicant_id
    if (resolvedApplicantId === null && form.applicant_name.trim() && form.customer_id !== null) {
      const rootId = resolveRootCustomerId(form.customer_id)
      if (rootId !== null) {
        const created = await createApplicant({
          name: form.applicant_name.trim(),
          customer_id: String(rootId),
        })
        resolvedApplicantId = created.id
      }
    }
    const payload = {
      drawing_no: form.drawing_no.trim(),
      name: form.name.trim(),
      applicant_name: form.applicant_name.trim() || null,
      applicant_id: resolvedApplicantId,
      // customer_id 雪花 ID 字符串（CLAUDE.md §3）—— 直接传字符串
      customer_id: form.customer_id!,
      request_date: form.request_date,
      planned_delivery_date: form.planned_delivery_date,
      is_urgent: form.is_urgent,
      children: form.children.map((c) => ({
        drawing_no: c.drawing_no.trim(),
        name: c.name.trim(),
        quantity: c.quantity,
        applicant_name: null as string | null,
      })),
    }
    // 后端接口签名：create_assembly(payload, pdf_bytes?) — 走单文件 multipart。
    // 本页 PDF 是可选的，所以两个分支都要支持。
    const { createAssemblyWithFile } = await import('@/api/assembly')
    const result = pdfFile.value
      ? await createAssemblyWithFile(payload, pdfFile.value)
      : await createAssembly(payload)
    if (pdfFile.value) {
      ElMessage.success(
        `创建成功：装配件 + ${result.children.length} 个子件`,
      )
      // 释放本地 blob URL
      if (pdfBlobUrl.value) URL.revokeObjectURL(pdfBlobUrl.value)
      pdfBlobUrl.value = null
      router.push({ path: '/assemblies', query: { status: 'PENDING' } })
    } else {
      ElMessage.success('创建成功：请到详情页上传总装 PDF 或添加子件')
      router.push(`/assemblies/${result.assembly.id}`)
    }
  } catch (e) {
    ElMessage.error((e as Error).message ?? '创建失败')
  } finally {
    submitting.value = false
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.assembly-create {
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
.form-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
  }
}
.section-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 16px 0 12px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  border-left: 3px solid var(--primary-color);
  padding-left: 10px;
}
.section-sub {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 400;
}
.pdf-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  font-size: 13px;
  color: var(--text-regular);
}
.pdf-name {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.pdf-size {
  color: var(--text-secondary);
  font-size: 12px;
}
.form-hint {
  margin: 6px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
}
.form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border-color);
}
:deep(.el-form-item) {
  margin-bottom: 16px;
}
</style>