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
          <el-descriptions :column="3" border>
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
              <el-date-picker v-model="form.planned_delivery_date" type="date" size="small" style="width:100%" />
            </el-descriptions-item>
            <el-descriptions-item label="实际送货">
              <el-date-picker v-model="form.actual_delivery_date" type="date" size="small" style="width:100%" />
            </el-descriptions-item>
            <el-descriptions-item label="单据 ID">#{{ part.id }}</el-descriptions-item>
          </el-descriptions>

          <div class="edit-actions">
            <el-button @click="onCancelEdit">取消</el-button>
            <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
          </div>
        </template>

        <template v-else>
          <el-descriptions :column="3" border>
            <el-descriptions-item label="序列号">
              <span v-if="part.serial_no" class="mono">{{ part.serial_no }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="图号">{{ part.drawing_no }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTagType(part.status)" effect="plain" size="small">
                {{ statusLabel(part.status) }}
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
          </el-descriptions>

          <div class="edit-actions">
            <el-button type="primary" plain @click="onStartEdit">编辑</el-button>
          </div>
        </template>
      </template>
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
      <el-descriptions v-if="assemblyDetail" :column="3" border>
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
      :show-print="true"
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
          <span v-if="canManageCncFiles && part?.status === 'PROGRAMMING'" class="event-count">
            下发前需上传 G 代码 + 设定单
          </span>
        </div>
      </template>

      <el-tabs v-model="cncActiveTab">
        <el-tab-pane name="gcode" label="G 代码">
          <div v-if="cncPrograms && cncPrograms.length > 0" class="cnc-list">
            <div v-for="p in cncPrograms" :key="p.id" class="cnc-row">
              <el-tag size="small" type="info">{{ p.file_type }}</el-tag>
              <span class="cnc-name">{{ p.original_filename }}</span>
              <span class="cnc-size">{{ formatBytes(p.file_size) }}</span>
              <span class="cnc-time">{{ formatDateTime(p.created_at) }}</span>
              <el-button link type="primary" size="small" @click="onDownloadCnc(p)">下载</el-button>
              <el-button
                v-if="canManageCncFiles"
                link
                type="danger"
                size="small"
                @click="onDeleteCnc(p.id)"
              >删除</el-button>
            </div>
          </div>
          <el-empty v-else description="暂无 G 代码程序" :image-size="80" />
        </el-tab-pane>

        <el-tab-pane name="setup" label="CNC 设定单">
          <div v-if="setupSheets && setupSheets.length > 0" class="cnc-list">
            <div v-for="p in setupSheets" :key="p.id" class="cnc-row">
              <el-tag size="small" type="info">{{ p.file_type }}</el-tag>
              <span class="cnc-name">{{ p.original_filename }}</span>
              <span class="cnc-size">{{ formatBytes(p.file_size) }}</span>
              <span class="cnc-time">{{ formatDateTime(p.created_at) }}</span>
              <el-button link type="primary" size="small" @click="onDownloadCnc(p)">下载</el-button>
              <el-button
                v-if="canManageSetupSheet"
                link
                type="danger"
                size="small"
                @click="onDeleteCnc(p.id)"
              >删除</el-button>
            </div>
          </div>
          <el-empty v-else description="暂无 CNC 设定单" :image-size="80" />
        </el-tab-pane>
      </el-tabs>

      <div v-if="canManageCncFiles || canManageSetupSheet" class="cnc-upload">
        <el-upload
          v-if="canManageCncFiles"
          :http-request="onUploadCnc"
          :show-file-list="false"
          accept=".nc,.tap,.cnc,.mpf,.ngc"
          :before-upload="beforeCncUpload"
        >
          <el-button type="primary" plain>
            <el-icon><Upload /></el-icon><span>上传 G 代码</span>
          </el-button>
        </el-upload>
        <el-upload
          v-if="canManageSetupSheet"
          :http-request="onUploadSetupSheet"
          :show-file-list="false"
          accept=".pdf"
          :before-upload="beforeCncUpload"
        >
          <el-button type="primary" plain>
            <el-icon><Upload /></el-icon><span>上传设定单</span>
          </el-button>
        </el-upload>
        <el-button
          v-if="canManageCncFiles && part?.status === 'PROGRAMMING'"
          type="success"
          :loading="releaseSubmitting"
          @click="onOpenReleaseDialog"
        >
          下发到 CNC 货架
        </el-button>
      </div>
    </el-card>

    <!-- 外协报价（2026-07-16 新增；只读展示 + 状态+角色门控的新建入口） -->
    <el-card shadow="never" class="quote-card" v-loading="quotesLoading">
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
        <el-table-column label="状态" width="110" align="center">
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
          show-overflow-tooltip
        />
        <el-table-column prop="process_code" label="工序" width="100" />
        <el-table-column label="单价(元)" width="100" align="right">
          <template #default="{ row }">{{ (row as OutsourceQuote).price }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">
            <span class="muted">{{ formatDateTime((row as OutsourceQuote).created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center" fixed="right">
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
          >品检打回</el-button>
        </template>
        <!-- 外协：PENDING/IN_PROCESS 可见（MANAGER + CLERK） -->
        <el-button
          v-if="canSendToOutsource && canBeSentToOutsource"
          type="primary"
          @click="openSendOutsourceDialog"
        >发送至外协</el-button>
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

    <!-- 发送至外协 对话框（2026-07-15 新增） -->
    <el-dialog
      v-model="sendOutsourceDialogVisible"
      title="发送至外协 — 选择外协工序与外协公司"
      width="560px"
      :close-on-click-modal="false"
      @closed="onSendOutsourceDialogClosed"
    >
      <el-form label-width="110px">
        <el-form-item label="外协工序" required>
          <el-radio-group
            v-model="sendOutsourceProcessId"
            style="display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto;"
            @change="onSendOutsourceProcessChange"
          >
            <el-radio
              v-for="p in outsourceProcesses"
              :key="p.id"
              :value="String(p.id)"
            >
              {{ p.code }} — {{ p.name }}
            </el-radio>
            <span v-if="outsourceProcesses.length === 0" class="muted">
              没有 OUTSOURCE 工序，请先在「设置 → 工序管理」中新增
            </span>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="外协公司" required>
          <el-radio-group
            v-model="sendOutsourceCompanyId"
            style="display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto;"
            :disabled="!sendOutsourceProcessId"
          >
            <el-radio
              v-for="c in filteredOutsourceCompanies"
              :key="c.id"
              :value="String(c.id)"
              :disabled="!c.is_active"
            >
              {{ c.name }}
              <span v-if="!c.is_active" class="muted">（已停用）</span>
            </el-radio>
            <span v-if="sendOutsourceProcessId && filteredOutsourceCompanies.length === 0" class="muted">
              没有公司映射此工序，请先在外协管理中维护
            </span>
            <span v-if="!sendOutsourceProcessId" class="muted">
              请先选择外协工序
            </span>
          </el-radio-group>
        </el-form-item>
        <el-alert
          v-if="part"
          type="info"
          :closable="false"
          show-icon
          :title="`当前状态：${part.status}；发送后状态将变为 OUTSOURCE。`"
        />
      </el-form>
      <template #footer>
        <el-button @click="sendOutsourceDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="sendOutsourceSubmitting"
          :disabled="!sendOutsourceProcessId || !sendOutsourceCompanyId"
          @click="onSendOutsourceConfirm"
        >确认发送</el-button>
      </template>
    </el-dialog>

    <!-- 外协回收 对话框（2026-07-15 新增） -->
    <el-dialog
      v-model="receiveOutsourceDialogVisible"
      title="外协回收 — 选择目标生产货架与下一道工序"
      width="560px"
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

    <!-- 品检打回对话框（PartDetail 用，复用 releaseVisible 之外的独立状态） -->
    <el-dialog
      v-model="failInspDialogVisible"
      title="品检打回 — 选择目标生产货架"
      width="480px"
      :close-on-click-modal="false"
      @closed="onFailInspDialogClosed"
    >
      <el-form label-width="96px">
        <el-form-item label="目标生产货架" required>
          <el-radio-group
            v-model="failInspShelfId"
            style="display: flex; flex-direction: column; gap: 6px; max-height: 220px; overflow-y: auto"
          >
            <el-radio
              v-for="s in productionShelves"
              :key="s.id"
              :value="String(s.id)"
              :disabled="!s.is_active"
            >
              {{ s.code }} — {{ s.name }}
              <span v-if="!s.is_active" class="muted">（已停用）</span>
            </el-radio>
            <span v-if="productionShelves.length === 0" class="muted">
              没有可用生产货架
            </span>
          </el-radio-group>
        </el-form-item>
        <el-alert
          type="info"
          :closable="false"
          title="打回后零件回到「在生产货架上」状态，next_process_id 清空，文员重新下发时再选下一道工序。"
          show-icon
        />
      </el-form>
      <template #footer>
        <el-button @click="failInspDialogVisible = false">取消</el-button>
        <el-button
          type="warning"
          :loading="failInspSubmitting"
          :disabled="!failInspShelfId"
          @click="onFailInspectionConfirm"
        >确认打回</el-button>
      </template>
    </el-dialog>

    <!-- 取消 / 删除确认对话框 -->
    <el-dialog v-model="confirmVisible" :title="confirmTitle" width="420px">
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

    <!-- 下发到 CNC 货架对话框（PROGRAMMING → IN_PROCESS） -->
    <el-dialog v-model="releaseVisible" title="下发到 CNC 货架" width="440px" @closed="onReleaseClosed">
      <el-form label-width="96px">
        <el-form-item label="目标货架" required>
          <el-select
            v-model="releaseShelfId"
            placeholder="选择生产货架"
            style="width: 100%"
            filterable
          >
            <el-option
              v-for="s in releaseFilteredShelves"
              :key="s.id"
              :label="s.name"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="下一道工序" required>
          <el-select
            v-model="releaseNextProcessId"
            placeholder="选择工序（必填）"
            style="width: 100%"
            filterable
          >
            <el-option
              v-for="p in releaseFilteredProcesses"
              :key="p.id"
              :label="`${p.code} / ${p.name}`"
              :value="p.id"
            />
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
      width="640px"
      :close-on-click-modal="false"
      @closed="onQuoteCreateDialogClosed"
    >
      <el-form label-width="100px">
        <el-form-item label="零件">
          <el-input
            v-model="part!.name"
            disabled
            placeholder="当前零件"
          />
        </el-form-item>
        <el-form-item label="外协公司" required>
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
        <el-form-item label="工序(OUTSOURCE)" required>
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
        <el-form-item label="单价(元)">
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
          :disabled="!quoteForm.outsource_company_id || !quoteForm.process_id"
          @click="onQuoteCreateConfirm"
        >保存为 DRAFT</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowRight, Connection, Cpu, Document, Plus, PriceTag, Right, Setting, Upload, User } from '@element-plus/icons-vue'
import FileListCard from '@/components/FileListCard.vue'
import Barcode from '@/components/Barcode.vue'
import {
  cancelPart,
  failInspection,
  getPart,
  listPartEvents,
  passInspection,
  receiveFromOutsource,
  releaseFromProgramming,
  sendToOutsource,
  softDeletePart,
  updatePart,
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
} from '@/api/cnc'
import type { PartFileItem } from '@/types/part_file'
import { listShelves } from '@/api/shelves'
import type { Shelf } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import type { Process } from '@/types/process'
import { listCompaniesByProcess } from '@/api/outsource'
import {
  createOutsourceQuote,
  listOutsourceCompanies,
  listOutsourceQuotes,
} from '@/api/outsource'
import {
  OUTSOURCE_QUOTE_STATUS_LABEL,
  OUTSOURCE_QUOTE_STATUS_TAG,
  type OutsourceCompany,
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
  listPartFiles,
  uploadPartDrawing,
  uploadPart3DModel,
  uploadPartCadFile,
} from '@/api/assembly'
import { useAuthSession } from '@/composables/useAuthSession'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'

const route = useRoute()
const router = useRouter()
const partId = ref<string>(String(route.params.id ?? ''))

// ============ 数据 ============
const part = ref<PartItem | null>(null)
const events = ref<PartEvent[] | null>(null)
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
const quoteOutsourceProcesses = ref<Process[]>([])
const quoteCompanies = ref<{ id: string; name: string }[]>([])
const quoteSubmitting = ref(false)
const infoLoading = ref(false)
const eventsLoading = ref(false)
const filesLoading = ref(false)
const cncLoading = ref(false)
const assemblyLoading = ref(false)
const cncActiveTab = ref<'gcode' | 'setup'>('gcode')

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
})

function onStartEdit(): void {
  if (!part.value) return
  form.name = part.value.name
  form.drawing_no = part.value.drawing_no
  form.quantity = part.value.quantity
  form.is_urgent = part.value.is_urgent
  form.planned_delivery_date = part.value.planned_delivery_date
  form.actual_delivery_date = part.value.actual_delivery_date
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

async function fetchQuotes(): Promise<void> {
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
  } catch (e) {
    ElMessage.error(`品检通过失败：${(e as Error).message}`)
  } finally {
    passSubmitting.value = false
  }
}

const failInspDialogVisible = ref(false)
const failInspShelfId = ref<string>('')
const failInspSubmitting = ref(false)

async function openFailInspectionDialog(): Promise<void> {
  failInspShelfId.value = ''
  if (productionShelves.value.length === 0) {
    try {
      const resp = await listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 })
      productionShelves.value = resp.items
    } catch {
      productionShelves.value = []
    }
  }
  failInspDialogVisible.value = true
}

function onFailInspDialogClosed(): void {
  failInspShelfId.value = ''
}

async function onFailInspectionConfirm(): Promise<void> {
  if (!failInspShelfId.value) return
  failInspSubmitting.value = true
  try {
    await failInspection(partId.value, failInspShelfId.value)
    ElMessage.success('已打回生产货架')
    failInspDialogVisible.value = false
    await fetchPart()
    void fetchEvents()
  } catch (e) {
    ElMessage.error(`品检打回失败：${(e as Error).message}`)
  } finally {
    failInspSubmitting.value = false
  }
}

// ============ 发送至外协（2026-07-15 新增）============
const canSendToOutsource = computed(() => isManager.value || isClerk.value)
const canReceiveFromOutsource = computed(() => isManager.value || isClerk.value)
const canBeSentToOutsource = computed(() => {
  if (!part.value) return false
  const s = part.value.status
  // PENDING / IN_PROCESS（ON_SHELF/WITH_WORKER）可发送
  // 终态 + PROGRAMMING + INSPECTION + READY_TO_SHIP + DELIVERED + REPAIRING + OUTSOURCE 不可
  return s === 'PENDING' || s === 'IN_PROCESS'
})

const sendOutsourceDialogVisible = ref(false)
const sendOutsourceProcessId = ref<string>('')
const sendOutsourceCompanyId = ref<string>('')
const sendOutsourceSubmitting = ref(false)
const outsourceProcesses = ref<Process[]>([])
const filteredOutsourceCompanies = ref<OutsourceCompany[]>([])

async function openSendOutsourceDialog(): Promise<void> {
  sendOutsourceProcessId.value = ''
  sendOutsourceCompanyId.value = ''
  filteredOutsourceCompanies.value = []
  try {
    if (outsourceProcesses.value.length === 0) {
      const all = await listProcesses({ limit: 200 })
      outsourceProcesses.value = all.items.filter((p) => p.category === 'OUTSOURCE')
    }
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载工序失败')
  }
  sendOutsourceDialogVisible.value = true
}

function onSendOutsourceDialogClosed(): void {
  sendOutsourceProcessId.value = ''
  sendOutsourceCompanyId.value = ''
  filteredOutsourceCompanies.value = []
}

async function onSendOutsourceProcessChange(): Promise<void> {
  sendOutsourceCompanyId.value = ''
  filteredOutsourceCompanies.value = []
  if (!sendOutsourceProcessId.value) return
  try {
    filteredOutsourceCompanies.value = await listCompaniesByProcess(
      sendOutsourceProcessId.value,
    )
  } catch (e) {
    ElMessage.error((e as Error).message ?? '加载外协公司失败')
  }
}

async function onSendOutsourceConfirm(): Promise<void> {
  if (!sendOutsourceProcessId.value || !sendOutsourceCompanyId.value) return
  sendOutsourceSubmitting.value = true
  try {
    await sendToOutsource(partId.value, {
      outsource_company_id: sendOutsourceCompanyId.value,
      next_process_id: sendOutsourceProcessId.value,
    })
    ElMessage.success('已发送至外协')
    sendOutsourceDialogVisible.value = false
    await fetchPart()
    void fetchEvents()
  } catch (e) {
    ElMessage.error(`发送外协失败：${(e as Error).message}`)
  } finally {
    sendOutsourceSubmitting.value = false
  }
}

// ============ 外协回收（2026-07-15 新增）============
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
  } catch (e) {
    ElMessage.error(`外协回收失败：${(e as Error).message}`)
  } finally {
    receiveSubmitting.value = false
  }
}

onMounted(() => {
  void fetchPart()
  void fetchEvents()
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
  if (!quoteForm.outsource_company_id || !quoteForm.process_id) {
    ElMessage.warning('请填写外协公司与工序')
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
  .cnc-row {
    display: grid;
    grid-template-columns: 60px 1fr 80px 130px auto auto;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border: 1px solid var(--el-border-color-lighter);
    border-radius: 4px;
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
</style>
