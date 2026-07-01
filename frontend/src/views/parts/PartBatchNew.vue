<!--
  PartBatchNew.vue

  /parts/new  批量新建零件页（与 /parts 并列）。

  流程：
  1. 点空白区 / 「+ 添加零件」 → 弹出 Dialog 填写一条零件（含图纸上传）
  2. Dialog 确定 → 校验通过后入队到「待新增零件」表
  3. 点表格中任意行 → 弹出只读预览 Dialog（含图纸预览）
  4. 全部填好 → 点底部「提交 N 条」 → POST /api/v1/parts/batch
  5. 成功 → 清空列表 + 跳回 /parts；失败 → 弹窗列出失败行

  注意：图纸目前只在浏览器侧（blob URL 预览），后端 batch 接口暂不接收文件；
  后续接好 t_part.drawing_url + 文件存储后，把 dialog 里的文件加入 batch payload 即可。
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
        <el-button type="primary" @click="openAddDialog">
          <el-icon><Plus /></el-icon>
          <span>添加零件</span>
        </el-button>
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
        <el-table-column label="图纸" width="80" align="center">
          <template #default="{ row }">
            <el-image
              v-if="row.drawingUrl"
              :src="row.drawingUrl"
              fit="cover"
              :preview-src-list="[row.drawingUrl]"
              :preview-teleported="true"
              style="width: 40px; height: 40px; border-radius: 4px; cursor: pointer"
            />
            <el-icon v-else :size="24" color="#c0c4cc"><Picture /></el-icon>
          </template>
        </el-table-column>
        <el-table-column prop="drawingNo" label="图号" width="130" />
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="quantity" label="数量" width="70" align="right" />
        <el-table-column prop="unitPrice" label="单价" width="90" align="right">
          <template #default="{ row }">{{ row.unitPrice.toFixed(2) }}</template>
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
            <el-form-item label="申请人" prop="applicantName">
              <el-input v-model="form.applicantName" placeholder="例如：林雪强" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="客户" prop="customerId">
              <el-cascader
                v-model="form.customerId"
                :options="customerTree"
                :props="{ value: 'id', label: 'name', children: 'children', checkStrictly: true, emitPath: false }"
                placeholder="选择一级 / 二级客户"
                style="width: 100%"
                clearable
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="数量" prop="quantity">
              <el-input-number v-model="form.quantity" :min="1" :step="1" controls-position="right" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="单价(¥)" prop="unitPrice">
              <el-input-number
                v-model="form.unitPrice"
                :min="0"
                :precision="2"
                :step="0.1"
                controls-position="right"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="加急">
              <el-switch v-model="form.isUrgent" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="总价(¥)">
          <span class="amount">¥ {{ totalPrice.toFixed(2) }}</span>
          <span class="hint-inline">= 数量 × 单价（自动计算）</span>
        </el-form-item>

        <el-row :gutter="16">
          <el-col :span="8">
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
          <el-col :span="8">
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
          <el-col :span="8">
            <el-form-item label="实际送货">
              <el-date-picker
                v-model="form.actualDeliveryDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="可空"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="图纸">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            :on-change="onDrawingChange"
            :on-remove="onDrawingRemoveUpload"
            accept="image/*,.pdf,.dwg,.dxf"
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
          <div v-if="form.drawingUrl && isImageFile(form.drawingName)" class="drawing-preview">
            <el-image
              :src="form.drawingUrl"
              :preview-src-list="[form.drawingUrl]"
              :preview-teleported="true"
              fit="contain"
              style="max-width: 240px; max-height: 180px; border: 1px solid var(--border-color); border-radius: 4px"
            />
          </div>
          <p class="form-hint">支持图片 / PDF / DWG；当前仅做浏览器侧预览，后端 batch 接口未传文件。</p>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="addDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="dialogSubmitting" @click="onAddConfirm">
          {{ editingUid ? '保存到列表' : '加入列表' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 预览 Dialog（只读） -->
    <el-dialog v-model="previewDialogVisible" title="预览零件" width="720px">
      <el-descriptions v-if="previewing" :column="2" border>
        <el-descriptions-item label="图号">{{ previewing.drawingNo }}</el-descriptions-item>
        <el-descriptions-item label="名称">{{ previewing.name }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ previewing.applicantName }}</el-descriptions-item>
        <el-descriptions-item label="客户">{{ previewing.customerLabel || '—' }}</el-descriptions-item>
        <el-descriptions-item label="数量">{{ previewing.quantity }}</el-descriptions-item>
        <el-descriptions-item label="加急">
          <el-tag v-if="previewing.isUrgent" type="danger" size="small" effect="dark">加急</el-tag>
          <span v-else class="muted">否</span>
        </el-descriptions-item>
        <el-descriptions-item label="单价">¥ {{ previewing.unitPrice.toFixed(2) }}</el-descriptions-item>
        <el-descriptions-item label="总价">¥ {{ previewing.totalPrice.toFixed(2) }}</el-descriptions-item>
        <el-descriptions-item label="请购日期">{{ previewing.requestDate }}</el-descriptions-item>
        <el-descriptions-item label="计划交期">{{ previewing.plannedDeliveryDate }}</el-descriptions-item>
        <el-descriptions-item label="实际送货" :span="2">
          <span v-if="previewing.actualDeliveryDate">{{ previewing.actualDeliveryDate }}</span>
          <span v-else class="muted">—</span>
        </el-descriptions-item>
        <el-descriptions-item label="图纸" :span="2">
          <el-image
            v-if="previewing.drawingUrl && isImageFile(previewing.drawingName)"
            :src="previewing.drawingUrl"
            :preview-src-list="[previewing.drawingUrl]"
            :preview-teleported="true"
            fit="contain"
            style="max-width: 100%; max-height: 360px"
          />
          <div v-else-if="previewing.drawingName" class="drawing-info">
            <el-icon><Picture /></el-icon>
            <span class="drawing-name">{{ previewing.drawingName }}</span>
          </div>
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
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElMessage,
  ElMessageBox,
  type FormInstance,
  type FormRules,
  type UploadFile,
} from 'element-plus'
import { DocumentAdd, Picture, Plus, Upload } from '@element-plus/icons-vue'
import { batchCreateParts, type PartCreatePayload } from '@/api/parts'
import { listCustomers, type Customer } from '@/api/customer'

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

// ============ 待新增列表 ============
interface StagedEntry {
  uid: string
  drawingNo: string
  name: string
  applicantName: string
  customerId: number | null
  customerLabel: string
  quantity: number
  unitPrice: number
  isUrgent: boolean
  requestDate: string
  plannedDeliveryDate: string
  actualDeliveryDate: string
  drawingFile: File | null
  drawingName: string | null
  drawingUrl: string | null
  totalPrice: number
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

function isImageFile(name: string | null | undefined): boolean {
  if (!name) return false
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name)
}

// ============ Dialog 表单 ============
interface FormState {
  drawingNo: string
  name: string
  applicantName: string
  customerId: number | null
  quantity: number
  unitPrice: number
  isUrgent: boolean
  requestDate: string
  plannedDeliveryDate: string
  actualDeliveryDate: string
  drawingFile: File | null
  drawingName: string | null
  drawingUrl: string | null
}

const formRef = ref<FormInstance>()
const addDialogVisible = ref(false)
const dialogSubmitting = ref(false)
const editingUid = ref<string | null>(null)

const initialForm = (): FormState => ({
  drawingNo: '',
  name: '',
  applicantName: '',
  customerId: null,
  quantity: 1,
  unitPrice: 0,
  isUrgent: false,
  requestDate: '',
  plannedDeliveryDate: '',
  actualDeliveryDate: '',
  drawingFile: null,
  drawingName: null,
  drawingUrl: null,
})

const form = reactive<FormState>(initialForm())

const totalPrice = computed<number>(() => {
  const q = Number(form.quantity) || 0
  const p = Number(form.unitPrice) || 0
  return q * p
})

const rules: FormRules = {
  drawingNo: [{ required: true, message: '请输入图号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  applicantName: [{ required: true, message: '请输入申请人', trigger: 'blur' }],
  customerId: [
    {
      required: true,
      validator: (_rule, value, callback) => {
        if (typeof value !== 'number' || !Number.isFinite(value)) {
          callback(new Error('请选择客户（必须是二级叶子节点）'))
          return
        }
        // cascader 选项都是二级叶子节点（构建 customerTree 时只挂 parent_id 非空的）
        const c = customers.value.find((x) => String(x.id) === String(value))
        if (!c || c.parent_id === null) {
          callback(new Error('请选择二级客户节点'))
          return
        }
        callback()
      },
      trigger: 'change',
    },
  ],
  quantity: [{ required: true, message: '请输入数量', trigger: 'blur' }],
  unitPrice: [{ required: true, message: '请输入单价', trigger: 'blur' }],
  requestDate: [{ required: true, message: '请选择请购日期', trigger: 'change' }],
  plannedDeliveryDate: [{ required: true, message: '请选择计划交期', trigger: 'change' }],
}

function openAddDialog(): void {
  editingUid.value = null
  Object.assign(form, initialForm())
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

function findCustomerLabel(id: number | null): string {
  if (id === null) return ''
  const c = customers.value.find((x) => Number(x.id) === id)
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
  // cascader value → customerId（emitPath:false 时直接是 number/string）
  const customerId = form.customerId
  if (typeof customerId !== 'number') {
    ElMessage.error('请选择客户')
    return
  }
  dialogSubmitting.value = true
  try {
    const entry: StagedEntry = {
      uid: editingUid.value ?? makeUid(),
      drawingNo: form.drawingNo.trim(),
      name: form.name.trim(),
      applicantName: form.applicantName.trim(),
      customerId,
      customerLabel: findCustomerLabel(customerId),
      quantity: form.quantity,
      unitPrice: form.unitPrice,
      isUrgent: form.isUrgent,
      requestDate: form.requestDate,
      plannedDeliveryDate: form.plannedDeliveryDate,
      actualDeliveryDate: form.actualDeliveryDate,
      drawingFile: form.drawingFile,
      drawingName: form.drawingName,
      drawingUrl: form.drawingUrl,
      totalPrice: totalPrice.value,
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
    customerId: target.customerId,
    quantity: target.quantity,
    unitPrice: target.unitPrice,
    isUrgent: target.isUrgent,
    requestDate: target.requestDate,
    plannedDeliveryDate: target.plannedDeliveryDate,
    actualDeliveryDate: target.actualDeliveryDate,
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
    const items: PartCreatePayload[] = staged.value.map((s) => ({
      name: s.name,
      drawing_no: s.drawingNo,
      applicant_name: s.applicantName,
      quantity: s.quantity,
      unit_price: s.unitPrice,
      total_price: s.totalPrice,
      request_date: s.requestDate,
      planned_delivery_date: s.plannedDeliveryDate,
      actual_delivery_date: s.actualDeliveryDate || null,
      is_urgent: s.isUrgent,
      customer_id: s.customerId!,
    }))
    const res = await batchCreateParts(items)
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
    router.push('/parts')
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