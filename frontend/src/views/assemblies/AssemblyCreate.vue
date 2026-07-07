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
  - children 至少 1 行；每行 page_index >= 2（page 1 是总装图）
  - PDF 文件名后缀必须是 .pdf；其他类型后端拒收
-->
<template>
  <div class="assembly-create">
    <p class="hint">
      一次性创建一个装配件 + 它的全部子零件 + 上传总装 PDF。请先填装配件基础信息，再逐条录入子零件。
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
              <el-select
                v-model="form.applicant_id"
                filterable
                :loading="applicantLoading"
                :disabled="!form.customer_id"
                placeholder="选择或输入申请人姓名（不在表中则提交时自动新增）"
                style="width: 100%"
                clearable
                @change="onApplicantSelect"
                @blur="onApplicantInputBlur"
              >
                <el-option
                  v-for="a in applicantCandidates"
                  :key="a.id"
                  :label="a.name"
                  :value="a.id"
                />
              </el-select>
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

        <el-form-item label="总装 PDF" prop="pdfFile">
          <el-upload
            :show-file-list="false"
            :auto-upload="false"
            :on-change="onPdfChange"
            accept=".pdf"
          >
            <el-button>
              <el-icon><Upload /></el-icon>
              <span>{{ pdfName ? '更换 PDF' : '选择 PDF' }}</span>
            </el-button>
          </el-upload>
          <div v-if="pdfName" class="pdf-info">
            <el-icon><Document /></el-icon>
            <span class="pdf-name">{{ pdfName }}</span>
            <span class="pdf-size">{{ formatSize(pdfSize) }}</span>
            <el-button link type="danger" size="small" @click="onPdfRemove">
              移除
            </el-button>
          </div>
          <p class="form-hint">
            仅支持 .pdf；第一页是总装图，从第 2 页起对应子零件（每行 page_index）。
          </p>
        </el-form-item>

        <div class="section-title">
          <span>子零件清单</span>
          <span class="section-sub">共 {{ children.length }} 条</span>
          <el-button type="primary" link @click="onAddChild">
            <el-icon><Plus /></el-icon>
            <span>添加一行</span>
          </el-button>
        </div>

        <el-table :data="children" border size="small" empty-text="暂无子零件">
          <el-table-column type="index" label="#" width="50" />
          <el-table-column label="图号" min-width="160">
            <template #default="{ row, $index }">
              <el-form-item
                :prop="`children.${$index}.drawing_no`"
                :rules="childRules.drawing_no"
                :show-message="false"
                style="margin-bottom: 0"
              >
                <el-input v-model="row.drawing_no" placeholder="例如：E42FX1020107101-1" size="small" />
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
                <el-input v-model="row.name" placeholder="例如：基础板" size="small" />
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
          <el-table-column label="单价(¥)" width="110">
            <template #default="{ row }">
              <el-input-number
                v-model="row.unit_price"
                :min="0"
                :precision="2"
                :step="0.1"
                size="small"
                controls-position="right"
                style="width: 100%"
              />
            </template>
          </el-table-column>
          <el-table-column label="PDF 页码" width="110">
            <template #default="{ row, $index }">
              <el-form-item
                :prop="`children.${$index}.page_index`"
                :rules="childRules.page_index"
                :show-message="false"
                style="margin-bottom: 0"
              >
                <el-input-number
                  v-model="row.page_index"
                  :min="2"
                  :step="1"
                  size="small"
                  controls-position="right"
                  style="width: 100%"
                />
              </el-form-item>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80" align="center" fixed="right">
            <template #default="{ $index }">
              <el-button link type="danger" size="small" @click="onRemoveChild($index)">
                删除
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
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElMessage,
  type FormInstance,
  type FormItemRule,
  type FormRules,
  type UploadFile,
} from 'element-plus'
import { Check, Document, Plus, Upload } from '@element-plus/icons-vue'
import { listCustomers, type Customer } from '@/api/customer'
import { createAssembly } from '@/api/assembly'
import { createApplicant } from '@/api/applicant'
import { useApplicantSearch } from '@/composables/useApplicantSearch'
import type { AssemblyChildPayload } from '@/types/assembly'

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
} = useApplicantSearch({ resolveRootCustomerId })

async function onCustomerChange(pickedId: unknown): Promise<void> {
  const raw = Array.isArray(pickedId) ? pickedId[pickedId.length - 1] : pickedId
  const idStr = raw === null || raw === undefined ? '' : String(raw)
  form.applicant_id = null
  form.applicant_name = ''
  await loadApplicantsForCustomer(idStr || null)
}

function onApplicantSelect(value: string | null): void {
  if (value === null) {
    form.applicant_name = ''
    return
  }
  const matched = applicantCandidates.value.find((a) => a.id === value)
  form.applicant_name = matched?.name ?? ''
}

function onApplicantInputBlur(event: FocusEvent): void {
  const target = event.target as HTMLInputElement | null
  const typed = (target?.value ?? '').trim()
  if (!typed) return
  if (form.applicant_id !== null) {
    const matched = applicantCandidates.value.find((a) => a.id === form.applicant_id)
    if (matched && matched.name === typed) {
      form.applicant_name = matched.name
      return
    }
  }
  form.applicant_id = null
  form.applicant_name = typed
  // el-select 会在 blur 后清空 filter 输入 → 还原 DOM 值让用户看见
  nextTick(() => {
    const el = event.target as HTMLInputElement | null
    if (el) el.value = typed
  })
}

// ============ 表单状态 ============
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
})

const pdfFile = ref<File | null>(null)
const pdfName = ref<string>('')
const pdfSize = ref<number>(0)

const loading = ref(false)
const submitting = ref(false)

interface ChildRow extends AssemblyChildPayload {
  quantity: number
  unit_price: number
}
const children = ref<ChildRow[]>([])

function makeEmptyChild(): ChildRow {
  return {
    drawing_no: '',
    name: '',
    quantity: 1,
    unit_price: 0,
    page_index: 2,
  }
}
function onAddChild(): void {
  children.value.push(makeEmptyChild())
}
function onRemoveChild(idx: number): void {
  children.value.splice(idx, 1)
}
function onPdfChange(uploadFile: UploadFile): void {
  if (!uploadFile.raw) return
  if (!uploadFile.name.toLowerCase().endsWith('.pdf')) {
    ElMessage.error('总装文件必须是 .pdf 后缀')
    return
  }
  pdfFile.value = uploadFile.raw
  pdfName.value = uploadFile.name
  pdfSize.value = uploadFile.size ?? 0
}
function onPdfRemove(): void {
  pdfFile.value = null
  pdfName.value = ''
  pdfSize.value = 0
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
  pdfFile: [
    {
      validator: (_r, _v, cb) => {
        if (!pdfFile.value) cb(new Error('请上传总装 PDF'))
        else cb()
      },
      trigger: 'change',
    },
  ],
}

const childRules: Record<string, FormItemRule[]> = {
  drawing_no: [{ required: true, message: '请输入图号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  page_index: [
    {
      validator: (_r, value, cb) => {
        if (typeof value !== 'number' || value < 2) {
          cb(new Error('page_index 必须 ≥ 2'))
          return
        }
        cb()
      },
      trigger: 'change',
    },
  ],
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
  if (children.value.length === 0) {
    ElMessage.error('请至少添加 1 个子零件')
    return
  }
  if (!pdfFile.value) {
    ElMessage.error('请上传总装 PDF')
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
      children: children.value.map((c) => ({
        drawing_no: c.drawing_no.trim(),
        name: c.name.trim(),
        quantity: c.quantity,
        unit_price: c.unit_price,
        total_price: null as number | null,
        applicant_name: null as string | null,
        page_index: c.page_index,
      })),
    }
    const result = await createAssembly(payload, pdfFile.value)
    ElMessage.success(
      `创建成功：装配件 + ${result.children.length} 个子零件`,
    )
    router.push(`/assemblies/${result.assembly.id}`)
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