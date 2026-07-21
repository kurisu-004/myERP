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

  2026-07-21 起：合并为统一入口，新增「PDF 批量上传」Tab。
    - Tab 1「录入」：原手工逐条录入 + 应标 Excel 导入（从 PartBidImport.vue 迁入）
    - Tab 2「PDF 批量上传」：批量拖 PDF + 可选 Excel，按文件名解析图号/名称，
      多页 PDF 自动建装配件 + 子件，单页 PDF 独立零件；
      用户可在树预览里点选 master 页。
-->

<template>
  <div class="batch-new">
    <el-tabs v-model="activeTab" class="batch-tabs">
      <el-tab-pane label="录入" name="manual">
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
      <ResponsiveList
        v-else
        :items="staged"
        row-key="uid"
        empty-text="暂无待新增零件"
        :card-class="(row) => (row.isUrgent ? 'rl-card--urgent' : '')"
        border
        stripe
        size="small"
        :row-class-name="rowClassName"
        @row-click="onRowPreview"
        @card-click="onRowPreview"
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

        <!-- 手机卡片 -->
        <template #card="{ row }">
          <div class="rl-card-head">
            <span class="rl-card-title">{{ (row as StagedEntry).name }}</span>
            <el-tag v-if="(row as StagedEntry).isUrgent" type="danger" size="small" effect="dark">加急</el-tag>
          </div>
          <div class="rl-card-sub">
            图号 {{ (row as StagedEntry).drawingNo || '—' }}
          </div>
          <div class="rl-kv">
            <div class="rl-kv__item">
              <span class="rl-kv__key">数量</span>
              <span class="rl-kv__val">{{ (row as StagedEntry).quantity }}</span>
            </div>
            <div class="rl-kv__item">
              <span class="rl-kv__key">计划交期</span>
              <span class="rl-kv__val">{{ (row as StagedEntry).plannedDeliveryDate || '—' }}</span>
            </div>
            <div class="rl-kv__item">
              <span class="rl-kv__key">申请人</span>
              <span class="rl-kv__val">{{ (row as StagedEntry).applicantName || '—' }}</span>
            </div>
            <div class="rl-kv__item rl-kv__item--full">
              <span class="rl-kv__key">客户</span>
              <span class="rl-kv__val">{{ (row as StagedEntry).customerLabel || '—' }}</span>
            </div>
          </div>
          <div class="rl-card-actions">
            <el-button link type="primary" size="small" @click.stop="openDrawingPreview(row as StagedEntry)">图纸预览</el-button>
            <el-button link type="primary" size="small" @click.stop="onRowPreview(row as StagedEntry)">查看</el-button>
            <el-button link type="danger" size="small" @click.stop="onRemoveRow((row as StagedEntry).uid)">删除</el-button>
          </div>
        </template>
      </ResponsiveList>

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
      :width="addDlg.width.value"
      :top="addDlg.top.value"
      :fullscreen="addDlg.fullscreen.value"
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
        <div class="form-grid">
          <div>
            <el-form-item label="图号" prop="drawingNo">
              <el-input v-model="form.drawingNo" placeholder="例如：LT39822" />
            </el-form-item>
          </div>
          <div>
            <el-form-item label="名称" prop="name">
              <el-input v-model="form.name" placeholder="请输入品名 / 零件名称" />
            </el-form-item>
          </div>
        </div>

        <div class="form-grid">
          <div>
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
          </div>
          <div>
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
          </div>
        </div>

        <div class="form-grid">
          <div>
            <el-form-item label="数量" prop="quantity">
              <el-input-number v-model="form.quantity" :min="1" :step="1" controls-position="right" style="width: 100%" />
            </el-form-item>
          </div>
          <div>
            <el-form-item label="加急">
              <el-switch v-model="form.isUrgent" />
            </el-form-item>
          </div>
        </div>

        <div class="form-grid">
          <div>
            <el-form-item label="请购日期" prop="requestDate">
              <el-date-picker
                v-model="form.requestDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="请选择"
                style="width: 100%"
              />
            </el-form-item>
          </div>
          <div>
            <el-form-item label="计划交期" prop="plannedDeliveryDate">
              <el-date-picker
                v-model="form.plannedDeliveryDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="请选择"
                style="width: 100%"
              />
            </el-form-item>
          </div>
        </div>

        <!-- 送货单字段（PR-F 2026-07-17） -->
        <div class="form-grid">
          <div>
            <el-form-item label="订单号">
              <el-input v-model="form.orderNo" placeholder="如 6200037950（可选）" />
            </el-form-item>
          </div>
          <div>
            <el-form-item label="系统交期">
              <el-date-picker
                v-model="form.systemDeliveryDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="订单方系统内部交期（可选）"
                style="width: 100%"
              />
            </el-form-item>
          </div>
        </div>

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

<!-- 预览 Dialog（只读，手机全屏 / 桌面 720px，列数随断点切换） -->
    <el-dialog
      v-model="previewDialogVisible"
      title="预览零件"
      :width="previewDlg.width.value"
      :top="previewDlg.top.value"
      :fullscreen="previewDlg.fullscreen.value"
    >
      <el-descriptions v-if="previewing" :column="previewDescCol" border>
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
      </el-tab-pane>

      <!-- ============================================================== -->
      <!-- Tab 2: PDF 批量上传（2026-07-21 新增） -->
      <!-- ============================================================== -->
      <el-tab-pane label="PDF 批量上传" name="pdf">
        <p class="hint">
          拖拽多个 PDF 文件（命名格式 <code>图号_零件名称.pdf</code>），
          可同时拖入应标 Excel 文件（按图号匹配申请人/数量/加急等）。
          单页 PDF = 独立零件；多页 PDF = 装配件 + 子件。
          树形预览里可点选「总装图」单选。
        </p>

        <el-card shadow="never" class="pdf-form-card">
          <el-form :model="pdfForm" inline>
            <el-form-item label="L1 客户" required>
              <el-cascader
                v-model="pdfForm.customerId"
                :options="customerTree"
                :props="{ value: 'id', label: 'name', children: 'children', emitPath: false, checkStrictly: true }"
                placeholder="选到二级叶子客户"
                style="width: 200px"
                clearable
              />
            </el-form-item>
            <el-form-item label="申请人">
              <el-autocomplete
                v-model="pdfForm.applicantName"
                :fetch-suggestions="(q, cb) => queryApplicant(q, cb)"
                placeholder="申请人姓名（可选）"
                clearable
                style="width: 180px"
              />
            </el-form-item>
            <el-form-item label="请购日期">
              <el-date-picker
                v-model="pdfForm.requestDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="默认今天"
                style="width: 160px"
              />
            </el-form-item>
            <el-form-item label="计划交期">
              <el-date-picker
                v-model="pdfForm.plannedDeliveryDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="默认请购 +7 天"
                style="width: 160px"
              />
            </el-form-item>
            <el-form-item label="加急">
              <el-switch v-model="pdfForm.isUrgent" />
            </el-form-item>
          </el-form>

          <el-row :gutter="16">
            <el-col :span="12">
              <el-upload
                multiple
                accept=".pdf"
                :auto-upload="false"
                :file-list="pdfFiles"
                :on-change="onPdfChange"
                :on-remove="onPdfRemove"
                drag
              >
                <el-icon class="el-icon--upload"><upload-filled /></el-icon>
                <div class="el-upload__text">拖拽或点击上传 PDF（可多个）</div>
                <template #tip>
                  <div class="el-upload__tip">单页 PDF = 独立零件；多页 PDF = 装配件</div>
                </template>
              </el-upload>
            </el-col>
            <el-col :span="12">
              <el-upload
                accept=".xlsx,.xls"
                :auto-upload="false"
                :file-list="excelFiles"
                :on-change="onExcelChange"
                :on-remove="onExcelRemove"
                drag
                :show-file-list="true"
              >
                <el-icon class="el-icon--upload"><document /></el-icon>
                <div class="el-upload__text">拖拽应标 Excel（可选；按图号匹配）</div>
                <template #tip>
                  <div class="el-upload__tip">列：物料编号 / 货物(劳务)名称 / 申请人 / 数量 / 单价 / 紧急状态 / 预估交期天数</div>
                </template>
              </el-upload>
            </el-col>
          </el-row>

          <div class="pdf-actions">
            <el-button
              type="primary"
              :disabled="pdfFiles.length === 0 || pdfBuildingTree"
              @click="onBuildTree"
            >
              <el-icon><magic-stick /></el-icon>
              <span>{{ pdfTree.length > 0 ? '重新解析预览' : '解析并预览' }}</span>
            </el-button>
            <el-button
              type="success"
              :disabled="pdfTree.length === 0 || pdfSubmitting"
              @click="onSubmitPdfTree"
            >
              <el-icon><check /></el-icon>
              <span>提交创建</span>
            </el-button>
            <span class="tree-stat">
              共 {{ pdfStandaloneCount }} 个独立零件 + {{ pdfAssemblyCount }} 个装配件（{{ pdfChildCount }} 子件）
            </span>
          </div>
        </el-card>

        <el-card v-if="pdfTree.length > 0" shadow="never" class="pdf-tree-card">
          <el-table
            :data="pdfTree"
            row-key="uid"
            :tree-props="{ children: 'children' }"
            default-expand-all
            border
            class="pdf-tree-table"
          >
            <el-table-column label="类型 / 页码" width="140">
              <template #default="{ row }">
                <el-tag v-if="row.kind === 'assembly'" type="warning" size="small">装配件</el-tag>
                <el-tag v-else-if="row.kind === 'standalone'" type="info" size="small">独立零件</el-tag>
                <el-tag v-else size="small">第 {{ row.page_index + 1 }} 页</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="PDF 文件名" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="filename">{{ row.pdf_filename }}</span>
              </template>
            </el-table-column>
            <el-table-column label="图号" min-width="140">
              <template #default="{ row }">
                <el-input
                  v-model="row.drawing_no"
                  size="small"
                  :class="{ 'is-error': !row.drawing_no }"
                  placeholder="必填"
                />
              </template>
            </el-table-column>
            <el-table-column label="名称" min-width="160">
              <template #default="{ row }">
                <el-input v-model="row.name" size="small" placeholder="选填" />
              </template>
            </el-table-column>
            <el-table-column v-if="hasNonStandalone" label="数量" width="90">
              <template #default="{ row }">
                <el-input-number
                  v-if="row.kind !== 'assembly'"
                  v-model="row.quantity"
                  :min="1"
                  size="small"
                  controls-position="right"
                />
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="申请人" min-width="120">
              <template #default="{ row }">
                <el-input v-model="row.applicant_name" size="small" />
              </template>
            </el-table-column>
            <el-table-column label="加急" width="80" align="center">
              <template #default="{ row }">
                <el-switch v-model="row.is_urgent" />
              </template>
            </el-table-column>
            <el-table-column label="订单编号" width="130">
              <template #default="{ row }">
                <el-input v-model="row.order_no" size="small" placeholder="客户系统编号" />
              </template>
            </el-table-column>
            <el-table-column label="系统交期" width="150">
              <template #default="{ row }">
                <el-date-picker
                  v-model="row.system_delivery_date"
                  type="date"
                  value-format="YYYY-MM-DD"
                  size="small"
                  clearable
                  placeholder="选填"
                />
              </template>
            </el-table-column>
            <el-table-column label="备注" min-width="160">
              <template #default="{ row }">
                <el-input v-model="row.note" size="small" type="textarea" :rows="1" />
              </template>
            </el-table-column>
            <el-table-column label="设为总装图" width="120" align="center">
              <template #default="{ row }">
                <el-radio
                  v-if="row.kind === 'assembly_child'"
                  v-model="row.assembly_uid_master_choice"
                  :value="row.assembly_uid + ':' + row.page_index"
                />
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>
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
import ResponsiveList from '@/components/ResponsiveList.vue'
import { batchCreateParts, type PartBatchFilePayload, type PartCreatePayload } from '@/api/parts'
import { listCustomers, type Customer } from '@/api/customer'
import { createApplicant } from '@/api/applicant'
import { useApplicantSearch } from '@/composables/useApplicantSearch'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'

const router = useRouter()

// ============ 响应式 ============
const { isMobile } = useBreakpoint()
const previewDescCol = computed(() => (isMobile.value ? 1 : 2))

// 各 dialog 独立的响应式宽度（保留桌面固定 px）
const addDlg = useDialogSize({ desktopWidth: 900, fullscreenOnMobile: true })
const previewDlg = useDialogSize({ desktopWidth: 720, fullscreenOnMobile: true })

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

// ============================================================================
// Tab 2: PDF 批量上传（2026-07-21 新增）
// ============================================================================
import {
  batchCreatePartsWithPdfs,
  type PartBatchTreeItemFE,
  type PartBatchTreeAssemblyFE,
} from '@/api/parts'
import { bulkGetOrCreateApplicants } from '@/api/applicant'
import { parseBidExcel, type BidRow } from '@/utils/bidExcelParser'
import { parseDrawingFilename } from '@/utils/drawingFilename'

const route = useRoute()
const activeTab = ref<string>(typeof route.query.tab === 'string' ? route.query.tab : 'manual')
watch(activeTab, (v) => {
  router.replace({ query: { ...route.query, tab: v } })
})

interface PdfFormState {
  customerId: string | null  // 叶子客户 id
  applicantName: string
  applicantId: string | null
  requestDate: string
  plannedDeliveryDate: string
  isUrgent: boolean
}

const pdfForm = reactive<PdfFormState>({
  customerId: null,
  applicantName: '',
  applicantId: null,
  requestDate: todayIso(),
  plannedDeliveryDate: '',
  isUrgent: false,
})

// PDF / Excel 文件列表（el-upload 控件绑定）
const pdfFiles = ref<UploadFile[]>([])
const excelFiles = ref<UploadFile[]>([])
const pdfBuildingTree = ref(false)
const pdfSubmitting = ref(false)

// 树预览数据结构
type TreeAssembly = {
  uid: string
  kind: 'assembly'
  pdf_index: number
  pdf_filename: string
  total_pages: number
  drawing_no: string
  name: string
  applicant_name: string
  customer_id: string
  request_date: string
  planned_delivery_date: string
  system_delivery_date: string | null
  order_no: string | null
  note: string | null
  is_urgent: boolean
  children: TreeChild[]
}
type TreeChild = {
  uid: string
  kind: 'assembly_child' | 'standalone'
  pdf_index: number
  page_index: number
  pdf_filename: string
  drawing_no: string
  name: string
  applicant_name: string
  quantity: number
  customer_id: string
  request_date: string
  planned_delivery_date: string
  system_delivery_date: string | null
  order_no: string | null
  note: string | null
  is_urgent: boolean
  assembly_uid: string | null
  assembly_uid_master_choice: string | null
}
type TreeRow = TreeAssembly | TreeChild

const pdfTree = ref<TreeRow[]>([])

const hasNonStandalone = computed(() =>
  pdfTree.value.some((r) => r.kind === 'assembly'),
)
const pdfStandaloneCount = computed(() =>
  pdfTree.value.filter((r) => r.kind === 'standalone').length,
)
const pdfAssemblyCount = computed(() =>
  pdfTree.value.filter((r) => r.kind === 'assembly').length,
)
const pdfChildCount = computed(() =>
  pdfTree.value
    .filter((r): r is TreeAssembly => r.kind === 'assembly')
    .reduce((sum, a) => sum + a.children.length, 0),
)

// el-upload 钩子
function onPdfChange(file: UploadFile): void {
  // 多文件上传会触发多次 on-change；用 fileList 状态自动管理
  pdfFiles.value = fileList(pdfFiles.value, file, '.pdf')
}
function onPdfRemove(file: UploadFile): void {
  pdfFiles.value = pdfFiles.value.filter((f) => f.uid !== file.uid)
}
function onExcelChange(file: UploadFile): void {
  excelFiles.value = fileList(excelFiles.value, file, '.xlsx,.xls', /*matchExt*/ true)
}
function onExcelRemove(file: UploadFile): void {
  excelFiles.value = excelFiles.value.filter((f) => f.uid !== file.uid)
}

/** 把新 file push 到 list（去重 by uid），扩展名校称校验。 */
function fileList(
  current: UploadFile[],
  file: UploadFile,
  accept: string,
  matchExt = false,
): UploadFile[] {
  if (current.some((f) => f.uid === file.uid)) return current
  const name = (file.name || '').toLowerCase()
  const exts = accept.replace(/\./g, '').split(',')
  if (matchExt) {
    if (!exts.some((e) => name.endsWith('.' + e))) {
      ElMessage.warning(`不支持的文件类型：${file.name}`)
      return current
    }
  }
  return [...current, file]
}

/** 申请人的 el-autocomplete 客户端过滤候选（按 root_customer_id 拉一次，内存过滤） */
function queryApplicant(queryString: string, callback: (items: unknown[]) => void): void {
  const q = (queryString || '').trim().toLowerCase()
  const list = (applicantCandidates.value || []).map((a) => ({
    id: a.id,
    name: a.name,
    value: a.name,
  }))
  const matched = q ? list.filter((a) => a.name.toLowerCase().includes(q)) : list.slice(0, 30)
  callback(matched)
}

watch(() => pdfForm.customerId, async (v) => {
  pdfForm.applicantName = ''
  pdfForm.applicantId = null
  await loadApplicantsForCustomer(v || null)
})

/** PDF 按页数动态读取（pdfjs-dist）。 */
async function countPdfPages(file: File): Promise<number> {
  // 复用 usePdfPageCount 的实现；这里直接调避免拆组件
  const buf = await file.arrayBuffer()
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const pdfjs: any = await import('pdfjs-dist/build/pdf.mjs').catch(() => import('pdfjs-dist'))
  const loadingTask = pdfjs.getDocument({ data: new Uint8Array(buf) })
  const doc = await loadingTask.promise
  await doc.cleanup()
  await doc.destroy()
  return doc.numPages as number
}

async function readExcel(file: File): Promise<BidRow[]> {
  const buf = await file.arrayBuffer()
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const XLSX: any = await import('xlsx')
  const wb = XLSX.read(buf, { type: 'array' })
  const parsed = parseBidExcel(wb, todayIso())
  if (parsed.errors.length > 0) {
    ElMessage.warning(`Excel 解析告警：${parsed.errors.length} 条（已忽略）`)
  }
  return parsed.rows
}

/** 点击「解析并预览」 */
async function onBuildTree(): Promise<void> {
  if (pdfFiles.value.length === 0) {
    ElMessage.warning('请先上传 PDF')
    return
  }
  if (!pdfForm.customerId) {
    ElMessage.warning('请选择 L1 客户（叶子节点）')
    return
  }
  pdfBuildingTree.value = true
  try {
    // Excel 解析（可选）
    let excelByDrawingNo: Map<string, BidRow> | null = null
    if (excelFiles.value.length > 0) {
      const raw = excelFiles.value[0].raw as File | undefined
      if (raw) {
        const rows = await readExcel(raw)
        excelByDrawingNo = new Map(rows.map((r) => [r.drawingNo, r]))
      }
    }

    const out: TreeRow[] = []
    let pdfIndex = 0
    for (const f of pdfFiles.value) {
      const raw = f.raw as File | undefined
      if (!raw) continue
      const fname = f.name
      const parsed = parseDrawingFilename(fname)
      const pages = await countPdfPages(raw)

      const applyExcel = (row: TreeChild): void => {
        if (!excelByDrawingNo) return
        const matched = excelByDrawingNo.get(row.drawing_no)
        if (!matched) return
        row.applicant_name = matched.applicantName || row.applicant_name
        row.quantity = matched.quantity || row.quantity
        row.is_urgent = matched.isUrgent ?? row.is_urgent
        if (matched.plannedDeliveryDate) row.planned_delivery_date = matched.plannedDeliveryDate
        if (matched.unitPrice) row.order_no = row.order_no  // 占位；不覆盖
      }

      if (pages === 1) {
        out.push({
          uid: `s-${pdfIndex}`,
          kind: 'standalone',
          pdf_index: pdfIndex,
          page_index: 0,
          pdf_filename: fname,
          drawing_no: parsed.drawingNo || '',
          name: parsed.partName || '',
          applicant_name: pdfForm.applicantName || '',
          quantity: 1,
          customer_id: pdfForm.customerId!,
          request_date: pdfForm.requestDate,
          planned_delivery_date: pdfForm.plannedDeliveryDate || pdfForm.requestDate,
          system_delivery_date: null,
          order_no: null,
          note: null,
          is_urgent: pdfForm.isUrgent,
          assembly_uid: null,
          assembly_uid_master_choice: null,
        })
        applyExcel(out[out.length - 1] as TreeChild)
      } else {
        const asmUid = `a-${pdfIndex}`
        const children: TreeChild[] = []
        for (let i = 0; i < pages; i++) {
          const child: TreeChild = {
            uid: `${asmUid}-${i}`,
            kind: 'assembly_child',
            pdf_index: pdfIndex,
            page_index: i,
            pdf_filename: fname,
            drawing_no: parsed.drawingNo
              ? i === 0 ? parsed.drawingNo : `${parsed.drawingNo}-${String(i + 1).padStart(2, '0')}`
              : '',
            name: parsed.partName
              ? i === 0 ? parsed.partName : `${parsed.partName}-${i + 1}`
              : '',
            applicant_name: pdfForm.applicantName || '',
            quantity: 1,
            customer_id: pdfForm.customerId!,
            request_date: pdfForm.requestDate,
            planned_delivery_date: pdfForm.plannedDeliveryDate || pdfForm.requestDate,
            system_delivery_date: null,
            order_no: null,
            note: null,
            is_urgent: pdfForm.isUrgent,
            assembly_uid: asmUid,
            assembly_uid_master_choice: null,
          }
          applyExcel(child)
          children.push(child)
        }
        out.push({
          uid: asmUid,
          kind: 'assembly',
          pdf_index: pdfIndex,
          pdf_filename: fname,
          total_pages: pages,
          drawing_no: parsed.drawingNo || '',
          name: parsed.partName || '',
          applicant_name: pdfForm.applicantName || '',
          customer_id: pdfForm.customerId!,
          request_date: pdfForm.requestDate,
          planned_delivery_date: pdfForm.plannedDeliveryDate || pdfForm.requestDate,
          system_delivery_date: null,
          order_no: null,
          note: null,
          is_urgent: pdfForm.isUrgent,
          children,
        })
      }
      pdfIndex++
    }

    // 兜底：申请人无 id 时批量新建（沿用 PartBidImport 模式）
    if (pdfForm.applicantName && !pdfForm.applicantId) {
      try {
        const rootId = resolveRootCustomerId(pdfForm.customerId) || pdfForm.customerId
        const out2 = await bulkGetOrCreateApplicants([
          { name: pdfForm.applicantName, customer_id: rootId! },
        ])
        if (out2.length > 0) {
          pdfForm.applicantId = out2[0].applicant_id
          // 把树里所有行的 applicant_name 与 id 同步
          const update = (r: TreeRow) => {
            r.applicant_name = pdfForm.applicantName
            if (r.kind === 'assembly') r.children.forEach(update)
          }
          pdfTree.value.forEach(update)
          out.forEach(update)
        }
      } catch (e) {
        ElMessage.warning(`申请人「${pdfForm.applicantName}」自动创建失败：${(e as Error).message}`)
      }
    }

    pdfTree.value = out
    ElMessage.success(`已构建预览：${pdfStandaloneCount.value} 独立 + ${pdfAssemblyCount.value} 装配件`)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '解析失败')
  } finally {
    pdfBuildingTree.value = false
  }
}

/** 点击「提交创建」 */
async function onSubmitPdfTree(): Promise<void> {
  if (pdfTree.value.length === 0) {
    ElMessage.warning('请先解析预览')
    return
  }
  pdfSubmitting.value = true
  try {
    const items: PartBatchTreeItemFE[] = []
    const assemblies: PartBatchTreeAssemblyFE[] = []
    for (const top of pdfTree.value) {
      if (top.kind === 'assembly') {
        assemblies.push({
          uid: top.uid,
          drawing_no: top.drawing_no || null,
          name: top.name || null,
          applicant_name: top.applicant_name,
          applicant_id: pdfForm.applicantId,
          customer_id: top.customer_id,
          request_date: top.request_date,
          planned_delivery_date: top.planned_delivery_date,
          system_delivery_date: top.system_delivery_date,
          order_no: top.order_no,
          note: top.note,
          is_urgent: top.is_urgent,
        })
        for (const c of top.children) {
          items.push({
            pdf_index: top.pdf_index,
            page_index: c.page_index,
            assembly_uid: top.uid,
            is_master: c.assembly_uid_master_choice === `${top.uid}:${c.page_index}`,
            drawing_no: c.drawing_no,
            name: c.name || `子件${c.page_index + 1}`,
            applicant_name: c.applicant_name,
            applicant_id: pdfForm.applicantId,
            quantity: c.quantity,
            customer_id: c.customer_id,
            request_date: c.request_date,
            planned_delivery_date: c.planned_delivery_date,
            system_delivery_date: c.system_delivery_date,
            order_no: c.order_no,
            note: c.note,
            is_urgent: c.is_urgent,
          })
        }
      } else {
        items.push({
          pdf_index: top.pdf_index,
          page_index: top.page_index,
          assembly_uid: null,
          is_master: false,
          drawing_no: top.drawing_no,
          name: top.name || `零件${top.pdf_index + 1}`,
          applicant_name: top.applicant_name,
          applicant_id: pdfForm.applicantId,
          quantity: top.quantity,
          customer_id: top.customer_id,
          request_date: top.request_date,
          planned_delivery_date: top.planned_delivery_date,
          system_delivery_date: top.system_delivery_date,
          order_no: top.order_no,
          note: top.note,
          is_urgent: top.is_urgent,
        })
      }
    }

    // 文件按 pdf_index 顺序对齐
    const orderedFiles: PartBatchFilePayload[] = []
    for (let i = 0; i < pdfFiles.value.length; i++) {
      const f = pdfFiles.value[i]
      if (f.raw) orderedFiles.push({ data: f.raw as File, filename: f.name, contentType: (f.raw as File).type })
    }

    const res = await batchCreatePartsWithPdfs(items, assemblies, orderedFiles)
    if (res.failed && res.failed.length > 0) {
      const msgs = res.failed.slice(0, 5).map((f) => f.message).join('；')
      ElMessageBox.alert(
        `前置校验失败 ${res.failed.length} 条：${msgs}`,
        '提交失败',
        { type: 'error' },
      )
      return
    }
    ElMessage.success(
      `成功创建 ${res.standalone_parts.length} 个独立零件 + ${res.assemblies.length} 个装配件`,
    )
    // 清空 + 跳回
    pdfTree.value = []
    pdfFiles.value = []
    excelFiles.value = []
    activeTab.value = 'manual'
    router.push('/parts?status=PENDING')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '提交失败')
  } finally {
    pdfSubmitting.value = false
  }
}
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