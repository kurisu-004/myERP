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
          <el-descriptions :column="descCol" border>
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
              <el-tag
                v-if="part.has_been_repaired"
                type="warning"
                size="small"
                effect="dark"
                style="margin-left: 6px"
              >
                返修
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
              <el-date-picker v-model="form.planned_delivery_date" type="date" value-format="YYYY-MM-DD" size="small" style="width:100%" />
            </el-descriptions-item>
            <el-descriptions-item label="实际送货">
              <el-date-picker v-model="form.actual_delivery_date" type="date" value-format="YYYY-MM-DD" size="small" style="width:100%" />
            </el-descriptions-item>
            <el-descriptions-item label="单据 ID">#{{ part.id }}</el-descriptions-item>

            <!-- 送货单字段（PR-F 2026-07-17） -->
            <el-descriptions-item label="订单号">
              <el-input v-model="form.order_no" size="small" placeholder="如 6200037950" />
            </el-descriptions-item>
            <el-descriptions-item label="系统交期">
              <el-date-picker v-model="form.system_delivery_date" type="date" value-format="YYYY-MM-DD" size="small" style="width:100%" />
            </el-descriptions-item>
            <el-descriptions-item label="备注">
              <el-input v-model="form.note" size="small" placeholder="文员手填" />
            </el-descriptions-item>
          </el-descriptions>

          <div class="edit-actions">
            <el-button @click="onCancelEdit">取消</el-button>
            <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
          </div>
        </template>

        <template v-else>
          <el-descriptions :column="descCol" border>
            <el-descriptions-item label="序列号">
              <span v-if="part.serial_no" class="mono">{{ part.serial_no }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="图号">{{ part.drawing_no }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTagType(part.status)" effect="plain" size="small">
                {{ statusLabel(part.status) }}
              </el-tag>
              <el-tag
                v-if="part.has_been_repaired"
                type="warning"
                size="small"
                effect="dark"
                style="margin-left: 6px"
              >
                返修
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

            <!-- 送货单字段（PR-F 2026-07-17） -->
            <el-descriptions-item label="订单号">
              <span v-if="part.order_no">{{ part.order_no }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="系统交期">
              <span v-if="part.system_delivery_date">{{ part.system_delivery_date }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="备注">
              <span v-if="part.note">{{ part.note }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
          </el-descriptions>

          <div class="edit-actions">
            <el-button v-if="canEditPart" type="primary" plain @click="onStartEdit">编辑</el-button>
          </div>
        </template>
      </template>
    </el-card>

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
                <el-tag v-if="evt.batch_no" size="small" effect="plain" type="info">
                  批次{{ evt.batch_no }}
                </el-tag>
                <span v-if="evt.quantity != null" class="muted">× {{ evt.quantity }}</span>
                <span v-if="evt.worker_name" class="worker-name">
                  <el-icon><User /></el-icon>
                  {{ evt.worker_name }}
                </span>
                <!-- 2026-07-17：历史记录中显示操作者姓名（优先 operator_name，username 仅作 fallback） -->
                <span v-if="evt.operator_name || evt.operator_username" class="operator-name">
                  <el-icon><Setting /></el-icon>
                  {{ evt.operator_name || evt.operator_username }}
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

    <!-- 所属送货单（PR-G 2026-07-22）：仅当 t_part.delivery_note_id 非空 -->
    <el-card
      v-if="part && part.delivery_note_id != null"
      shadow="never"
      class="delivery-note-card"
    >
      <template #header>
        <div class="card-header">
          <span class="card-title">
            <el-icon><Document /></el-icon>
            <span>所属送货单</span>
            <el-tag
              v-if="part.delivery_note_status"
              :type="DELIVERY_NOTE_STATUS_TAG[part.delivery_note_status as DeliveryNoteStatus] || 'info'"
              size="small"
              effect="plain"
            >
              {{ DELIVERY_NOTE_STATUS_LABEL[part.delivery_note_status as DeliveryNoteStatus] }}
            </el-tag>
          </span>
          <el-button
            link
            type="primary"
            size="small"
            @click="$router.push(`/delivery-notes/${part.delivery_note_id}`)"
          >
            查看送货单详情
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="单号">
          {{ part.delivery_note_no ?? '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          {{ part.delivery_note_status ?? '—' }}
        </el-descriptions-item>
      </el-descriptions>
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
      <el-descriptions v-if="assemblyDetail" :column="descCol" border>
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

    <!-- 图纸 -->
    <FileListCard
      :files="drawings"
      owner-type="part"
      :owner-id="partId"
      kind="DRAWING"
      :show-upload="canManageDrawings"
      :show-delete="canManageDrawings"
      :show-print="!isInspector"
      :api-upload="uploadPartDrawing"
      @refresh="fetchDrawings"
    />

    <!-- 3D 模型 -->
    <FileListCard
      :files="models3d"
      owner-type="part"
      :owner-id="partId"
      kind="3D_MODEL"
      :show-upload="canManage3DModels"
      :show-delete="canManage3DModels"
      :api-upload="uploadPart3DModel"
      @refresh="fetch3DModels"
    />

    <!-- CAD 源文件（DWG/DXF，2026-07-14 新增）-->
    <FileListCard
      :files="cadFiles"
      owner-type="part"
      :owner-id="partId"
      kind="CAD_2D"
      :show-upload="canManageDrawings"
      :show-delete="canManageDrawings"
      :api-upload="uploadPartCadFile"
      @refresh="fetchCadFiles"
    />

    <!-- CNC 文件（G 代码 + 设定单）合并到 el-tabs -->
    <el-card shadow="never" class="cnc-card" v-loading="cncLoading">
      <template #header>
        <div class="card-header">
          <span class="card-title">
            <el-icon><Cpu /></el-icon>
            <span>CNC 文件</span>
          </span>
        </div>
      </template>

      <!-- 配对列表：G 代码（左列）+ 设定单（右列），两两对应 -->
      <div v-if="cncSetupGroups.length > 0" class="cnc-group-list">
        <div class="cnc-group-header">
          <span class="cnc-group-header-col">G 代码</span>
          <span class="cnc-group-header-col">CNC 设定单</span>
        </div>
        <div
          v-for="(group, gIdx) in cncSetupGroups"
          :key="group.setup?.id ?? `__unpaired_${gIdx}`"
          class="cnc-group-row"
        >
          <!-- 左列：N 个 G 代码纵向堆叠 -->
          <div class="cnc-gcode-col">
            <template v-if="group.gcodes.length > 0">
              <div v-for="g in group.gcodes" :key="g.id" class="cnc-sub-row">
                <el-tag size="small" type="info">{{ g.file_type }}</el-tag>
                <span class="cnc-name">{{ g.original_filename }}</span>
                <span class="cnc-size">{{ formatBytes(g.file_size) }}</span>
                <span class="cnc-time">{{ formatDateTime(g.created_at) }}</span>
                <el-button link type="primary" size="small" @click="onDownloadCnc(g)">下载</el-button>
                <el-button
                  v-if="canManageCncFiles"
                  link type="danger" size="small"
                  @click="onDeleteCnc(g.id)"
                >删除</el-button>
              </div>
            </template>
            <span v-else class="cnc-empty">—</span>
          </div>
          <!-- 右列：每组 1 个或 0 个设定单 -->
          <div class="cnc-setup-col">
            <template v-if="group.setup">
              <div class="cnc-sub-row">
                <el-tag size="small" type="success">PDF</el-tag>
                <span class="cnc-name">{{ group.setup.original_filename }}</span>
                <span class="cnc-size">{{ formatBytes(group.setup.file_size) }}</span>
                <span class="cnc-time">{{ formatDateTime(group.setup.created_at) }}</span>
                <el-button link type="primary" size="small" @click="onDownloadCnc(group.setup)">下载</el-button>
                <el-button
                  v-if="canManageSetupSheet"
                  link type="danger" size="small"
                  @click="onDeleteCnc(group.setup.id)"
                >删除</el-button>
              </div>
            </template>
            <span v-else class="cnc-empty">无设定单</span>
          </div>
        </div>
      </div>
      <el-empty v-else description="暂无 CNC 程序" :image-size="80" />

      <div v-if="canManageCncFiles || canManageSetupSheet" class="cnc-upload">
        <!-- 配对上传按钮 -->
        <el-button
          v-if="canManageCncFiles && canManageSetupSheet"
          type="primary"
          @click="pairUploadVisible = true"
        >
          <el-icon><Upload /></el-icon><span>配对上载 (G代码 + 设定单)</span>
        </el-button>
        <el-button
          v-if="canManageCncFiles && part?.status === 'PROGRAMMING'"
          type="success"
          :loading="releaseSubmitting"
          @click="onOpenReleaseDialog"
        >
          下发到 CNC 货架
        </el-button>
      </div>

      <!-- 配对上传对话框 -->
      <el-dialog v-model="pairUploadVisible" title="配对上载 G 代码 + CNC 设定单" width="500px" @close="onPairUploadClose">
        <el-form label-width="100px">
          <el-form-item label="G 代码文件">
            <el-upload
              :auto-upload="false"
              :show-file-list="true"
              multiple
              accept=".nc,.tap,.cnc,.mpf,.ngc"
              :file-list="pairGcodeFiles"
              :on-change="onPairGcodeChange"
              :on-remove="onPairGcodeRemove"
            >
              <el-button plain>选择 G 代码（可多个）</el-button>
            </el-upload>
          </el-form-item>
          <el-form-item label="CNC 设定单">
            <el-upload
              :auto-upload="false"
              :show-file-list="true"
              :limit="1"
              accept=".pdf"
              :on-change="onPairSetupChange"
              :on-remove="() => { pairSetupFile = null }"
            >
              <el-button plain>选择设定单 (.pdf)</el-button>
            </el-upload>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="pairUploadVisible = false" :disabled="pairUploading">取消</el-button>
          <el-button
            type="primary"
            :loading="pairUploading"
            :disabled="pairGcodeFiles.length === 0 || !pairSetupFile"
            @click="onPairUploadConfirm"
          >确认上传</el-button>
        </template>
      </el-dialog>
    </el-card>

    <!-- 外协报价（2026-07-16 新增；只读展示 + 状态+角色门控的新建入口） -->
    <el-card
      v-if="canViewQuotes"
      shadow="never"
      class="quote-card"
      v-loading="quotesLoading"
    >
      <template #header>
        <div class="card-header">
          <span class="card-title">
            <el-icon><Document /></el-icon>
            <span>外协报价</span>
          </span>
          <el-button
            v-if="canCreateQuote"
            link
            type="primary"
            size="small"
            @click="openQuoteCreateDialog"
          >
            <el-icon><Plus /></el-icon>
            <span>新建外协报价</span>
          </el-button>
        </div>
      </template>
      <el-table
        v-if="quotes.length > 0"
        :data="quotes"
        size="small"
        border
        stripe
      >
        <el-table-column label="状态" min-width="110" align="center">
          <template #default="{ row }">
            <el-tag
              :type="((OUTSOURCE_QUOTE_STATUS_TAG[(row as OutsourceQuote).status] || 'info') as 'info' | 'success' | 'warning' | 'danger')"
              size="small"
              effect="plain"
            >
              {{ OUTSOURCE_QUOTE_STATUS_LABEL[(row as OutsourceQuote).status] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="outsource_company_name"
          label="外协公司"
          min-width="140"
          show-overflow-tooltip align="center"/>
        <el-table-column prop="process_code" label="工序" min-width="100" align="center"/>
        <el-table-column label="单价(元)" min-width="100" align="right">
          <template #default="{ row }">{{ (row as OutsourceQuote).price }}</template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="160" align="center">
          <template #default="{ row }">
            <span class="muted">{{ formatDateTime((row as OutsourceQuote).created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="120" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              @click="onViewQuoteDetail((row as OutsourceQuote))"
            >详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无外协报价" />
    </el-card>

    <!-- 批次监控（2026-07-29 批次化） -->
    <el-card shadow="never" class="batch-card" v-loading="batchesLoading">
      <template #header>
        <div class="card-header">
          <span class="card-title">批次监控</span>
          <span class="event-count">
            共 {{ batches.length }} 批 / {{ batchTotalQty }} 件
          </span>
        </div>
      </template>
      <el-table
        v-if="batches.length > 0"
        :data="batches"
        size="small"
        border
        stripe
      >
        <el-table-column label="批次" min-width="110" align="center">
          <template #default="{ row }">
            <span class="batch-label">{{ (row as PartBatch).batch_label }}</span>
          </template>
        </el-table-column>
        <el-table-column label="数量" width="80" align="right">
          <template #default="{ row }">{{ (row as PartBatch).quantity }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="110" align="center">
          <template #default="{ row }">
            <el-tag
              :type="statusTagType((row as PartBatch).status as OrderStatus)"
              size="small"
              effect="plain"
            >
              {{ statusLabelOf((row as PartBatch).status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="所在位置"
          min-width="130"
          align="center"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            {{ (row as PartBatch).current_holder_display || '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="下一工序"
          min-width="100"
          align="center"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            {{ (row as PartBatch).next_process_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="送货单"
          min-width="150"
          align="center"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            {{ (row as PartBatch).delivery_note_no || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="150" align="center">
          <template #default="{ row }">
            <span class="muted">{{ formatDateTime((row as PartBatch).created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          v-if="canManageBatches"
          label="操作"
          width="130"
          align="center"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              v-if="!isTerminalBatch(row as PartBatch) && (row as PartBatch).quantity > 1"
              link
              type="primary"
              size="small"
              @click="openSplitDialog(row as PartBatch)"
            >拆分</el-button>
            <el-button
              v-if="!isTerminalBatch(row as PartBatch)"
              link
              type="danger"
              size="small"
              @click="onCancelBatch(row as PartBatch)"
            >取消</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无批次" />
    </el-card>

    <!-- 拆分批次对话框 -->
    <el-dialog
      v-model="splitDialogVisible"
      title="拆分批次"
      :width="confirmDlg.width.value"
      :fullscreen="confirmDlg.fullscreen.value"
      @closed="onSplitDialogClosed"
    >
      <div v-if="splitSource" class="split-dialog-body">
        <p>
          源批次 <b>{{ splitSource.batch_label }}</b>
          （当前 {{ splitSource.quantity }} 件，
          {{ statusLabelOf(splitSource.status) }}）
        </p>
        <el-form label-width="90px">
          <el-form-item label="拆出数量" required>
            <el-input-number
              v-model="splitQuantity"
              :min="1"
              :max="splitSource.quantity - 1"
              :precision="0"
              style="width: 160px"
            />
          </el-form-item>
        </el-form>
        <p class="muted">
          拆出后：源批次剩 {{ splitSource.quantity - (splitQuantity ?? 0) }} 件，
          新批次 {{ splitQuantity ?? 0 }} 件（继承当前状态/位置）。
        </p>
      </div>
      <template #footer>
        <el-button @click="splitDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="splitSubmitting"
          :disabled="!splitQuantity || !splitSource || splitQuantity >= splitSource.quantity"
          @click="onSplitConfirm"
        >确认拆分</el-button>
      </template>
    </el-dialog>

    <!-- 底部操作：取消订单 / 删除（按角色门控） -->
    <el-card shadow="never" class="bottom-actions" v-if="part">
      <div class="action-row">
        <!-- 品检相关：仅 INSPECTION 状态可见 -->
        <template v-if="canInspect && part.status === 'INSPECTION'">
          <el-button
            type="success"
            :loading="passSubmitting"
            @click="onPassInspection"
          >品检通过</el-button>
          <el-button
            type="warning"
            @click="openFailInspectionDialog"
          >指定工序</el-button>
        </template>
        <!-- 外协回收：OUTSOURCE 状态可见（MANAGER + CLERK） -->
        <el-button
          v-if="canReceiveFromOutsource && part.status === 'OUTSOURCE'"
          type="success"
          @click="openReceiveOutsourceDialog"
        >外协回收</el-button>
        <el-button
          v-if="canCancelPart && part.status !== 'CANCELLED' && part.status !== 'COMPLETED'"
          type="warning"
          @click="onCancelOrder"
        >取消订单</el-button>
        <el-button
          v-if="canDeletePart"
          type="danger"
          @click="onDeletePart"
        >删除</el-button>
      </div>
    </el-card>

    <!-- 外协回收 对话框（2026-07-15 新增） -->
    <el-dialog
      v-model="receiveOutsourceDialogVisible"
      title="外协回收 — 选择目标生产货架与下一道工序"
      :width="receiveOutsourceDlg.width.value"
      :top="receiveOutsourceDlg.top.value"
      :fullscreen="receiveOutsourceDlg.fullscreen.value"
      :close-on-click-modal="false"
      @closed="onReceiveOutsourceDialogClosed"
    >
      <el-form label-width="110px">
        <el-form-item label="目标生产货架" required>
          <el-radio-group
            v-model="receiveShelfId"
            style="display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto;"
          >
            <el-radio
              v-for="s in receiveFilteredShelves"
              :key="s.id"
              :value="String(s.id)"
              :disabled="!s.is_active"
            >
              {{ s.code }} — {{ s.name }}
              <span v-if="!s.is_active" class="muted">（已停用）</span>
            </el-radio>
            <span v-if="receiveFilteredShelves.length === 0" class="muted">没有可用生产货架</span>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="下一道工序" required>
          <el-radio-group
            v-model="receiveProcessId"
            style="display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto;"
          >
            <el-radio
              v-for="p in receiveFilteredProcesses"
              :key="p.id"
              :value="String(p.id)"
            >
              {{ p.code }} — {{ p.name }}
            </el-radio>
            <span v-if="receiveFilteredProcesses.length === 0" class="muted">
              没有 INHOUSE 工序
            </span>
          </el-radio-group>
        </el-form-item>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="回收后零件回到 IN_PROCESS / ON_SHELF 状态，可继续车间加工。"
        />
      </el-form>
      <template #footer>
        <el-button @click="receiveOutsourceDialogVisible = false">取消</el-button>
        <el-button
          type="success"
          :loading="receiveSubmitting"
          :disabled="!receiveShelfId || !receiveProcessId"
          @click="onReceiveConfirm"
        >确认回收</el-button>
      </template>
    </el-dialog>

    <!-- 指定工序对话框（PartDetail 用）—— 2026-07-21 改：先选下一道工序，再选目标生产货架；可选品检备注 -->
    <el-dialog
      v-model="failInspDialogVisible"
      title="指定工序 — 选择下一道工序 + 目标生产货架"
      :width="failInspDlg.width.value"
      :top="failInspDlg.top.value"
      :fullscreen="failInspDlg.fullscreen.value"
      :close-on-click-modal="false"
      @closed="onFailInspDialogClosed"
    >
      <el-form label-width="96px">
        <el-form-item label="下一道工序" required>
          <el-select
            v-model="failInspProcessId"
            placeholder="请先选择下一道工序"
            filterable
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="p in failInspFilteredProcesses"
              :key="p.id"
              :value="String(p.id)"
              :label="`${p.code} — ${p.name}`"
            >
              {{ p.code }} — {{ p.name }}
              <el-tag v-if="p.category === 'OUTSOURCE'" type="warning" size="small" effect="plain" class="opt-tag">
                外协
              </el-tag>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="目标生产货架" required>
          <el-select
            v-model="failInspShelfId"
            placeholder="先选工序；货架候选按映射过滤"
            filterable
            clearable
            style="width: 100%"
            :disabled="!failInspProcessId"
          >
            <el-option
              v-for="s in failInspFilteredShelves"
              :key="s.id"
              :value="String(s.id)"
              :label="`${s.code} — ${s.name}`"
              :disabled="!s.is_active"
            >
              {{ s.code }} — {{ s.name }}
              <span v-if="!s.is_active" class="muted">（已停用）</span>
            </el-option>
            <template #empty>
              <span class="muted">
                {{
                  failInspProcessId
                    ? '当前工序未映射到任何生产货架，请先在「货架管理 → 工序映射」配置'
                    : '请先选择下一道工序'
                }}
              </span>
            </template>
          </el-select>
        </el-form-item>
        <el-form-item label="品检备注">
          <el-input
            v-model="failInspNote"
            type="textarea"
            :rows="3"
            :maxlength="500"
            show-word-limit
            placeholder="不合格原因 / 返修要点（写入事件历史，工人领取时可见）"
          />
        </el-form-item>
        <el-alert
          type="info"
          :closable="false"
          title="指定工序后零件回到「在生产货架上」状态，下一道工序与备注已写入事件历史；工人领取时可在卡片上看到备注。"
          show-icon
        />
      </el-form>
      <template #footer>
        <el-button @click="failInspDialogVisible = false">取消</el-button>
        <el-button
          type="warning"
          :loading="failInspSubmitting"
          :disabled="!failInspProcessId || !failInspShelfId"
          @click="onFailInspectionConfirm"
        >确认指定工序</el-button>
      </template>
    </el-dialog>

    <!-- 取消 / 删除确认对话框 -->
    <el-dialog
      v-model="confirmVisible"
      :title="confirmTitle"
      :width="confirmDlg.width.value"
      :top="confirmDlg.top.value"
      :fullscreen="confirmDlg.fullscreen.value"
    >
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

    <!-- 下发到 CNC 货架对话框（PROGRAMMING → IN_PROCESS）—— 2026-07-21 改：先选下一道工序再选目标货架 -->
    <el-dialog
      v-model="releaseVisible"
      title="下发到 CNC 货架"
      :width="releaseDlg.width.value"
      :top="releaseDlg.top.value"
      :fullscreen="releaseDlg.fullscreen.value"
      @closed="onReleaseClosed"
    >
      <el-form label-width="96px">
        <el-form-item label="下一道工序" required>
          <el-select
            v-model="releaseNextProcessId"
            placeholder="请先选择下一道工序"
            style="width: 100%"
            filterable
            clearable
          >
            <el-option
              v-for="p in releaseFilteredProcesses"
              :key="p.id"
              :label="`${p.code} / ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="目标货架" required>
          <el-select
            v-model="releaseShelfId"
            placeholder="先选工序；货架候选按映射过滤"
            style="width: 100%"
            filterable
            clearable
            :disabled="!releaseNextProcessId"
          >
            <el-option
              v-for="s in releaseFilteredShelves"
              :key="s.id"
              :label="s.name"
              :value="s.id"
            />
            <template #empty>
              <span class="muted">
                {{
                  releaseNextProcessId
                    ? '当前工序未映射到任何生产货架，请先在「货架管理 → 工序映射」配置'
                    : '请先选择下一道工序'
                }}
              </span>
            </template>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="releaseVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="releaseSubmitting"
          :disabled="!releaseShelfId || !releaseNextProcessId"
          @click="onReleaseConfirm"
        >确认下发</el-button>
      </template>
    </el-dialog>

    <!-- 新建外协报价 对话框（2026-07-16 新增；part_id 隐式取自 partDetail） -->
    <el-dialog
      v-model="showQuoteCreate"
      title="新建外协报价（DRAFT）"
      :width="quoteCreateDlg.width.value"
      :top="quoteCreateDlg.top.value"
      :fullscreen="quoteCreateDlg.fullscreen.value"
      :close-on-click-modal="false"
      @closed="onQuoteCreateDialogClosed"
    >
      <el-form
        ref="quoteFormRef"
        :model="quoteForm"
        :rules="quoteRules"
        label-width="100px"
      >
        <el-form-item label="零件">
          <el-input
            v-model="part!.name"
            disabled
            placeholder="当前零件"
          />
        </el-form-item>
        <el-form-item label="外协公司" prop="outsource_company_id">
          <el-select
            v-model="quoteForm.outsource_company_id"
            filterable
            style="width:100%"
          >
            <el-option
              v-for="c in quoteCompanies"
              :key="c.id"
              :label="c.name"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="工序(OUTSOURCE)" prop="process_id">
          <el-select
            v-model="quoteForm.process_id"
            filterable
            style="width:100%"
          >
            <el-option
              v-for="p in quoteOutsourceProcesses"
              :key="p.id"
              :label="`${p.code} ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="单价(元)" prop="price">
          <el-input v-model="quoteForm.price" type="number" :precision="2" :step="0.01" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="quoteForm.note" type="textarea" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showQuoteCreate = false">取消</el-button>
        <el-button
          type="primary"
          :loading="quoteSubmitting"
          @click="onQuoteCreateConfirm"
        >保存为 DRAFT</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules, type UploadFile } from 'element-plus'
import { ArrowRight, Connection, Cpu, Document, Plus, PriceTag, Right, Setting, Upload, User } from '@element-plus/icons-vue'
import FileListCard from '@/components/FileListCard.vue'
import Barcode from '@/components/Barcode.vue'
import {
  cancelPart,
  cancelPartBatch,
  failInspection,
  getPart,
  listPartBatches,
  listPartEvents,
  passInspection,
  receiveFromOutsource,
  releaseFromProgramming,
  softDeletePart,
  splitPartBatch,
  updatePart,
  type PartBatch,
  type PartItem,
  type PartEvent,
  type PartUpdatePayload,
} from '@/api/parts'
import {
  deleteCncProgram,
  getCncDownloadUrl,
  listPartCncPrograms,
  listPartSetupSheets,
  uploadPartCncProgram,
  uploadPartSetupSheet,
  uploadCncPair,
} from '@/api/cnc'
import type { PartFileItem } from '@/types/part_file'
import { listShelves } from '@/api/shelves'
import type { Shelf } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import type { Process } from '@/types/process'
import {
  createOutsourceQuote,
  listOutsourceCompanies,
  listOutsourceQuotes,
} from '@/api/outsource'
import {
  OUTSOURCE_QUOTE_STATUS_LABEL,
  OUTSOURCE_QUOTE_STATUS_TAG,
  type OutsourceQuote,
  type OutsourceQuoteStatus,
} from '@/types/outsource'
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
import {
  DELIVERY_NOTE_STATUS_LABEL,
  DELIVERY_NOTE_STATUS_TAG,
  type DeliveryNoteStatus,
} from '@/types/deliveryNote'
import {
  listPartFiles,
  uploadPartDrawing,
  uploadPart3DModel,
  uploadPartCadFile,
} from '@/api/assembly'
import { useAuthSession } from '@/composables/useAuthSession'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'

const route = useRoute()
const router = useRouter()
const partId = ref<string>(String(route.params.id ?? ''))

// ============ 响应式 ============
const { isMobile, isTablet } = useBreakpoint()
const descCol = computed(() => (isMobile.value ? 1 : isTablet.value ? 2 : 3))

// 各 dialog 独立的响应式宽度（保留桌面固定 px）
const receiveOutsourceDlg = useDialogSize({ desktopWidth: 560, fullscreenOnMobile: true })
const failInspDlg = useDialogSize({ desktopWidth: 480, fullscreenOnMobile: true })
const confirmDlg = useDialogSize({ desktopWidth: 420 })
const releaseDlg = useDialogSize({ desktopWidth: 440, fullscreenOnMobile: true })
const quoteCreateDlg = useDialogSize({ desktopWidth: 640, fullscreenOnMobile: true })

// ============ 数据 ============
const part = ref<PartItem | null>(null)
const events = ref<PartEvent[] | null>(null)
// 批次监控（2026-07-29 批次化）
const batches = ref<PartBatch[]>([])
const batchesLoading = ref(false)
const batchTotalQty = computed(() =>
  batches.value.reduce((acc, b) => acc + b.quantity, 0),
)
const splitDialogVisible = ref(false)
const splitSource = ref<PartBatch | null>(null)
const splitQuantity = ref<number | undefined>(undefined)
const splitSubmitting = ref(false)
const drawings = ref<PartFileItem[]>([])
const models3d = ref<PartFileItem[]>([])
const cadFiles = ref<PartFileItem[]>([])
const cncPrograms = ref<PartFileItem[]>([])
const setupSheets = ref<PartFileItem[]>([])
const assemblyDetail = ref<AssemblyDetail | null>(null)
// 外协报价（2026-07-16 新增；只读展示 + 状态+角色门控的新建入口）
const quotes = ref<OutsourceQuote[]>([])
const quotesLoading = ref(false)
const showQuoteCreate = ref(false)
const quoteForm = reactive({
  outsource_company_id: '' as string,
  process_id: '' as string,
  price: '' as string,
  note: '' as string,
})
const quoteFormRef = ref<FormInstance>()
// 前端必填校验：part_id 由 URL 隐式取自 partDetail，无需 prop。外协公司/工序/单价必填，price 还需 > 0。
const quoteRules: FormRules = {
  outsource_company_id: [
    { required: true, message: '请选择外协公司', trigger: 'change' },
  ],
  process_id: [
    { required: true, message: '请选择工序', trigger: 'change' },
  ],
  price: [
    { required: true, message: '请填写单价', trigger: 'blur' },
    {
      validator: (_rule, value: string, cb) => {
        const n = Number(value)
        if (value === '' || value == null || Number.isNaN(n) || n <= 0) {
          cb(new Error('单价必须大于 0'))
        } else {
          cb()
        }
      },
      trigger: 'blur',
    },
  ],
}
const quoteOutsourceProcesses = ref<Process[]>([])
const quoteCompanies = ref<{ id: string; name: string }[]>([])
const quoteSubmitting = ref(false)
const infoLoading = ref(false)
const eventsLoading = ref(false)
const filesLoading = ref(false)
const cncLoading = ref(false)
const assemblyLoading = ref(false)
// 配对上传对话框
const pairUploadVisible = ref(false)
const pairGcodeFiles = ref<UploadFile[]>([])
const pairSetupFile = ref<File | null>(null)
const pairUploading = ref(false)

interface CncSetupGroup {
  setup: PartFileItem | null  // null = 「未配对 gcode」桶
  gcodes: PartFileItem[]
}
const cncSetupGroups = computed<CncSetupGroup[]>(() => {
  const gcodeList = cncPrograms.value
  const setupList = setupSheets.value
  const setupById = new Map<string, PartFileItem>()
  for (const s of setupList) setupById.set(s.id, s)

  // 按 paired_file_id 把 gcode 分配到 setup
  const bySetupId = new Map<string, PartFileItem[]>()
  const unpairedGcodes: PartFileItem[] = []
  for (const g of gcodeList) {
    if (g.paired_file_id && setupById.has(g.paired_file_id)) {
      const arr = bySetupId.get(g.paired_file_id) ?? []
      arr.push(g)
      bySetupId.set(g.paired_file_id, arr)
    } else {
      unpairedGcodes.push(g)
    }
  }

  // 渲染顺序：先所有 setup（按原列表顺序）+ 其下挂载的 gcodes，再「未配对」桶
  const groups: CncSetupGroup[] = []
  for (const s of setupList) {
    groups.push({ setup: s, gcodes: bySetupId.get(s.id) ?? [] })
  }
  if (unpairedGcodes.length > 0) {
    groups.push({ setup: null, gcodes: unpairedGcodes })
  }
  return groups
})

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
  order_no: '' as string | null,
  system_delivery_date: '' as string | null,
  note: '' as string | null,
})

function onStartEdit(): void {
  if (!part.value) return
  form.name = part.value.name
  form.drawing_no = part.value.drawing_no
  form.quantity = part.value.quantity
  form.is_urgent = part.value.is_urgent
  form.planned_delivery_date = part.value.planned_delivery_date
  form.actual_delivery_date = part.value.actual_delivery_date
  form.order_no = part.value.order_no
  form.system_delivery_date = part.value.system_delivery_date
  form.note = part.value.note
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
      order_no: form.order_no || null,
      system_delivery_date: form.system_delivery_date || null,
      note: form.note || null,
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
    void fetchBatches()
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

// ============ 批次监控（2026-07-29 批次化）============
const canManageBatches = computed(() => isManager.value || isClerk.value)

function isTerminalBatch(b: PartBatch): boolean {
  return b.status === 'COMPLETED' || b.status === 'CANCELLED'
}

async function fetchBatches(): Promise<void> {
  batchesLoading.value = true
  try {
    batches.value = await listPartBatches(partId.value)
  } catch (e) {
    batches.value = []
    ElMessage.error((e as Error).message ?? '加载批次失败')
  } finally {
    batchesLoading.value = false
  }
}

function openSplitDialog(b: PartBatch): void {
  splitSource.value = b
  splitQuantity.value = undefined
  splitDialogVisible.value = true
}

function onSplitDialogClosed(): void {
  splitSource.value = null
  splitQuantity.value = undefined
}

async function onSplitConfirm(): Promise<void> {
  if (!splitSource.value || !splitQuantity.value) return
  splitSubmitting.value = true
  try {
    batches.value = await splitPartBatch(partId.value, {
      batch_id: splitSource.value.id,
      quantity: splitQuantity.value,
    })
    ElMessage.success('拆分成功')
    splitDialogVisible.value = false
    await fetchPart()
    void fetchEvents()
    void fetchBatches()
  } catch (e) {
    ElMessage.error(`拆分失败：${(e as Error).message}`)
  } finally {
    splitSubmitting.value = false
  }
}

async function onCancelBatch(b: PartBatch): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认取消批次 ${b.batch_label}（${b.quantity} 件，${statusLabelOf(b.status)}）？`
      + '该批次数量将从在制中移除，不可恢复。',
      '取消批次',
      { confirmButtonText: '确认取消', cancelButtonText: '返回', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    batches.value = await cancelPartBatch(partId.value, b.id)
    ElMessage.success('批次已取消')
    await fetchPart()
    void fetchEvents()
    void fetchBatches()
  } catch (e) {
    ElMessage.error(`取消批次失败：${(e as Error).message}`)
  }
}

async function fetchQuotes(): Promise<void> {
  if (!canViewQuotes.value) {
    quotes.value = []
    quotesLoading.value = false
    return
  }

  quotesLoading.value = true
  try {
    const r = await listOutsourceQuotes({ part_id: partId.value, limit: 200 })
    quotes.value = r.items
  } catch (e) {
    quotes.value = []
    ElMessage.error((e as Error).message ?? '加载外协报价失败')
  } finally {
    quotesLoading.value = false
  }
}

async function fetchDrawings(): Promise<void> {
  filesLoading.value = true
  try {
    drawings.value = await listPartFiles(partId.value, 'DRAWING')
  } catch (e) {
    drawings.value = []
    ElMessage.error((e as Error).message ?? '加载图纸列表失败')
  } finally {
    filesLoading.value = false
  }
}

async function fetch3DModels(): Promise<void> {
  try {
    models3d.value = await listPartFiles(partId.value, '3D_MODEL')
  } catch (e) {
    models3d.value = []
    ElMessage.error((e as Error).message ?? '加载 3D 模型列表失败')
  }
}

async function fetchCadFiles(): Promise<void> {
  try {
    cadFiles.value = await listPartFiles(partId.value, 'CAD_2D')
  } catch (e) {
    cadFiles.value = []
    ElMessage.error((e as Error).message ?? '加载 CAD 源文件失败')
  }
}

async function fetchCncPrograms(): Promise<void> {
  cncLoading.value = true
  try {
    cncPrograms.value = await listPartCncPrograms(partId.value, 'G_CODE')
    setupSheets.value = await listPartSetupSheets(partId.value)
  } catch (e) {
    cncPrograms.value = []
    setupSheets.value = []
    ElMessage.error((e as Error).message ?? '加载 CNC 文件失败')
  } finally {
    cncLoading.value = false
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

watch(
  () => route.params.id,
  async (id) => {
    const s = String(id ?? '')
    if (!s) return
    partId.value = s
    editing.value = false
    assemblyDetail.value = null
    drawings.value = []
    models3d.value = []
    cadFiles.value = []
    cncPrograms.value = []
    setupSheets.value = []
    quotes.value = []
    await fetchPart()
    void fetchEvents()
    void fetchBatches()
    void fetchQuotes()
    void fetchDrawings()
    void fetch3DModels()
    void fetchCadFiles()
    void fetchCncPrograms()
    void fetchAssembly()
  },
)

watch(
  () => part.value?.assembly_id,
  () => {
    void fetchAssembly()
  },
)

// ============ 角色权限（前端 UI 控制；后端有真权限校验兜底） ============
const { hasRole } = useAuthSession()
const isManager = computed(() => hasRole('MANAGER'))
const isClerk = computed(() => hasRole('CLERK'))
const isCnc = computed(() => hasRole('CNC_PROGRAMMER'))

// 图纸 / 3D 模型：MANAGER + CLERK（文员日常操作）
const canManageDrawings = computed(() => isManager.value || isClerk.value)
const canManage3DModels = computed(() => isManager.value || isClerk.value)

// G 代码 / 设定单：MANAGER + CNC_PROGRAMMER
const canManageCncFiles = computed(() => isManager.value || isCnc.value)
const canManageSetupSheet = computed(() => isManager.value || isCnc.value)

// 取消订单：MANAGER + CLERK
const canCancelPart = computed(() => isManager.value || isClerk.value)

// 删除订单：MANAGER-only
const canDeletePart = computed(() => isManager.value)

// 编辑零件信息 / 查看外协报价：MANAGER + CLERK
const canEditPart = computed(() => isManager.value || isClerk.value)
const canViewQuotes = computed(() => isManager.value || isClerk.value)

// 品检通过 / 打回：MANAGER + CLERK + INSPECTOR（与后端 _inspector_dep 一致）
const isInspector = computed(() => hasRole('INSPECTOR'))
const canInspect = computed(
  () => isManager.value || isClerk.value || isInspector.value,
)

// ============ CNC 程序（G 代码 + 设定单）============
function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function beforeCncUpload(file: File): boolean {
  if (file.size > 100 * 1024 * 1024) {
    ElMessage.error('文件超过 100MB 上限')
    return false
  }
  return true
}

async function onUploadCnc(req: { file: File }): Promise<void> {
  try {
    await uploadPartCncProgram(partId.value, req.file)
    ElMessage.success('上传成功')
    void fetchCncPrograms()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '上传失败')
  }
}

async function onUploadSetupSheet(req: { file: File }): Promise<void> {
  try {
    await uploadPartSetupSheet(partId.value, req.file)
    ElMessage.success('设定单上传成功')
    void fetchCncPrograms()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '上传失败')
  }
}

// ===== 配对上传（G 代码 + 设定单） =====
function onPairGcodeChange(file: UploadFile): void {
  // 按 uid 去重 + 扩展名校验（与 PartBatchNew.vue 的 fileList 模式一致）
  pairGcodeFiles.value = fileList(
    pairGcodeFiles.value,
    file,
    '.nc,.tap,.cnc,.mpf,.ngc',
    true,
  )
}
function onPairGcodeRemove(file: UploadFile): void {
  pairGcodeFiles.value = pairGcodeFiles.value.filter((f) => f.uid !== file.uid)
}
function onPairSetupChange(file: UploadFile): void {
  pairSetupFile.value = file.raw ?? null
}
function onPairUploadClose(): void {
  pairGcodeFiles.value = []
  pairSetupFile.value = null
}
async function onPairUploadConfirm(): Promise<void> {
  const raws: File[] = []
  for (const f of pairGcodeFiles.value) {
    if (f.raw) raws.push(f.raw)
  }
  if (raws.length === 0 || !pairSetupFile.value) return
  pairUploading.value = true
  try {
    // 逐个上传；setup 走 SHA-256 dedup 实际只上传一次
    for (const gcode of raws) {
      await uploadCncPair(partId.value, gcode, pairSetupFile.value)
    }
    ElMessage.success(`配对上传成功（${raws.length} 个 G 代码 + 1 个设定单）`)
    pairUploadVisible.value = false
    onPairUploadClose()
    void fetchCncPrograms()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '配对上传失败')
  } finally {
    pairUploading.value = false
  }
}

// 多文件 staging 助手（仿 PartBatchNew.vue:1803-1819）
function fileList(
  current: UploadFile[],
  file: UploadFile,
  accept: string,
  matchExt = false,
): UploadFile[] {
  if (current.some((f) => f.uid === file.uid)) return current
  if (matchExt) {
    const name = (file.name || '').toLowerCase()
    const exts = accept.replace(/\./g, '').split(',')
    if (!exts.some((e) => name.endsWith('.' + e))) {
      ElMessage.warning(`不支持的文件类型：${file.name}`)
      return current
    }
  }
  return [...current, file]
}

async function onDownloadCnc(p: PartFileItem): Promise<void> {
  try {
    const url = await getCncDownloadUrl(p.id)
    window.open(url, '_blank')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '获取下载链接失败')
  }
}

async function onDeleteCnc(id: string): Promise<void> {
  try {
    await deleteCncProgram(id)
    ElMessage.success('已删除')
    void fetchCncPrograms()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

// ============ 下发到 CNC 货架 ============
const releaseVisible = ref(false)
const releaseShelfId = ref<string | null>(null)
const releaseNextProcessId = ref<string | null>(null)
const releaseSubmitting = ref(false)
const productionShelves = ref<Shelf[]>([])
const processes = ref<Process[]>([])

// 2026-07-17：releaseVisible 用 useShelfProcessFilter
const {
  filteredShelves: releaseFilteredShelves,
  filteredProcesses: releaseFilteredProcesses,
  load: loadReleaseMap,
} = useShelfProcessFilter(
  productionShelves,
  processes,
  releaseShelfId,
  releaseNextProcessId,
)

async function onOpenReleaseDialog(): Promise<void> {
  releaseShelfId.value = null
  releaseNextProcessId.value = null
  try {
    const [shelfResp, procResp] = await Promise.all([
      listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 }),
      listProcesses({ limit: 200 }),
    ])
    productionShelves.value = shelfResp.items
    processes.value = procResp.items
    void loadReleaseMap()
  } catch {
    productionShelves.value = []
    processes.value = []
  }
  releaseVisible.value = true
}

function onReleaseClosed(): void {
  releaseShelfId.value = null
  releaseNextProcessId.value = null
}

async function onReleaseConfirm(): Promise<void> {
  if (!releaseShelfId.value || !releaseNextProcessId.value) return
  releaseSubmitting.value = true
  try {
    await releaseFromProgramming(
      partId.value, releaseShelfId.value, releaseNextProcessId.value,
    )
    ElMessage.success('已下发到生产货架')
    releaseVisible.value = false
    await fetchPart()
    void fetchEvents()
    void fetchBatches()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '下发失败')
  } finally {
    releaseSubmitting.value = false
  }
}

// ============ 品检通过 / 打回 ============
const passSubmitting = ref(false)

async function onPassInspection(): Promise<void> {
  if (!part.value) return
  try {
    await ElMessageBox.confirm(
      `确认零件「${part.value.name}」(${part.value.serial_no || part.value.drawing_no}) 品检合格，进入待送货状态？`,
      '品检通过',
      { type: 'success', confirmButtonText: '确认通过', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  passSubmitting.value = true
  try {
    await passInspection(partId.value)
    ElMessage.success('品检通过')
    await fetchPart()
    void fetchEvents()
    void fetchBatches()
  } catch (e) {
    ElMessage.error(`品检通过失败：${(e as Error).message}`)
  } finally {
    passSubmitting.value = false
  }
}

// 2026-07-21：指定工序对话框 —— 先选下一道工序，再选目标生产货架；可选品检备注。
const failInspDialogVisible = ref(false)
const failInspProcessId = ref<string>('')
const failInspShelfId = ref<string>('')
const failInspNote = ref<string>('')
const failInspSubmitting = ref(false)

// fail-inspection 用独立的 useShelfProcessFilter 实例（不复用 releaseVisible 的，
// 因为两个对话框的 shelf/process ref 不同；共享 ref 会导致关闭弹窗互相影响）。
const {
  filteredShelves: failInspFilteredShelves,
  filteredProcesses: failInspFilteredProcesses,
  load: loadFailInspMap,
} = useShelfProcessFilter(
  productionShelves,
  processes,
  computed({
    get: () => failInspShelfId.value || null,
    set: (v) => { failInspShelfId.value = v ?? '' },
  }),
  computed({
    get: () => failInspProcessId.value || null,
    set: (v) => { failInspProcessId.value = v ?? '' },
  }),
)

async function openFailInspectionDialog(): Promise<void> {
  failInspProcessId.value = ''
  failInspShelfId.value = ''
  failInspNote.value = ''
  // 与 releaseVisible 共享 productionShelves/processes 缓存；
  // 若 releaseVisible 还没打开过，此处按需补加载。
  if (productionShelves.value.length === 0 || processes.value.length === 0) {
    try {
      const [shelfResp, procResp] = await Promise.all([
        productionShelves.value.length === 0
          ? listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 })
          : Promise.resolve(null),
        processes.value.length === 0
          ? listProcesses({ limit: 200 })
          : Promise.resolve(null),
      ])
      if (shelfResp) productionShelves.value = shelfResp.items
      if (procResp) processes.value = procResp.items
    } catch {
      // ignore（filteredXxx 走兜底全量）
    }
  }
  void loadFailInspMap()
  failInspDialogVisible.value = true
}

function onFailInspDialogClosed(): void {
  failInspProcessId.value = ''
  failInspShelfId.value = ''
  failInspNote.value = ''
}

async function onFailInspectionConfirm(): Promise<void> {
  if (!failInspProcessId.value || !failInspShelfId.value) return
  failInspSubmitting.value = true
  try {
    await failInspection(partId.value, {
      shelf_id: failInspShelfId.value,
      next_process_id: failInspProcessId.value,
      note: failInspNote.value.trim() || null,
    })
    ElMessage.success('已指定下一道工序')
    failInspDialogVisible.value = false
    await fetchPart()
    void fetchEvents()
    void fetchBatches()
  } catch (e) {
    ElMessage.error(`指定工序失败：${(e as Error).message}`)
  } finally {
    failInspSubmitting.value = false
  }
}

// ============ 外协回收（2026-07-15 新增）============
const canReceiveFromOutsource = computed(() => isManager.value || isClerk.value)
const receiveOutsourceDialogVisible = ref(false)
const receiveShelfId = ref<string>('')
const receiveProcessId = ref<string>('')
const receiveSubmitting = ref(false)
const inhouseProcesses = computed(() =>
  processes.value.filter((p) => p.category === 'INHOUSE'),
)

// 2026-07-17：receiveOutsourceDialogVisible 用 useShelfProcessFilter
// 关键：processes 限缩成 INHOUSE 类别（外协回收必 INHOUSE），
// 走 inhouseProcesses 而非全量 processes。
const {
  filteredShelves: receiveFilteredShelves,
  filteredProcesses: receiveFilteredProcesses,
  load: loadReceiveMap,
} = useShelfProcessFilter(
  productionShelves,
  inhouseProcesses,
  // useShelfProcessFilter 要求 string|null；这里 ref 是 string 转一下
  computed({
    get: () => receiveShelfId.value || null,
    set: (v) => { receiveShelfId.value = v ?? '' },
  }),
  computed({
    get: () => receiveProcessId.value || null,
    set: (v) => { receiveProcessId.value = v ?? '' },
  }),
)

async function openReceiveOutsourceDialog(): Promise<void> {
  receiveShelfId.value = ''
  receiveProcessId.value = ''
  try {
    if (productionShelves.value.length === 0) {
      const resp = await listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 })
      productionShelves.value = resp.items
    }
    if (processes.value.length === 0) {
      const resp = await listProcesses({ limit: 200 })
      processes.value = resp.items
    }
    void loadReceiveMap()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载失败')
  }
  receiveOutsourceDialogVisible.value = true
}

function onReceiveOutsourceDialogClosed(): void {
  receiveShelfId.value = ''
  receiveProcessId.value = ''
}

async function onReceiveConfirm(): Promise<void> {
  if (!receiveShelfId.value || !receiveProcessId.value) return
  receiveSubmitting.value = true
  try {
    await receiveFromOutsource(partId.value, {
      shelf_id: receiveShelfId.value,
      next_process_id: receiveProcessId.value,
    })
    ElMessage.success('外协已回收')
    receiveOutsourceDialogVisible.value = false
    await fetchPart()
    void fetchEvents()
    void fetchBatches()
  } catch (e) {
    ElMessage.error(`外协回收失败：${(e as Error).message}`)
  } finally {
    receiveSubmitting.value = false
  }
}

onMounted(() => {
  void fetchPart()
  void fetchEvents()
  void fetchBatches()
  void fetchQuotes()
  void fetchDrawings()
  void fetch3DModels()
  void fetchCadFiles()
  void fetchCncPrograms()
})

// ============ 外协报价（2026-07-16 新增）============
// 「新建外协报价」按钮：MANAGER + CLERK 且零件状态 ∈ {PENDING, IN_PROCESS, OUTSOURCE, READY_TO_SHIP, REPAIRING}
const canCreateQuote = computed(() => {
  if (!isManager.value && !isClerk.value) return false
  if (!part.value) return false
  const s = part.value.status
  return (
    s === 'PENDING'
    || s === 'IN_PROCESS'
    || s === 'OUTSOURCE'
    || s === 'READY_TO_SHIP'
    || s === 'REPAIRING'
  )
})

async function openQuoteCreateDialog(): Promise<void> {
  quoteForm.outsource_company_id = ''
  quoteForm.process_id = ''
  quoteForm.price = ''
  quoteForm.note = ''
  try {
    const [companyResp, procResp] = await Promise.all([
      listOutsourceCompanies({ limit: 200 }),
      listProcesses({ limit: 200 }),
    ])
    quoteCompanies.value = companyResp.items.map((c) => ({ id: c.id, name: c.name }))
    quoteOutsourceProcesses.value = procResp.items.filter((p) => p.category === 'OUTSOURCE')
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载下拉数据失败')
    return
  }
  showQuoteCreate.value = true
}

function onQuoteCreateDialogClosed(): void {
  quoteForm.outsource_company_id = ''
  quoteForm.process_id = ''
  quoteForm.price = ''
  quoteForm.note = ''
}

async function onQuoteCreateConfirm(): Promise<void> {
  if (!quoteFormRef.value) return
  // el-form 校验：外协公司 / 工序 / 单价（>0）；校验失败时 validate() reject，直接短路
  try {
    await quoteFormRef.value.validate()
  } catch {
    return
  }
  quoteSubmitting.value = true
  try {
    await createOutsourceQuote({
      part_id: partId.value,
      outsource_company_id: quoteForm.outsource_company_id,
      process_id: quoteForm.process_id,
      price: quoteForm.price || '0',
      note: quoteForm.note || null,
    })
    ElMessage.success('已创建 DRAFT 报价')
    showQuoteCreate.value = false
    await fetchQuotes()
    // 同步刷新历史时间线（创建事件 QUOTE_CREATED）
    void fetchEvents()
    void fetchBatches()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '创建失败')
  } finally {
    quoteSubmitting.value = false
  }
}

/** 报价列表里的「详情」按钮 — 跳到 /outsource/quote 报价一览（统一操作入口） */
function onViewQuoteDetail(_q: OutsourceQuote): void {
  // PartDetail 上只读，编辑/审批/删除都走 /outsource/quote
  router.push('/outsource/quote')
}
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
.opt-tag {
  margin-left: 6px;
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
  .operator-name {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--text-secondary);
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
    margin-top: 4px;
  }
}

.cnc-card {
  :deep(.el-card__body) {
    padding: 16px 20px;
  }
  .cnc-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .cnc-group-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .cnc-group-header {
    display: grid;
    grid-template-columns: 2fr 1fr;     // 左列宽于右列
    gap: 8px;
    padding: 4px 8px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
  }
  .cnc-group-header-col {
    text-align: left;
  }
  .cnc-group-row {
    display: grid;
    grid-template-columns: 2fr 1fr;
    align-items: stretch;
    gap: 8px;
    padding: 6px 8px;
    border: 1px solid var(--el-border-color-lighter);
    border-radius: 4px;
    font-size: 13px;

    @include until(sm) {
      grid-template-columns: 1fr;
      gap: 4px;
    }
  }
  .cnc-gcode-col,
  .cnc-setup-col {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .cnc-sub-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    min-width: 0;
  }
  .cnc-empty {
    color: var(--text-secondary);
    font-size: 13px;
  }
  .cnc-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .cnc-size,
  .cnc-time {
    color: var(--text-secondary);
    font-size: 12px;
  }
  .cnc-upload {
    margin-top: 12px;
    display: flex;
    gap: 8px;
  }
}

/* 批次监控（2026-07-29） */
.batch-label {
  font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace;
  font-weight: 600;
}
.split-dialog-body p {
  margin: 6px 0;
  line-height: 1.6;
}
</style>
