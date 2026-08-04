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
        <el-table-column label="图号" min-width="130" align="center">
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
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip align="center"/>
        <el-table-column prop="quantity" label="数量" min-width="70" align="right" />
        <el-table-column label="申请人" min-width="120" show-overflow-tooltip align="center">
          <template #default="{ row }">{{ row.applicantName || '—' }}</template>
        </el-table-column>
        <el-table-column label="客户" min-width="160" show-overflow-tooltip align="center">
          <template #default="{ row }">{{ row.customerLabel || '—' }}</template>
        </el-table-column>
        <el-table-column prop="plannedDeliveryDate" label="计划交期" min-width="120" align="center"/>
        <el-table-column label="加急" min-width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.isUrgent" type="danger" size="small" effect="dark">加急</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="120" align="center" fixed="right">
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
          可同时拖入应标 / 历史价确认单 Excel 文件（按图号匹配申请人/数量/加急等）。
          多页 PDF 拆为候选页：勾选页可「合并为零件」或「合并为装配件」；
          单页 PDF 直接进入独立零件表。可手动新增 / 删除条目。
        </p>

        <el-card shadow="never" class="pdf-form-card">
          <el-form :model="pdfForm" inline>
            <el-form-item label="L1 客户" required>
              <el-select
                v-model="pdfForm.customerL1Id"
                placeholder="选择一级客户"
                filterable
                clearable
                style="width: 240px"
              >
                <el-option
                  v-for="c in l1Customers"
                  :key="c.id"
                  :label="c.name"
                  :value="c.id"
                />
              </el-select>
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
                  <div class="el-upload__tip">多页 PDF 会自动拆为候选页</div>
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
                <div class="el-upload__text">拖拽应标 / 历史价确认单 Excel（可选；按图号匹配）</div>
                <template #tip>
                  <div class="el-upload__tip">
                    支持：① 应标 Excel（列：物料编号 / 货物(劳务)名称 / 申请人 / 数量 / 单价 / 紧急状态 / 预估交期天数） ·
                    ② 历史价确认单（列：申请部门 / 申请人 / 交期(天) / 物料编号 / 物料名称 / 采购数量 / 含税单价 / 含税价格）
                  </div>
                </template>
              </el-upload>
            </el-col>
          </el-row>

          <!-- PR-H 2026-07-28：3D 模型批量上传（与 PDF / Excel 并排；命名约定 图号_名称.step） -->
          <el-row :gutter="16" style="margin-top: 16px">
            <el-col :span="24">
              <el-upload
                multiple
                accept=".step,.stp,.iges,.igs,.stl,.obj,.3mf"
                :auto-upload="false"
                :file-list="threeDModelFiles"
                :on-change="onThreeDModelChange"
                :on-remove="onThreeDModelRemove"
                drag
              >
                <el-icon class="el-icon--upload"><upload-filled /></el-icon>
                <div class="el-upload__text">拖拽或点击上传 3D 模型（可选；按文件名图号自动挂到对应零件行）</div>
                <template #tip>
                  <div class="el-upload__tip">
                    支持格式：.step / .stp / .iges / .igs / .stl / .obj / .3mf · 文件名约定：<strong>图号_名称.ext</strong>，与下方独立零件 / 装配件子件的图号一致。提交后入库为 <code>kind=THREE_D_MODEL</code>。
                  </div>
                </template>
              </el-upload>
            </el-col>
          </el-row>

          <div class="pdf-actions">
            <el-button
              type="primary"
              :disabled="pdfFiles.length === 0 || pdfBuildingTree"
              @click="rebuildFromUploads"
            >
              <el-icon><magic-stick /></el-icon>
              <span>{{ allPdfs.length > 0 ? '重新解析拆分' : '解析拆分' }}</span>
            </el-button>
            <span class="tree-stat">
              源文件 {{ allPdfs.length }} 个 · 独立零件 {{ standaloneParts.length }} 条 ·
              装配件 {{ assemblies.length }} 个（共 {{ totalAssemblyChildren }} 子件）
            </span>
          </div>
        </el-card>

        <!-- ① 源文件区 -->
        <el-card v-if="allPdfs.length > 0" shadow="never" class="pdf-source-card">
          <div class="source-header">
            <span class="title">源文件区</span>
            <span class="hint-inline">
              勾选页后用右侧按钮归组；勾选父级（PDF 文件名） = 全选该 PDF 全部页
            </span>
            <div class="source-actions">
              <el-button
                type="primary"
                plain
                :disabled="selectedPages.size === 0"
                @click="mergeSelectedAsPart"
              >
                <el-icon><link /></el-icon>
                <span>合并为零件</span>
              </el-button>
              <el-button
                type="warning"
                plain
                :disabled="selectedPages.size < 2"
                @click="mergeSelectedAsAssembly"
              >
                <el-icon><files /></el-icon>
                <span>合并为装配件</span>
              </el-button>
              <el-button @click="clearSelection">清空选择</el-button>
            </div>
          </div>

          <el-table
            ref="sourceTableRef"
            :data="sourceTree"
            row-key="id"
            :tree-props="{ children: 'children' }"
            default-expand-all
            border
            class="pdf-source-table"
            @selection-change="onSourceSelectionChange"
          >
            <el-table-column type="selection" width="55" />
            <el-table-column label="PDF 文件名" min-width="280" align="center">
              <template #default="{ row }">
                <el-link
                  type="primary"
                  underline="never"
                  class="filename-link"
                  @click="previewSourceRow(row as SourceTreeRow)"
                >
                  {{ (row as SourceTreeRow).filename }}
                </el-link>
              </template>
            </el-table-column>
            <el-table-column label="页" min-width="60" align="center">
              <template #default="{ row }">
                <span v-if="(row as SourceTreeRow).pageIndex === null">{{ (row as SourceTreeRow).totalPages }}</span>
                <span v-else>{{ (row as SourceTreeRow).pageIndex! + 1 }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" min-width="100" align="center">
              <template #default="{ row }">
                <el-button
                  v-if="(row as SourceTreeRow).pageIndex === null"
                  link
                  type="danger"
                  @click="removePdf((row as SourceTreeRow).pdfSourceUid)"
                >删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- ② 独立零件表 + ③ 装配件表（上下堆叠） -->
        <div v-if="allPdfs.length > 0">
          <el-card shadow="never" class="pdf-standalone-card">
              <div class="table-header">
                <span class="title">独立零件（{{ standaloneParts.length }}）</span>
                <el-button type="primary" plain size="small" @click="addManualPart">
                  <el-icon><plus /></el-icon>
                  <span>新增零件</span>
                </el-button>
              </div>
              <el-table
                ref="standaloneTableRef"
                :data="standaloneParts"
                row-key="uid"
                border
                size="small"
                empty-text="还没有独立零件。可在「源文件区」勾选页后合并，或直接新增。"
                class="pdf-standalone-table"
              >
                <el-table-column width="36" align="center" label="">
                  <template #default>
                    <el-icon class="drag-handle" title="拖动排序"><Rank /></el-icon>
                  </template>
                </el-table-column>
                <el-table-column label="图号" min-width="140" align="center">
                  <template #default="{ row }">
                    <el-input
                      v-model="row.drawing_no"
                      size="small"
                      :class="{ 'is-error': !row.drawing_no }"
                      placeholder="必填"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="名称" min-width="160" align="center">
                  <template #default="{ row }">
                    <el-input v-model="row.name" size="small" placeholder="选填" />
                  </template>
                </el-table-column>
                <el-table-column label="图纸" min-width="200" show-overflow-tooltip align="center">
                  <template #default="{ row }">
                    <el-link
                      type="primary"
                      underline="never"
                      class="filename-link"
                      @click="previewStandalonePart(row as StandalonePartRow)"
                    >
                      {{ pdfSourceLabel(row.pdfSourceUid) }}
                    </el-link>
                  </template>
                </el-table-column>
                <el-table-column label="数量" min-width="90" align="center">
                  <template #default="{ row }">
                    <el-input-number
                      v-model="row.quantity"
                      :min="1"
                      :max="9999"
                      size="small"
                      controls-position="right"
                      style="width: 90px"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="含税单价" min-width="120" align="center">
                  <template #default="{ row }">
                    <el-input-number
                      v-model="row.unit_price"
                      :min="0"
                      :precision="2"
                      :step="1"
                      size="small"
                      controls-position="right"
                      placeholder="可填"
                      style="width: 110px"
                      @change="(v: number | undefined) => onUnitPriceChange(row as StandalonePartRow, v)"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="含税价格" min-width="120" align="center">
                  <template #default="{ row }">
                    <el-input-number
                      v-model="row.total_price"
                      :min="0"
                      :precision="2"
                      :step="1"
                      size="small"
                      controls-position="right"
                      placeholder="可填"
                      style="width: 110px"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="3D" min-width="60" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.three_d_index != null" type="success" size="small">3D ✓</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="计划交期" min-width="160" align="center">
                  <template #default="{ row }">
                    <el-date-picker
                      v-model="row.planned_delivery_date"
                      type="date"
                      value-format="YYYY-MM-DD"
                      size="small"
                      clearable
                      placeholder="选交期"
                      style="width: 140px"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="分厂" min-width="160" align="center">
                  <template #default="{ row }">
                    <el-select
                      v-model="row.customer_id"
                      placeholder="选分厂"
                      filterable
                      clearable
                      size="small"
                      style="width: 150px"
                      :disabled="l2Customers.length === 0"
                      @change="(v: string) => onL2Change(row as StandalonePartRow, v)"
                    >
                      <el-option
                        v-for="c in l2Customers"
                        :key="c.id"
                        :label="c.name"
                        :value="c.id"
                      />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column label="申请人" min-width="160" align="center">
                  <template #default="{ row }">
                    <el-autocomplete
                      v-model="row.applicant_name"
                      value-key="name"
                      :fetch-suggestions="(q: string, cb: any) => querySearch(q, cb)"
                      :trigger-on-focus="true"
                      :debounce="0"
                      :loading="applicantLoading"
                      :disabled="!pdfForm.customerL1Id"
                      placeholder="选填"
                      clearable
                      size="small"
                      style="width: 150px"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="加急" min-width="70" align="center">
                  <template #default="{ row }">
                    <el-switch v-model="row.is_urgent" />
                  </template>
                </el-table-column>
                <el-table-column label="备注" min-width="140" align="center">
                  <template #default="{ row }">
                    <el-input v-model="row.note" size="small" type="textarea" :rows="1" placeholder="选填" />
                  </template>
                </el-table-column>
                <el-table-column label="操作" min-width="120" align="center" fixed="right">
                  <template #default="{ row }">
                    <el-button
                      v-if="row.mergedFrom && row.mergedFrom.length > 1"
                      link
                      type="warning"
                      size="small"
                      @click="splitStandalonePart(row as StandalonePartRow)"
                    >拆分</el-button>
                    <el-button link type="danger" size="small" @click="removeStandalonePart(row.uid)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
            <el-card shadow="never" class="pdf-assembly-card">
              <div class="table-header">
                <span class="title">装配件（{{ assemblies.length }}）</span>
                <el-button type="primary" plain size="small" @click="addManualAssembly">
                  <el-icon><plus /></el-icon>
                  <span>新增装配件</span>
                </el-button>
              </div>
              <el-table
                ref="assembliesTableRef"
                :data="assemblies"
                row-key="uid"
                border
                size="small"
                empty-text="还没有装配件。可在「源文件区」勾选多页后合并，或直接新增。"
                class="pdf-assembly-table"
              >
                <el-table-column width="36" align="center" label="">
                  <template #default>
                    <el-icon class="drag-handle" title="拖动排序"><Rank /></el-icon>
                  </template>
                </el-table-column>
                <el-table-column type="expand">
                  <template #default="{ row }">
                    <el-table :data="row.children" size="small" :show-header="true" class="child-table">
                      <el-table-column label="页" min-width="60" align="center">
                        <template #default="{ row: c }">P{{ c.page_index + 1 }}</template>
                      </el-table-column>
                      <el-table-column label="图号" min-width="140" align="center">
                        <template #default="{ row: c }">
                          <el-input v-model="c.drawing_no" size="small" />
                        </template>
                      </el-table-column>
                      <el-table-column label="名称" min-width="140" align="center">
                        <template #default="{ row: c }">
                          <el-input v-model="c.name" size="small" />
                        </template>
                      </el-table-column>
                      <el-table-column label="数量" min-width="90" align="center">
                        <template #default="{ row: c }">
                          <el-input-number
                            v-model="c.quantity"
                            :min="1"
                            :max="9999"
                            size="small"
                            controls-position="right"
                            style="width: 90px"
                          />
                        </template>
                      </el-table-column>
                      <el-table-column label="含税单价" min-width="110" align="center">
                        <template #default="{ row: c }">
                          <el-input-number
                            v-model="c.unit_price"
                            :min="0"
                            :precision="2"
                            :step="1"
                            size="small"
                            controls-position="right"
                            placeholder="可填"
                            style="width: 100px"
                            @change="(v: number | undefined) => onChildUnitPriceChange(c as AssemblyChildRow, v)"
                          />
                        </template>
                      </el-table-column>
                      <el-table-column label="含税价格" min-width="110" align="center">
                        <template #default="{ row: c }">
                          <el-input-number
                            v-model="c.total_price"
                            :min="0"
                            :precision="2"
                            :step="1"
                            size="small"
                            controls-position="right"
                            placeholder="可填"
                            style="width: 100px"
                          />
                        </template>
                      </el-table-column>
                      <el-table-column label="3D" min-width="55" align="center">
                        <template #default="{ row: c }">
                          <el-tag v-if="c.three_d_index != null" type="success" size="small">3D ✓</el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column label="计划交期" min-width="150" align="center">
                        <template #default="{ row: c }">
                          <el-date-picker
                            v-model="c.planned_delivery_date"
                            type="date"
                            value-format="YYYY-MM-DD"
                            size="small"
                            clearable
                            placeholder="选交期"
                            style="width: 130px"
                          />
                        </template>
                      </el-table-column>
                      <el-table-column label="加急" min-width="70" align="center">
                        <template #default="{ row: c }">
                          <el-switch v-model="c.is_urgent" />
                        </template>
                      </el-table-column>
                      <el-table-column label="备注" min-width="120" align="center">
                        <template #default="{ row: c }">
                          <el-input v-model="c.note" size="small" type="textarea" :rows="1" placeholder="选填" />
                        </template>
                      </el-table-column>
                    </el-table>
                  </template>
                </el-table-column>
                <el-table-column label="图号" min-width="140" align="center">
                  <template #default="{ row }">
                    <el-input v-model="row.drawing_no" size="small" />
                  </template>
                </el-table-column>
                <el-table-column label="名称" min-width="160" align="center">
                  <template #default="{ row }">
                    <el-input v-model="row.name" size="small" />
                  </template>
                </el-table-column>
                <el-table-column label="图纸" min-width="180" show-overflow-tooltip align="center">
                  <template #default="{ row }">
                    <el-link
                      type="primary"
                      underline="never"
                      class="filename-link"
                      @click="previewPdfSourceByUid(row.pdfSourceUid)"
                    >
                      {{ pdfSourceLabel(row.pdfSourceUid) }}
                    </el-link>
                  </template>
                </el-table-column>
                <el-table-column label="分厂" min-width="160" align="center">
                  <template #default="{ row }">
                    <el-select
                      v-model="row.customer_id"
                      placeholder="选分厂"
                      filterable
                      clearable
                      size="small"
                      style="width: 150px"
                      :disabled="l2Customers.length === 0"
                      @change="(v: string) => onL2Change(row as AssemblyRow, v)"
                    >
                      <el-option
                        v-for="c in l2Customers"
                        :key="c.id"
                        :label="c.name"
                        :value="c.id"
                      />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column label="申请人" min-width="160" align="center">
                  <template #default="{ row }">
                    <el-autocomplete
                      v-model="row.applicant_name"
                      value-key="name"
                      :fetch-suggestions="(q: string, cb: any) => querySearch(q, cb)"
                      :trigger-on-focus="true"
                      :debounce="0"
                      :loading="applicantLoading"
                      :disabled="!pdfForm.customerL1Id"
                      placeholder="选填"
                      clearable
                      size="small"
                      style="width: 150px"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="套数" min-width="80" align="center">
                  <template #default="{ row }">
                    <el-input-number
                      v-model="row.quantity"
                      :min="1"
                      :max="9999"
                      size="small"
                      controls-position="right"
                      style="width: 85px"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="装配图（总装图）" min-width="180" align="center">
                  <template #default="{ row }">
                    <el-select
                      v-model="row.masterPageIndex"
                      placeholder="无（清空即不指定）"
                      clearable
                      size="small"
                      style="width: 170px"
                    >
                      <el-option
                        v-for="c in row.children"
                        :key="c.page_index"
                        :label="`P${c.page_index + 1}（${c.drawing_no || '子件'}）`"
                        :value="c.page_index"
                      />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column label="计划交期" min-width="160" align="center">
                  <template #default="{ row }">
                    <el-date-picker
                      v-model="row.planned_delivery_date"
                      type="date"
                      value-format="YYYY-MM-DD"
                      size="small"
                      clearable
                      placeholder="选交期"
                      style="width: 140px"
                      @change="(v: string) => onAsmPlannedChange(row as AssemblyRow, v)"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="备注" min-width="140" align="center">
                  <template #default="{ row }">
                    <el-input v-model="row.note" size="small" type="textarea" :rows="1" placeholder="选填" />
                  </template>
                </el-table-column>
                <el-table-column label="子件数" min-width="70" align="center">
                  <template #default="{ row }">{{ row.children.length }}</template>
                </el-table-column>
                <el-table-column label="操作" min-width="80" align="center" fixed="right">
                  <template #default="{ row }">
                    <el-button link type="danger" size="small" @click="removeAssembly(row.uid)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
        </div>

    <!-- 提交按钮：放在装配件表下方（form-card 顶部仅保留解析拆分）。 -->
    <div v-if="allPdfs.length > 0" class="pdf-footer-actions">
      <el-button
        type="success"
        size="large"
        :disabled="(standaloneParts.length === 0 && assemblies.length === 0) || pdfSubmitting"
        @click="onSubmitPdfTree"
      >
        <el-icon><check /></el-icon>
        <span>提交创建（{{ standaloneParts.length }} 个零件 + {{ assemblies.length }} 个装配件）</span>
      </el-button>
    </div>
      </el-tab-pane>
    </el-tabs>

    <!-- PDF 文件名点击触发的全屏预览（Tab 2）。blob URL 生命周期见
         openPdfPreview / closePdfPreview。 -->
    <el-dialog
      v-model="pdfPreviewVisible"
      :title="pdfPreviewing?.title ?? 'PDF 预览'"
      fullscreen
      destroy-on-close
      :before-close="closePdfPreview"
    >
      <PdfViewer
        v-if="pdfPreviewing?.url"
        :url="pdfPreviewing.url"
        :page="pdfPreviewing.page"
      />
    </el-dialog>

    <!-- 手动新增零件 dialog -->
    <el-dialog
      v-model="manualPartDialogVisible"
      title="新增独立零件"
      width="520px"
      destroy-on-close
    >
      <el-form :model="manualPartForm" label-width="80px">
        <el-form-item label="图号" required>
          <el-input v-model="manualPartForm.drawing_no" placeholder="必填" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="manualPartForm.name" placeholder="选填" />
        </el-form-item>
        <el-form-item label="图纸" required>
          <el-upload
            accept=".pdf"
            :auto-upload="false"
            :file-list="manualPartFileList"
            :on-change="onManualPartFileChange"
            :on-remove="onManualPartFileRemove"
            :show-file-list="true"
          >
            <el-button>选择 PDF</el-button>
            <template #tip>
              <div class="el-upload__tip">单文件 · 与源文件区无关</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manualPartDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!manualPartFormValid" @click="confirmManualPart">添加</el-button>
      </template>
    </el-dialog>

    <!-- 手动新增装配件 dialog -->
    <el-dialog
      v-model="manualAsmDialogVisible"
      title="新增装配件"
      width="520px"
      destroy-on-close
    >
      <el-form :model="manualAsmForm" label-width="80px">
        <el-form-item label="图号" required>
          <el-input v-model="manualAsmForm.drawing_no" placeholder="必填" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="manualAsmForm.name" placeholder="选填" />
        </el-form-item>
        <el-form-item label="图纸" required>
          <el-upload
            accept=".pdf"
            :auto-upload="false"
            :file-list="manualAsmFileList"
            :on-change="onManualAsmFileChange"
            :on-remove="onManualAsmFileRemove"
            :show-file-list="true"
          >
            <el-button>选择 PDF</el-button>
            <template #tip>
              <div class="el-upload__tip">上传后按页数自动生成子件</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manualAsmDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!manualAsmFormValid" @click="confirmManualAssembly">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ElMessage,
  ElMessageBox,
  type FormInstance,
  type FormRules,
  type UploadFile,
} from 'element-plus'
import { DocumentAdd, Picture, Plus, Rank, Upload } from '@element-plus/icons-vue'
import PdfViewer from '@/components/PdfViewer.vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import Sortable from 'sortablejs'
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
/** Tab 2 PDF 批量上传专用：仅展示一级客户。Tab 1 手动录入仍走 cascader 全树。 */
const l1Customers = computed(() =>
  customers.value
    .filter((c) => c.parent_id === null)
    .map((c) => ({ id: c.id, name: c.name })),
)
/** Tab 2 分厂下拉专用：所选 L1 的二级子客户。 */
const l2Customers = computed(() =>
  pdfForm.customerL1Id
    ? customers.value
        .filter((c) => c.parent_id === pdfForm.customerL1Id)
        .map((c) => ({ id: c.id, name: c.name }))
    : [],
)
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
  nextTick(() => {
    initStandaloneSortable()
    initAssembliesSortable()
  })
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
import { parseBidExcel, type BidRow, type ParseResult } from '@/utils/bidExcelParser'
import { parseHistoricalPriceExcel } from '@/utils/historicalPriceExcelParser'
import { parseDrawingFilename } from '@/utils/drawingFilename'
import { pdfjsLib } from '@/utils/pdfjs'

const route = useRoute()
const activeTab = ref<string>(typeof route.query.tab === 'string' ? route.query.tab : 'manual')
watch(activeTab, (v) => {
  router.replace({ query: { ...route.query, tab: v } })
})

interface PdfFormState {
  /** 一级客户 id（Tab 2 必选；决定 serial_prefix 来源 + 二级客户候选范围）。 */
  customerL1Id: string | null
  requestDate: string
}

const pdfForm = reactive<PdfFormState>({
  customerL1Id: null,
  requestDate: todayIso(),
})

// PDF Tab 一级客户切换 → 拉一次该客户下申请人全集。
// useApplicantSearch.loadForCustomer 内部对同一 rootCustomerId 不重拉；
// 切换到空客户则清空缓存。immediate: false 避免首次 null 时多余调用。
watch(
  () => pdfForm.customerL1Id,
  (next) => {
    void loadApplicantsForCustomer(next)
  },
  { immediate: false },
)

// PDF / Excel 文件列表（el-upload 控件绑定）
const pdfFiles = ref<UploadFile[]>([])
const excelFiles = ref<UploadFile[]>([])
// PR-H 2026-07-28：3D 模型批量上传（.step / .stp / .iges / .igs / .stl / .obj / .3mf）
const threeDModelFiles = ref<UploadFile[]>([])
const pdfBuildingTree = ref(false)
const pdfSubmitting = ref(false)

// ============ 新数据模型（2026-07-22 重构） ============

/** 源文件区表格的树节点。多页 PDF 是父节点，子页是 children。 */
interface SourceTreeRow {
  /** 顶层 = pdfSourceUid；子行 = `${pdfSourceUid}:p${pageIndex}`。 */
  id: string
  pdfSourceUid: string
  /** null = PDF 顶层；>=0 = 子页。 */
  pageIndex: number | null
  filename: string
  totalPages: number
  children?: SourceTreeRow[]
}

/** 一份「图纸源」：上传的原始 PDF 或前端合成的新 PDF。 */
interface PdfSource {
  uid: string
  raw: Blob
  filename: string
  totalPages: number
  /** true = 由 pdf-lib 合并产生；false = 原 PDF。 */
  synthesized: boolean
  /** 合成来源（仅 synthesized=true）。 */
  synthesizedFrom?: { pdfUid: string; pageIndices: number[] }[]
  /** 原始 PDF 的 uid（合成时记录）。 */
  originPdfUid?: string
}

/** 独立零件表的一行。 */
interface StandalonePartRow {
  uid: string
  pdfSourceUid: string
  pageCount: number
  /** 合成来源（仅 pageCount > 1）。 */
  mergedFrom?: { pdfUid: string; pageIndex: number }[]
  drawing_no: string
  name: string
  applicant_name: string
  customer_id: string  // 二级客户 id（L2 leaf）；后端 customer_id 校验需要叶子节点
  customer_name: string  // 显示用，提交时不发送
  request_date: string
  planned_delivery_date: string
  system_delivery_date: string | null
  order_no: string | null
  note: string | null
  is_urgent: boolean
  quantity: number
  /** PR-H 2026-07-28：含税单价（来自历史价确认单 G 列，可手动覆盖） */
  unit_price: number | null
  /** PR-H 2026-07-28：含税总价（来自历史价确认单 I 列；空时 = unit_price × quantity） */
  total_price: number | null
  /** PR-H 2026-07-28：3D 模型数组下标；null = 不挂 */
  three_d_index: number | null
}

/** 装配件子件。分厂 / 申请人由顶层 AssemblyRow 指定，提交时复制到每条 item。 */
interface AssemblyChildRow {
  uid: string
  pdfSourceUid: string
  page_index: number
  drawing_no: string
  name: string
  quantity: number
  is_urgent: boolean
  request_date: string
  planned_delivery_date: string
  system_delivery_date: string | null
  order_no: string | null
  note: string | null
  /** PR-H 2026-07-28：含税单价（来自历史价确认单 G 列） */
  unit_price: number | null
  /** PR-H 2026-07-28：含税总价 */
  total_price: number | null
  /** PR-H 2026-07-28：3D 模型数组下标；null = 不挂 */
  three_d_index: number | null
}

/** 装配件顶层行。 */
interface AssemblyRow {
  uid: string
  pdfSourceUid: string
  drawing_no: string
  name: string
  applicant_name: string
  customer_id: string  // 二级客户 id（L2 leaf）
  customer_name: string
  request_date: string
  planned_delivery_date: string
  system_delivery_date: string | null
  order_no: string | null
  note: string | null
  is_urgent: boolean
  masterPageIndex: number | null
  /** 装配体套数（默认 1）。2026-08-04 新增：用于背面页 Q: 打印 */
  quantity: number
  children: AssemblyChildRow[]
}

const allPdfs = ref<PdfSource[]>([])
/** 源文件区选中的页：key = `${pdfUid}:${pageIndex}`（pageIndex 0-based）。 */
const selectedPages = ref<Set<string>>(new Set())
const standaloneParts = ref<StandalonePartRow[]>([])
const assemblies = ref<AssemblyRow[]>([])
// PR-H 2026-07-28：拖拽排序 — 表格 DOM ref + Sortable 实例句柄
const standaloneTableRef = ref<{ $el?: HTMLElement } | null>(null)
const assembliesTableRef = ref<{ $el?: HTMLElement } | null>(null)
let standaloneSortable: Sortable | null = null
let assembliesSortable: Sortable | null = null

/** 只用于源文件区表格展示的原始（未合成）PDF。 */
const originalPdfs = computed(() => allPdfs.value.filter((s) => !s.synthesized))
const totalAssemblyChildren = computed(() =>
  assemblies.value.reduce((sum, a) => sum + a.children.length, 0),
)

/** 源文件区树状数据：仅显示多页 PDF（单页 PDF 已直接进独立零件表）。
 *  多页 PDF → 1 父行 + N 子页。 */
const sourceTree = computed<SourceTreeRow[]>(() =>
  originalPdfs.value
    .filter((src) => src.totalPages > 1)
    .map((src) => ({
      id: src.uid,
      pdfSourceUid: src.uid,
      pageIndex: null,
      filename: src.filename,
      totalPages: src.totalPages,
      children: Array.from({ length: src.totalPages }, (_, i) => ({
        id: `${src.uid}:p${i}`,
        pdfSourceUid: src.uid,
        pageIndex: i,
        filename: `${src.filename}（第 ${i + 1} 页）`,
        totalPages: src.totalPages,
      })),
    })),
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
// PR-H 2026-07-28：3D 模型上传钩子
function onThreeDModelChange(file: UploadFile): void {
  threeDModelFiles.value = fileList(threeDModelFiles.value, file, '.step,.stp,.iges,.igs,.stl,.obj,.3mf')
}
function onThreeDModelRemove(file: UploadFile): void {
  threeDModelFiles.value = threeDModelFiles.value.filter((f) => f.uid !== file.uid)
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

/** PDF 按页数动态读取（pdfjs-dist）。与 composables/usePdfPageCount.ts 同模式：
 *  destroy() 在 PDFDocumentLoadingTask 上，不在 PDFDocumentProxy 上（旧实现
 *  调 doc.destroy() 抛 "doc.destroy is not a function"）。 */
async function countPdfPages(file: File): Promise<number> {
  const buf = await file.arrayBuffer()
  const task = pdfjsLib.getDocument({ data: buf })
  try {
    const doc = await task.promise
    return doc.numPages
  } finally {
    await task.destroy()
  }
}

async function readExcel(file: File): Promise<BidRow[]> {
  const buf = await file.arrayBuffer()
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const XLSX: any = await import('xlsx')
  const wb = XLSX.read(buf, { type: 'array' })
  const sheetNames = wb.SheetNames
  let parsed: ParseResult
  if (sheetNames.includes('历史价确认单明细')) {
    parsed = parseHistoricalPriceExcel(wb, todayIso())
  } else if (sheetNames.includes('招标项目-标的')) {
    parsed = parseBidExcel(wb, todayIso())
  } else {
    throw new Error(
      `Excel 格式无法识别（需要「历史价确认单明细」或「招标项目-标的」sheet），当前文件 sheet: ${sheetNames.join(', ')}`,
    )
  }
  if (parsed.errors.length > 0) {
    ElMessage.warning(`Excel 解析告警：${parsed.errors.length} 条（已忽略）`)
  }
  return parsed.rows
}

/** 从 pdfFiles 重新构建 allPdfs + standaloneParts（不自动组成装配件）。
 *  多页 PDF → 每页独立成一行；单页 PDF → 一行。 */
async function rebuildFromUploads(): Promise<void> {
  if (pdfFiles.value.length === 0) {
    ElMessage.warning('请先上传 PDF')
    return
  }
  if (!pdfForm.customerL1Id) {
    ElMessage.warning('请选择一级客户')
    return
  }
  pdfBuildingTree.value = true
  try {
    // 解析 Excel（可选）
    let excelByDrawingNo: Map<string, BidRow> | null = null
    if (excelFiles.value.length > 0) {
      const raw = excelFiles.value[0].raw as File | undefined
      if (raw) {
        const rows = await readExcel(raw)
        excelByDrawingNo = new Map(rows.map((r) => [r.drawingNo, r]))
      }
    }

    const sources: PdfSource[] = []
    const rows: StandalonePartRow[] = []
    const unparsedPdfNames: string[] = []

    for (const f of pdfFiles.value) {
      const raw = f.raw as File | undefined
      if (!raw) continue
      const fname = f.name
      const parsed = parseDrawingFilename(fname)
      if (!parsed.drawingNo && !parsed.partName) {
        unparsedPdfNames.push(fname)
      }
      const totalPages = await countPdfPages(raw)
      const pdfUid = `pdf-${f.uid}`
      sources.push({
        uid: pdfUid,
        raw,
        filename: fname,
        totalPages,
        synthesized: false,
      })
      if (totalPages === 1) {
        // 单页 PDF → 自动进独立零件表
        rows.push(makeStandaloneRow({
          pdfSourceUid: pdfUid,
          drawing_no: parsed.drawingNo || '',
          name: parsed.partName || '',
        }))
      }
      // 多页 PDF 不预生成任何行；用户从源文件区显式选择页 → 合并
    }

    allPdfs.value = sources
    standaloneParts.value = rows
    assemblies.value = []
    selectedPages.value = new Set()
    if (excelByDrawingNo) applyExcelToAll(excelByDrawingNo)
    // PR-H 2026-07-28：按 drawing_no 把 3D 模型挂到对应独立零件 / 装配件子件行
    linkThreeDModelsToRows()

    if (unparsedPdfNames.length > 0) {
      ElMessage.warning(
        `以下 ${unparsedPdfNames.length} 个 PDF 文件名无法识别图号/名称，请手动填写：` +
          unparsedPdfNames.slice(0, 5).join('、') +
          (unparsedPdfNames.length > 5 ? ' …' : ''),
      )
    }
    ElMessage.success(`已解析：源 ${sources.length} 个 · 候选 ${rows.length} 页`)
  } catch (e) {
    ElMessage.error((e as Error).message ?? '解析失败')
  } finally {
    pdfBuildingTree.value = false
  }
}

/** 用 Excel 行覆盖表内字段（applicant / quantity / planned_delivery_date + 分厂 L2 + 单价）。
 *  2026-07-30 起不再覆盖 is_urgent（批量 PDF 导入默认全部不加急，由用户手动 switch）。 */
function applyExcelToAll(excelByDrawingNo: Map<string, BidRow>): void {
  for (const r of standaloneParts.value) {
    const matched = excelByDrawingNo.get(r.drawing_no)
    if (!matched) continue
    r.applicant_name = matched.applicantName || r.applicant_name
    r.quantity = matched.quantity || r.quantity
    // 2026-07-30：批量 PDF 导入不再从应标 Excel 继承 is_urgent，默认全部不加急；
    // 用户在 el-switch 单独打开加急。
    if (matched.plannedDeliveryDate) r.planned_delivery_date = matched.plannedDeliveryDate
    // PR-H 2026-07-28：含税单价 / 总价
    if (matched.unitPrice != null) r.unit_price = matched.unitPrice
    if (matched.totalPrice != null) r.total_price = matched.totalPrice
    // 自动解析二级客户（分厂）
    if (!r.customer_id) {
      const l2Id = resolveL2CustomerId(matched.deptName)
      if (l2Id) {
        r.customer_id = l2Id
        const c = customers.value.find((x) => x.id === l2Id)
        r.customer_name = c?.name ?? ''
      }
    }
  }
  for (const a of assemblies.value) {
    // 顶层分厂 / 申请人：用第一个能在 Excel 查到的 child 的行（child 不再各自持有这两个字段）
    const firstHit = a.children
      .map((c) => excelByDrawingNo.get(c.drawing_no))
      .find((m): m is BidRow => !!m)
    if (firstHit) {
      if (!a.customer_id) {
        const l2Id = resolveL2CustomerId(firstHit.deptName)
        if (l2Id) {
          a.customer_id = l2Id
          const c = customers.value.find((x) => x.id === l2Id)
          a.customer_name = c?.name ?? ''
        }
      }
      a.applicant_name = firstHit.applicantName || a.applicant_name
    }
    // 子件继承 quantity / urgent / planned_delivery_date / 单价 / 总价
    for (const c of a.children) {
      const matched = excelByDrawingNo.get(c.drawing_no)
      if (!matched) continue
      c.quantity = matched.quantity || c.quantity
      // 2026-07-30：子件不再从应标 Excel 继承 is_urgent。
      if (matched.plannedDeliveryDate) c.planned_delivery_date = matched.plannedDeliveryDate
      // PR-H 2026-07-28：含税单价 / 总价
      if (matched.unitPrice != null) c.unit_price = matched.unitPrice
      if (matched.totalPrice != null) c.total_price = matched.totalPrice
    }
  }
}

/** 把 Excel 的「申请人所在一级部门名称」（如"二厂"）解析成 L2 客户 id。
 *  仅在所选 L1 客户的子客户里查找。 */
function resolveL2CustomerId(deptName: string): string | null {
  if (!pdfForm.customerL1Id || !deptName) return null
  const match = customers.value.find(
    (c) => c.parent_id === pdfForm.customerL1Id && c.name.trim() === deptName.trim(),
  )
  return match?.id ?? null
}

/** PR-H 2026-07-28：含税单价改动 → 自动联动 total_price（仅在用户未手动锁定时）；
 *  保留 Excel 回填值优先 —— 若 total_price 已被 Excel 写入过且与自动算的不一致，
 *  仍按 Excel 的值，不强制覆盖（用户可手动改回）。 */
function onUnitPriceChange(row: StandalonePartRow, v: number | undefined): void {
  row.unit_price = v ?? null
  // 仅当用户没明确设置过 total_price 时自动算
  if (row.quantity > 0 && row.unit_price != null) {
    row.total_price = row.unit_price * row.quantity
  }
}
function onChildUnitPriceChange(c: AssemblyChildRow, v: number | undefined): void {
  c.unit_price = v ?? null
  if (c.quantity > 0 && c.unit_price != null) {
    c.total_price = c.unit_price * c.quantity
  }
}

/** PR-H 2026-07-28：3D 模型支持扩展名（与后端 _file_kind_policy.THREE_D_MODEL 对齐）。 */
const THREE_D_EXTS = ['step', 'stp', 'iges', 'igs', 'stl', 'obj', '3mf']

/** 把 3D 模型按文件名解析的 drawing_no 自动挂到独立零件 / 装配件子件行。
 *  - 文件名约定：图号_名称.ext（与 PDF 解析共用 `parseDrawingFilename`，先剥扩展名）。
 *  - 已挂过该图号的 → 跳过（不重复挂）。
 *  - 找不到匹配行 → ElMessage.warning（不报错，整批仍可提交）。 */
function linkThreeDModelsToRows(): void {
  if (threeDModelFiles.value.length === 0) return
  const warns: string[] = []
  threeDModelFiles.value.forEach((f, idx) => {
    const raw = f.raw as File | undefined
    if (!raw) return
    const fname = f.name
    const noExt = THREE_D_EXTS.reduce(
      (acc, ext) => acc.replace(new RegExp(`\\.${ext}$`, 'i'), ''),
      fname,
    )
    const parsed = parseDrawingFilename(`${noExt}.pdf`) // 复用 PDF 解析逻辑
    if (!parsed.drawingNo) {
      warns.push(`3D 模型「${fname}」文件名无法识别图号，已忽略`)
      return
    }
    // 先尝试独立零件，再试装配件子件
    const spMatch = standaloneParts.value.find((r) => r.drawing_no === parsed.drawingNo)
    if (spMatch) {
      if (spMatch.three_d_index != null) {
        warns.push(`图号 ${parsed.drawingNo} 已挂载 3D 模型，跳过「${fname}」`)
        return
      }
      spMatch.three_d_index = idx
      return
    }
    let attached = false
    for (const a of assemblies.value) {
      const childMatch = a.children.find((c) => c.drawing_no === parsed.drawingNo)
      if (childMatch) {
        if (childMatch.three_d_index != null) {
          warns.push(`图号 ${parsed.drawingNo} 已挂载 3D 模型，跳过「${fname}」`)
          attached = true
          break
        }
        childMatch.three_d_index = idx
        attached = true
        break
      }
    }
    if (!attached) warns.push(`未找到图号 ${parsed.drawingNo} 对应零件行，已忽略「${fname}」`)
  })
  if (warns.length > 0) {
    ElMessage.warning(
      `3D 模型挂载提示（${warns.length} 条）：\n` +
        warns.slice(0, 5).join('\n') +
        (warns.length > 5 ? '\n…' : ''),
    )
  }
}

/** 默认表单填充一个独立零件行。 */
function makeStandaloneRow(opts: {
  pdfSourceUid: string
  drawing_no: string
  name: string
  pageCount?: number
  mergedFrom?: { pdfUid: string; pageIndex: number }[]
}): StandalonePartRow {
  return {
    uid: `part-${makeUid()}`,
    pdfSourceUid: opts.pdfSourceUid,
    pageCount: opts.pageCount ?? 1,
    mergedFrom: opts.mergedFrom,
    drawing_no: opts.drawing_no,
    name: opts.name,
    applicant_name: '',
    customer_id: '',
    customer_name: '',
    request_date: pdfForm.requestDate,
    planned_delivery_date: '',
    system_delivery_date: null,
    order_no: null,
    note: null,
    is_urgent: false,
    quantity: 1,
    // PR-H 2026-07-28
    unit_price: null,
    total_price: null,
    three_d_index: null,
  }
}

/** 装配件子件默认填充（分厂 / 申请人由顶层 AssemblyRow 指定）。 */
function makeAssemblyChild(opts: {
  pdfSourceUid: string
  pageIndex: number
  drawing_no: string
  name: string
}): AssemblyChildRow {
  return {
    uid: `child-${makeUid()}`,
    pdfSourceUid: opts.pdfSourceUid,
    page_index: opts.pageIndex,
    drawing_no: opts.drawing_no,
    name: opts.name,
    quantity: 1,
    is_urgent: false,
    request_date: pdfForm.requestDate,
    planned_delivery_date: '',
    system_delivery_date: null,
    order_no: null,
    note: null,
    // PR-H 2026-07-28
    unit_price: null,
    total_price: null,
    three_d_index: null,
  }
}

/** 点击「提交创建」 */
async function onSubmitPdfTree(): Promise<void> {
  if (standaloneParts.value.length === 0 && assemblies.value.length === 0) {
    ElMessage.warning('请先解析上传')
    return
  }
  // 前端兜底：所有 row 的 customer_id 必须有 L2 客户（后端强校验 L2-leaf）
  const missing: string[] = []
  for (const r of standaloneParts.value) {
    if (!r.customer_id) missing.push(`独立零件 ${r.drawing_no || r.uid}`)
  }
  for (const a of assemblies.value) {
    // 子件分厂继承自顶层 → 顶层 customer_id 校验已覆盖
    if (!a.customer_id) missing.push(`装配件 ${a.drawing_no || a.uid}`)
  }
  if (missing.length > 0) {
    ElMessage.warning(
      `以下 ${missing.length} 行未指定分厂（二级客户），请补全后再提交：\n` +
        missing.slice(0, 5).join('\n') +
        (missing.length > 5 ? '\n…' : ''),
    )
    return
  }
  pdfSubmitting.value = true
  try {
    const items: PartBatchTreeItemFE[] = []
    const assembliesPayload: PartBatchTreeAssemblyFE[] = []
    // files 按 allPdfs 顺序对齐（顺序即为后端的 pdf_index）
    const files: PartBatchFilePayload[] = allPdfs.value.map((src) => ({
      data: src.raw,
      filename: src.filename,
      contentType: 'application/pdf',
    }))

    // PR-H 2026-07-28：3D 模型按 threeDModelFiles 顺序对齐（与 items[i].three_d_index 对齐）
    const threeDModelPayloads: PartBatchFilePayload[] = []
    for (const f of threeDModelFiles.value) {
      const raw = f.raw
      if (!raw) continue
      threeDModelPayloads.push({
        data: raw,
        filename: f.name,
        contentType: 'application/octet-stream',  // 后端按扩展名重新判
      })
    }

    // 独立零件：page_index 始终 0（合成 / 原 PDF 都按整体上传）
    for (const r of standaloneParts.value) {
      const pdfIndex = allPdfs.value.findIndex((s) => s.uid === r.pdfSourceUid)
      if (pdfIndex < 0) {
        ElMessage.error(`独立零件 ${r.drawing_no} 引用了已删除的图纸源`)
        return
      }
      items.push({
        pdf_index: pdfIndex,
        page_index: 0,
        assembly_uid: null,
        is_master: false,
        drawing_no: r.drawing_no,
        name: r.name || r.drawing_no,
        applicant_name: r.applicant_name,
        applicant_id: null,
        quantity: r.quantity,
        customer_id: r.customer_id,
        request_date: r.request_date,
        planned_delivery_date: r.planned_delivery_date || r.request_date,
        system_delivery_date: r.system_delivery_date,
        order_no: r.order_no,
        note: r.note,
        is_urgent: r.is_urgent,
        // PR-H 2026-07-28
        unit_price: r.unit_price ?? null,
        total_price: r.total_price ?? null,
        three_d_index: r.three_d_index ?? null,
      })
    }

    // 装配件 + 子件
    for (const a of assemblies.value) {
      const pdfIndex = allPdfs.value.findIndex((s) => s.uid === a.pdfSourceUid)
      if (pdfIndex < 0) {
        ElMessage.error(`装配件 ${a.drawing_no} 引用了已删除的图纸源`)
        return
      }
      assembliesPayload.push({
        uid: a.uid,
        drawing_no: a.drawing_no || null,
        name: a.name || null,
        applicant_name: a.applicant_name,
        applicant_id: null,
        customer_id: a.customer_id,
        request_date: a.request_date,
        planned_delivery_date: a.planned_delivery_date || a.request_date,
        system_delivery_date: a.system_delivery_date,
        order_no: a.order_no,
        note: a.note,
        is_urgent: a.is_urgent,
        quantity: a.quantity,
      })
      for (const c of a.children) {
        items.push({
          pdf_index: pdfIndex,
          page_index: c.page_index,
          assembly_uid: a.uid,
          is_master: a.masterPageIndex === c.page_index,
          drawing_no: c.drawing_no,
          name: c.name || `子件${c.page_index + 1}`,
          // 分厂 / 申请人继承自装配件顶层
          applicant_name: a.applicant_name,
          applicant_id: null,
          quantity: c.quantity,
          customer_id: a.customer_id,
          request_date: c.request_date,
          planned_delivery_date: c.planned_delivery_date || c.request_date,
          system_delivery_date: c.system_delivery_date,
          order_no: c.order_no,
          note: c.note,
          is_urgent: c.is_urgent,
          // PR-H 2026-07-28
          unit_price: c.unit_price ?? null,
          total_price: c.total_price ?? null,
          three_d_index: c.three_d_index ?? null,
        })
      }
    }

    const res = await batchCreatePartsWithPdfs(items, assembliesPayload, files, threeDModelPayloads)
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
    allPdfs.value = []
    standaloneParts.value = []
    assemblies.value = []
    selectedPages.value.clear()
    pdfFiles.value = []
    excelFiles.value = []
    threeDModelFiles.value = []  // PR-H 2026-07-28
    activeTab.value = 'manual'
    router.push('/parts?status=PENDING')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '提交失败')
  } finally {
    pdfSubmitting.value = false
  }
}

// ============ PDF 文件名点击预览（Tab 2） ============
/** 弹窗内显示的 blob URL + 标题 + 起始页。blob URL 由 pdfFiles[i].raw →
 * URL.createObjectURL 生成；关闭弹窗或组件卸载时 revoke，避免内存泄漏。 */
interface PdfPreviewState {
  url: string
  title: string
  page: number
}

const pdfPreviewing = ref<PdfPreviewState | null>(null)
const pdfPreviewVisible = ref(false)

/** 打开预览（通用入口）：传入 pdfSourceUid 和起始页（1-indexed）。 */
function previewAt(pdfSourceUid: string, title: string, page: number): void {
  if (pdfPreviewing.value) {
    try { URL.revokeObjectURL(pdfPreviewing.value.url) } catch { /* ignore */ }
  }
  const src = allPdfs.value.find((s) => s.uid === pdfSourceUid)
  if (!src) {
    ElMessage.warning('PDF 不可用')
    return
  }
  pdfPreviewing.value = {
    url: URL.createObjectURL(src.raw),
    title,
    page,
  }
  pdfPreviewVisible.value = true
}

/** 关闭预览：revoke URL，清状态。el-dialog `:before-close` 会调。 */
function closePdfPreview(): void {
  if (pdfPreviewing.value) {
    try { URL.revokeObjectURL(pdfPreviewing.value.url) } catch { /* ignore */ }
    pdfPreviewing.value = null
  }
  pdfPreviewVisible.value = false
}

onBeforeUnmount(() => {
  closePdfPreview()
  standaloneSortable?.destroy()
  assembliesSortable?.destroy()
})

// ============ 拖拽排序（sortable.js） ============
// PR-H 2026-07-28：拖动 handle 列重排行顺序。
// - 不接受嵌套展开行（child-table 不挂 sortable）；仅顶层独立零件 / 装配件行。
// - watch 行数 + 数据身份变化时重建实例（避免 v-if / 数据长度变化时 tbody 重建导致旧实例悬挂）。
function initStandaloneSortable(): void {
  const root = standaloneTableRef.value?.$el
  if (!root) return
  const tbody = root.querySelector(
    '.el-table__body-wrapper .el-table__body > tbody',
  ) as HTMLElement | null
  if (!tbody) return
  standaloneSortable?.destroy()
  standaloneSortable = Sortable.create(tbody, {
    handle: '.drag-handle',
    draggable: 'tr',
    animation: 150,
    ghostClass: 'sortable-ghost',
    onEnd(evt: { oldIndex?: number; newIndex?: number }) {
      const { oldIndex, newIndex } = evt
      if (oldIndex == null || newIndex == null || oldIndex === newIndex) return
      const next = standaloneParts.value.slice()
      const [moved] = next.splice(oldIndex, 1)
      if (moved) next.splice(newIndex, 0, moved)
      standaloneParts.value = next
    },
  })
}
function initAssembliesSortable(): void {
  const root = assembliesTableRef.value?.$el
  if (!root) return
  const tbody = root.querySelector(
    '.el-table__body-wrapper .el-table__body > tbody',
  ) as HTMLElement | null
  if (!tbody) return
  assembliesSortable?.destroy()
  assembliesSortable = Sortable.create(tbody, {
    handle: '.drag-handle',
    draggable: 'tr',
    animation: 150,
    ghostClass: 'sortable-ghost',
    onEnd(evt: { oldIndex?: number; newIndex?: number }) {
      const { oldIndex, newIndex } = evt
      if (oldIndex == null || newIndex == null || oldIndex === newIndex) return
      const next = assemblies.value.slice()
      const [moved] = next.splice(oldIndex, 1)
      if (moved) next.splice(newIndex, 0, moved)
      assemblies.value = next
    },
  })
}
watch(
  () => standaloneParts.value.length,
  () => nextTick(initStandaloneSortable),
)
watch(
  () => assemblies.value.length,
  () => nextTick(initAssembliesSortable),
)

// ============ 源文件区：勾选 + 归组 + 删除 ============

/** 生成页 UID（用于 selectedPages Set）。 */
function pageUid(pdfUid: string, pageIndex: number): string {
  return `${pdfUid}:${pageIndex}`
}

/** 解析 Set 里的 key → pdfUid / pageIndex。 */
function parsePageUid(key: string): { pdfUid: string; pageIndex: number } {
  const last = key.lastIndexOf(':')
  return { pdfUid: key.slice(0, last), pageIndex: Number(key.slice(last + 1)) }
}

function togglePageSelection(pdfUid: string, pageIndex: number, on: boolean): void {
  const next = new Set(selectedPages.value)
  const k = pageUid(pdfUid, pageIndex)
  if (on) next.add(k)
  else next.delete(k)
  selectedPages.value = next
}

/** el-table type=selection 回调：把选中的 SourceTreeRow 扁平化成页 UID 集合。
 *  顶层被选中 → 等价于「该 PDF 全部页」。 */
function onSourceSelectionChange(rows: SourceTreeRow[]): void {
  const next = new Set<string>()
  for (const r of rows) {
    if (r.pageIndex !== null) {
      next.add(pageUid(r.pdfSourceUid, r.pageIndex))
    } else {
      const src = allPdfs.value.find((s) => s.uid === r.pdfSourceUid)
      if (src) for (let p = 0; p < src.totalPages; p++) next.add(pageUid(r.pdfSourceUid, p))
    }
  }
  selectedPages.value = next
}

/** 源文件区点击文件名预览。顶层 → 第 1 页；子页 → 对应页。 */
function previewSourceRow(row: SourceTreeRow): void {
  const page = row.pageIndex === null ? 1 : row.pageIndex + 1
  previewAt(row.pdfSourceUid, `${row.filename} 预览`, page)
}

function togglePdfSelection(pdfUid: string, on: boolean): void {
  const src = allPdfs.value.find((s) => s.uid === pdfUid)
  if (!src) return
  const next = new Set(selectedPages.value)
  for (let p = 0; p < src.totalPages; p++) {
    const k = pageUid(pdfUid, p)
    if (on) next.add(k)
    else next.delete(k)
  }
  selectedPages.value = next
}

function isPdfFullySelected(pdfUid: string): boolean {
  const src = allPdfs.value.find((s) => s.uid === pdfUid)
  if (!src || src.totalPages === 0) return false
  for (let p = 0; p < src.totalPages; p++) {
    if (!selectedPages.value.has(pageUid(pdfUid, p))) return false
  }
  return true
}

function isPdfPartiallySelected(pdfUid: string): boolean {
  const src = allPdfs.value.find((s) => s.uid === pdfUid)
  if (!src) return false
  let any = false
  for (let p = 0; p < src.totalPages; p++) {
    if (selectedPages.value.has(pageUid(pdfUid, p))) {
      any = true
      break
    }
  }
  return any && !isPdfFullySelected(pdfUid)
}

// el-table 类型来自 element-plus 类型导出，运行时为函数组件；用宽松类型包住
const sourceTableRef = ref<{ clearSelection: () => void } | null>(null)

function clearSelection(): void {
  sourceTableRef.value?.clearSelection()
  selectedPages.value = new Set()
}

/** 用 pdf-lib 合并 PDF 的指定页（0-based pageIndices）→ 新 PDF Blob。 */
async function mergePages(raw: Blob, pageIndices: number[]): Promise<Blob> {
  const { PDFDocument } = await import('pdf-lib')
  const src = await PDFDocument.load(await raw.arrayBuffer())
  const out = await PDFDocument.create()
  const copied = await out.copyPages(src, pageIndices)
  copied.forEach((p) => out.addPage(p))
  const bytes = await out.save()
  // pdf-lib save() 返回 Uint8Array<ArrayBufferLike>，TS 5+ 要求 BlobPart 严格是
  // ArrayBuffer 类型；slice() 拷贝出独立 ArrayBuffer，避开 SharedArrayBuffer 误判。
  return new Blob([bytes.slice().buffer as ArrayBuffer], { type: 'application/pdf' })
}

/** 把选中页按 pdfUid 分组。 */
function selectedByPdf(): Map<string, number[]> {
  const m = new Map<string, number[]>()
  for (const k of selectedPages.value) {
    const { pdfUid, pageIndex } = parsePageUid(k)
    const arr = m.get(pdfUid) ?? []
    arr.push(pageIndex)
    m.set(pdfUid, arr)
  }
  for (const arr of m.values()) arr.sort((a, b) => a - b)
  return m
}

/** 合并选中页 → 一个独立零件（同一 PDF）。 */
async function mergeSelectedAsPart(): Promise<void> {
  const byPdf = selectedByPdf()
  if (byPdf.size === 0) {
    ElMessage.warning('请先勾选页')
    return
  }
  if (byPdf.size > 1) {
    ElMessage.warning('合并为零件的页必须来自同一 PDF')
    return
  }
  const [pdfUid, pageIndices] = [...byPdf.entries()][0]
  const src = allPdfs.value.find((s) => s.uid === pdfUid)
  if (!src) return

  if (pageIndices.length === src.totalPages) {
    // 全部页 → 直接复用原 PDF，无合成
    standaloneParts.value.push(makeStandaloneRow({
      pdfSourceUid: pdfUid,
      drawing_no: parseDrawingFilename(src.filename).drawingNo || '',
      name: parseDrawingFilename(src.filename).partName || '',
    }))
  } else {
    // 部分页 → pdf-lib 合成
    const merged = await mergePages(src.raw, pageIndices)
    const newUid = `syn-${makeUid()}`
    allPdfs.value.push({
      uid: newUid,
      raw: merged,
      filename: `${stripExt(src.filename)}_p${pageIndices.map((i) => i + 1).join('+')}.pdf`,
      totalPages: pageIndices.length,
      synthesized: true,
      synthesizedFrom: [{ pdfUid, pageIndices }],
      originPdfUid: pdfUid,
    })
    standaloneParts.value.push(makeStandaloneRow({
      pdfSourceUid: newUid,
      pageCount: pageIndices.length,
      mergedFrom: pageIndices.map((i) => ({ pdfUid, pageIndex: i })),
      drawing_no: parseDrawingFilename(src.filename).drawingNo || '',
      name: parseDrawingFilename(src.filename).partName || '',
    }))
  }
  // 移除原 standalone 行（如果存在）
  clearSelection()
  ElMessage.success(`已合并 ${pageIndices.length} 页 → 独立零件`)
}

/** 合并选中页 → 一个装配件（同一 PDF，至少 2 页）。 */
async function mergeSelectedAsAssembly(): Promise<void> {
  const byPdf = selectedByPdf()
  if (byPdf.size === 0) {
    ElMessage.warning('请先勾选页')
    return
  }
  if (byPdf.size > 1) {
    ElMessage.warning('合并为装配件的页必须来自同一 PDF')
    return
  }
  const [pdfUid, pageIndices] = [...byPdf.entries()][0]
  if (pageIndices.length < 2) {
    ElMessage.warning('合并为装配件至少需要 2 页')
    return
  }
  const src = allPdfs.value.find((s) => s.uid === pdfUid)
  if (!src) return
  const parsed = parseDrawingFilename(src.filename)
  const asmUid = `asm-${makeUid()}`
  const children: AssemblyChildRow[] = pageIndices.map((pi) => {
    const drawingNo = parsed.drawingNo
      ? (pi === 0 ? parsed.drawingNo : `${parsed.drawingNo}-${String(pi + 1).padStart(2, '0')}`)
      : ''
    const name = parsed.partName
      ? (pi === 0 ? parsed.partName : `${parsed.partName}-${pi + 1}`)
      : ''
    return makeAssemblyChild({
      pdfSourceUid: pdfUid,
      pageIndex: pi,
      drawing_no: drawingNo,
      name: name,
    })
  })
  assemblies.value.push({
    uid: asmUid,
    pdfSourceUid: pdfUid,
    drawing_no: parsed.drawingNo || '',
    name: parsed.partName || '',
    applicant_name: '',
    customer_id: '',
    customer_name: '',
    request_date: pdfForm.requestDate,
    planned_delivery_date: '',
    system_delivery_date: null,
    order_no: null,
    note: null,
    is_urgent: false,
    masterPageIndex: null,
    quantity: 1,
    children,
  })
  clearSelection()
  ElMessage.success(`已合并 ${pageIndices.length} 页 → 装配件`)
}

/** 拆分：把合成后的独立零件拆回 N 个单页行。 */
function splitStandalonePart(row: StandalonePartRow): void {
  if (!row.mergedFrom || row.mergedFrom.length === 0) return
  // 找到原始 PDF
  const originUid = row.mergedFrom[0].pdfUid
  const src = allPdfs.value.find((s) => s.uid === originUid)
  if (!src) {
    ElMessage.error('原 PDF 已不存在，无法拆分')
    return
  }
  // 按 pageIndex 排序，逐页创建独立行
  const sorted = [...row.mergedFrom].sort((a, b) => a.pageIndex - b.pageIndex)
  for (const m of sorted) {
    const parsed = parseDrawingFilename(src.filename)
    const drawingNo = parsed.drawingNo
      ? (m.pageIndex === 0 ? parsed.drawingNo : `${parsed.drawingNo}-${String(m.pageIndex + 1).padStart(2, '0')}`)
      : ''
    const name = parsed.partName
      ? (m.pageIndex === 0 ? parsed.partName : `${parsed.partName}-${m.pageIndex + 1}`)
      : ''
    standaloneParts.value.push(makeStandaloneRow({
      pdfSourceUid: originUid,
      drawing_no: drawingNo,
      name: name,
    }))
  }
  // 删除合成 PDF（若已合并成 part，且 part 是唯一引用）
  const synthUid = row.pdfSourceUid
  if (synthUid.startsWith('syn-')) {
    allPdfs.value = allPdfs.value.filter((s) => s.uid !== synthUid)
  }
  standaloneParts.value = standaloneParts.value.filter((r) => r.uid !== row.uid)
  ElMessage.success(`已拆回 ${sorted.length} 页`)
}

/** 删除一个原 PDF：连带删除合成 PDF + 引用它的所有 part / assembly。 */
function removePdf(pdfUid: string): void {
  if (!allPdfs.value.some((s) => s.uid === pdfUid)) return
  // 1. 找出要删除的源：原 PDF + 它的所有合成派生
  const removeUids = new Set<string>([pdfUid])
  allPdfs.value.filter((s) => s.originPdfUid === pdfUid).forEach((s) => removeUids.add(s.uid))
  // 2. 从 allPdfs 移除
  allPdfs.value = allPdfs.value.filter((s) => !removeUids.has(s.uid))
  // 3. 清理 standaloneParts
  standaloneParts.value = standaloneParts.value.filter((r) => !removeUids.has(r.pdfSourceUid))
  // 4. 清理 assemblies
  assemblies.value = assemblies.value.filter((a) => !removeUids.has(a.pdfSourceUid))
  // 5. 清理 selection
  const next = new Set<string>()
  for (const k of selectedPages.value) {
    if (!removeUids.has(parsePageUid(k).pdfUid)) next.add(k)
  }
  selectedPages.value = next
  // 6. 从 el-upload pdfFiles 移除原 UploadFile（pdfUid = "pdf-<uploadFileUid>"）
  const uploadUid = pdfUid.startsWith('pdf-') ? pdfUid.slice(4) : null
  if (uploadUid) {
    pdfFiles.value = pdfFiles.value.filter((f) => String(f.uid) !== uploadUid)
  }
}

function removeStandalonePart(uid: string): void {
  const row = standaloneParts.value.find((r) => r.uid === uid)
  if (!row) return
  // 若是合成行，删除合成 PDF
  if (row.pdfSourceUid.startsWith('syn-')) {
    allPdfs.value = allPdfs.value.filter((s) => s.uid !== row.pdfSourceUid)
  }
  standaloneParts.value = standaloneParts.value.filter((r) => r.uid !== uid)
}

function removeAssembly(uid: string): void {
  assemblies.value = assemblies.value.filter((a) => a.uid !== uid)
}

/** 展示用：把 PdfSource uid 翻译成可读文件名 + 页数。 */
function pdfSourceLabel(uid: string): string {
  const src = allPdfs.value.find((s) => s.uid === uid)
  if (!src) return '(已删除)'
  return src.synthesized
    ? `${src.filename}（合成 ${src.totalPages} 页）`
    : `${src.filename}（${src.totalPages} 页）`
}

/** 预览独立零件 / 装配件图纸。 */
function previewStandalonePart(row: StandalonePartRow): void {
  previewAt(row.pdfSourceUid, `${row.drawing_no || row.name} 预览`, 1)
}
function previewPdfSource(src: PdfSource): void {
  previewAt(src.uid, `${src.filename} 预览`, 1)
}
function previewPdfSourceByUid(uid: string): void {
  previewAt(uid, `${pdfSourceLabel(uid)} 预览`, 1)
}

function stripExt(name: string): string {
  return name.replace(/\.pdf$/i, '')
}

/** 行（standalone / child）分厂下拉 onChange：同步 customer_name。 */
function onL2Change(row: { customer_id: string; customer_name?: string }, v: string): void {
  row.customer_id = v
  const c = customers.value.find((x) => x.id === v)
  row.customer_name = c?.name ?? ''
}

/** 装配件顶层计划交期改值 → 同步所有子件。简单覆盖语义：child 单独改后
 *  下次顶层改会被覆盖（如需「记住 child 单独覆盖」需加 flag 字段，本轮不做）。 */
function onAsmPlannedChange(asmRow: AssemblyRow, v: string): void {
  asmRow.planned_delivery_date = v
  for (const c of asmRow.children) c.planned_delivery_date = v
}

// ============ 手动新增零件 / 装配件 ============

const manualPartDialogVisible = ref(false)
const manualPartForm = reactive<{
  drawing_no: string
  name: string
  file: File | null
}>({ drawing_no: '', name: '', file: null })
const manualPartFileList = computed<UploadFile[]>(() =>
  manualPartForm.file
    ? [{ uid: -1, name: manualPartForm.file.name, status: 'success', raw: manualPartForm.file as UploadFile['raw'] }]
    : [],
)
const manualPartFormValid = computed(() =>
  manualPartForm.drawing_no.trim().length > 0 && manualPartForm.file !== null,
)

function addManualPart(): void {
  manualPartForm.drawing_no = ''
  manualPartForm.name = ''
  manualPartForm.file = null
  manualPartDialogVisible.value = true
}

function onManualPartFileChange(file: UploadFile): void {
  manualPartForm.file = (file.raw as File | undefined) ?? null
}

function onManualPartFileRemove(): void {
  manualPartForm.file = null
}

async function confirmManualPart(): Promise<void> {
  if (!manualPartFormValid.value) return
  const f = manualPartForm.file!
  const pdfUid = `manual-${makeUid()}`
  allPdfs.value.push({
    uid: pdfUid,
    raw: f,
    filename: f.name,
    totalPages: 1,
    synthesized: false,
  })
  standaloneParts.value.push(makeStandaloneRow({
    pdfSourceUid: pdfUid,
    drawing_no: manualPartForm.drawing_no.trim(),
    name: manualPartForm.name.trim(),
  }))
  manualPartDialogVisible.value = false
  ElMessage.success('已新增零件')
}

const manualAsmDialogVisible = ref(false)
const manualAsmForm = reactive<{
  drawing_no: string
  name: string
  file: File | null
}>({ drawing_no: '', name: '', file: null })
const manualAsmFileList = computed<UploadFile[]>(() =>
  manualAsmForm.file
    ? [{ uid: -2, name: manualAsmForm.file.name, status: 'success', raw: manualAsmForm.file as UploadFile['raw'] }]
    : [],
)
const manualAsmFormValid = computed(() =>
  manualAsmForm.drawing_no.trim().length > 0 && manualAsmForm.file !== null,
)

function addManualAssembly(): void {
  manualAsmForm.drawing_no = ''
  manualAsmForm.name = ''
  manualAsmForm.file = null
  manualAsmDialogVisible.value = true
}

function onManualAsmFileChange(file: UploadFile): void {
  manualAsmForm.file = (file.raw as File | undefined) ?? null
}

function onManualAsmFileRemove(): void {
  manualAsmForm.file = null
}

async function confirmManualAssembly(): Promise<void> {
  if (!manualAsmFormValid.value) return
  const f = manualAsmForm.file!
  const totalPages = await countPdfPages(f)
  const pdfUid = `manual-asm-${makeUid()}`
  allPdfs.value.push({
    uid: pdfUid,
    raw: f,
    filename: f.name,
    totalPages,
    synthesized: false,
  })
  const asmUid = `asm-${makeUid()}`
  const children: AssemblyChildRow[] = []
  for (let p = 0; p < totalPages; p++) {
    children.push(makeAssemblyChild({
      pdfSourceUid: pdfUid,
      pageIndex: p,
      drawing_no: totalPages === 1
        ? manualAsmForm.drawing_no.trim()
        : `${manualAsmForm.drawing_no.trim()}-${String(p + 1).padStart(2, '0')}`,
      name: totalPages === 1
        ? manualAsmForm.name.trim() || manualAsmForm.drawing_no.trim()
        : `${manualAsmForm.name.trim() || manualAsmForm.drawing_no.trim()}-${p + 1}`,
    }))
  }
  assemblies.value.push({
    uid: asmUid,
    pdfSourceUid: pdfUid,
    drawing_no: manualAsmForm.drawing_no.trim(),
    name: manualAsmForm.name.trim(),
    applicant_name: '',
    customer_id: '',
    customer_name: '',
    request_date: pdfForm.requestDate,
    planned_delivery_date: '',
    system_delivery_date: null,
    order_no: null,
    note: null,
    is_urgent: false,
    masterPageIndex: totalPages > 1 ? 0 : null,
    quantity: 1,
    children,
  })
  manualAsmDialogVisible.value = false
  ElMessage.success(`已新增装配件（共 ${totalPages} 子件）`)
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

.pdf-footer-actions {
  margin-top: 16px;
  display: flex;
  justify-content: center;
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
  background-color: #fde2e2 !important;
}
:deep(.el-table__row.row-urgent:hover > td.el-table__cell) {
  background-color: #fbcaca !important;
}

/* PR-H 2026-07-28：sortable.js 拖拽视觉 */
.drag-handle {
  cursor: grab;
  color: var(--text-secondary);
  font-size: 16px;
}
.drag-handle:hover {
  color: var(--el-color-primary);
}
.drag-handle:active {
  cursor: grabbing;
}
:deep(.sortable-ghost) {
  opacity: 0.4;
  background-color: #f0f9ff !important;
}
:deep(.sortable-chosen) {
  background-color: #ecf5ff !important;
}
</style>