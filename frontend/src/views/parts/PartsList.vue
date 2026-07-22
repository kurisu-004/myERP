<!--
  PartsList.vue

  零件一览表（mobile-responsive，2026-07-21 合并 master + worktree-mobile-responsive）。

  桌面（≥md）：
    - el-table 撑开；表头 popover 筛选（状态 / 加急 / 客户）；序列号/图号/名称/计划交期均可点表头排序
    - 行内编辑（MANAGER + CLERK）：drawing_no/name/applicant_name/quantity/unit_price/
      request_date/planned_delivery_date/system_delivery_date/order_no/note/is_urgent
      全部就地改、保存；INSPECTOR 看不到「编辑 / 下发 / 批量打印 / 应标导入」按钮
    - 批量打印图纸（iframe）
    - 加急行红底 #fde2e2

  手机（<md）：
    - ResponsiveList 改走卡片流，卡片只展示关键字段（序列号 / 图号 / 数量 / 计划交期 /
      客户 / 所在位置 + 操作）
    - 表头 popover 折叠为底部弹出抽屉（el-drawer direction="btt"）；筛选按钮带激活高亮
    - 批量打印 hidden（依赖 iframe）；其余功能保留
-->
<template>
  <div class="parts-list">
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <!-- 搜索组（2026-07-22：三组分类） -->
        <div class="filter-group filter-group--search">
          <el-input
            v-model="search.keyword"
            placeholder="图号（含子串）/ 名称（前缀）"
            clearable
            style="width: 260px"
            @keyup.enter="onSearch"
            @clear="onSearch"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>

          <!-- 订单号独立搜索框（2026-07-22） -->
          <el-input
            v-model="search.orderNo"
            placeholder="订单号"
            clearable
            style="width: 160px"
            @keyup.enter="onSearch"
            @clear="onSearch"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>

          <el-button @click="onReset">
            <el-icon><RefreshLeft /></el-icon>
            <span>重置</span>
          </el-button>
        </div>

        <!-- 日期组（2026-07-22：三组分类） -->
        <div class="filter-group filter-group--dates">
          <div class="date-filter-item">
            <span class="date-filter-label">请购日期</span>
            <el-date-picker
              v-model="requestDateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              range-separator="~"
              start-placeholder="起点"
              end-placeholder="终点"
              unlink-panels
              clearable
              style="width: 240px"
              @change="onDateRangeChange"
            />
          </div>
          <div class="date-filter-item">
            <span class="date-filter-label">计划交期</span>
            <el-date-picker
              v-model="plannedDateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              range-separator="~"
              start-placeholder="起点"
              end-placeholder="终点"
              unlink-panels
              clearable
              style="width: 240px"
              @change="onDateRangeChange"
            />
          </div>
          <div class="date-filter-item">
            <span class="date-filter-label">系统交期</span>
            <el-date-picker
              v-model="systemDateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              range-separator="~"
              start-placeholder="起点"
              end-placeholder="终点"
              unlink-panels
              clearable
              style="width: 240px"
              @change="onDateRangeChange"
            />
          </div>
        </div>

        <!-- 操作组（2026-07-22：三组分类） -->
        <div class="filter-group filter-group--actions">
          <!-- 手机筛选入口（桌面走表头 popover） -->
          <el-button
            v-if="isMobile"
            :type="anyFilterActive ? 'primary' : 'default'"
            plain
            @click="openMobileFilter"
          >
            <el-icon><Filter /></el-icon>
            <span>筛选</span>
          </el-button>

          <!-- INSPECTOR 看不到导入按钮（PR-I 2026-07-20）-->
          <el-button
            v-if="!isInspector"
            @click="router.push('/parts/new?tab=pdf')"
          >
            <el-icon><Document /></el-icon>
            <span>从 PDF/Excel 批量导入</span>
          </el-button>

          <!-- 批量打印 / 批量下发 toggle（2026-07-17 打印；2026-07-22 下发；INSPECTOR 不可见；手机隐藏） -->
          <template v-if="!isInspector && !isMobile">
            <template v-if="!batchMode">
              <el-button type="success" plain @click="onEnterBatchMode">
                <el-icon><Printer /></el-icon>
                <span>批量打印图纸</span>
              </el-button>
              <el-button type="primary" plain @click="onEnterBatchDispatchMode">
                <el-icon><Promotion /></el-icon>
                <span>批量下发</span>
              </el-button>
            </template>
            <el-button v-else type="warning" @click="onExitBatchMode">
              <el-icon><Close /></el-icon>
              <span>退出批量模式</span>
            </el-button>
          </template>

          <el-tag v-if="isCncProgrammer" type="warning" effect="plain" size="small">
            编程员视图：默认查看「编程中」零件
          </el-tag>
          <span v-if="total > 0" class="total-hint">共 {{ total }} 条</span>
        </div>
      </div>
    </el-card>

    <ResponsiveList
      ref="partsListRef"
      :items="items"
      :loading="loading"
      row-key="id"
      :empty-text="emptyText"
      :card-class="(row) => (row.is_urgent ? 'rl-card--urgent' : '')"
      stripe
      border
      size="small"
      :default-sort="defaultSort"
      :row-class-name="rowClassName"
      :row-style="{ cursor: batchMode ? 'pointer' : 'default' }"
      @sort-change="onSortChange"
      @selection-change="onSelectionChange"
      @row-click="onBatchRowClick"
    >
      <el-table-column
        v-if="batchMode"
        type="selection"
        width="55"
        :reserve-selection="true"
        :selectable="isBatchSelectable"
      />

      <el-table-column
        prop="serial_no"
        label="序列号"
        width="110"
        fixed="left"
        sortable="custom"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <span :class="{ muted: !row.serial_no }">{{ row.serial_no || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column
        prop="drawing_no"
        label="图号"
        width="130"
        fixed="left"
        sortable="custom"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <el-input
            v-if="editingId === row.id"
            v-model="editBuffer.drawing_no"
            size="small"
          />
          <span v-else>{{ row.drawing_no }}</span>
        </template>
      </el-table-column>

      <el-table-column
        prop="name"
        label="名称"
        min-width="200"
        sortable="custom"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <el-input
            v-if="editingId === row.id"
            v-model="editBuffer.name"
            size="small"
          />
          <router-link v-else :to="`/parts/${row.id}`" class="name-link">
            {{ row.name }}
          </router-link>
        </template>
      </el-table-column>

      <el-table-column label="申请人" width="110" show-overflow-tooltip>
        <template #default="{ row }">
          <el-input
            v-if="editingId === row.id"
            v-model="editBuffer.applicant_name"
            size="small"
          />
          <span v-else>{{ row.applicant_name || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="数量" width="110" align="right">
        <template #default="{ row }">
          <el-input-number
            v-if="editingId === row.id"
            v-model="editBuffer.quantity"
            :min="1"
            :precision="0"
            :controls="false"
            size="small"
            style="width: 90px"
          />
          <span v-else>{{ row.quantity }}</span>
        </template>
      </el-table-column>

      <el-table-column label="单价" width="120" align="right">
        <template #default="{ row }">
          <el-input-number
            v-if="editingId === row.id"
            v-model="editBuffer.unit_price"
            :min="0"
            :precision="2"
            :step="0.01"
            :controls="false"
            size="small"
            style="width: 100px"
          />
          <span v-else>{{ row.unit_price }}</span>
        </template>
      </el-table-column>

      <el-table-column
        prop="request_date"
        label="请购日期"
        width="150"
        sortable="custom"
      >
        <template #default="{ row }">
          <el-date-picker
            v-if="editingId === row.id"
            v-model="editBuffer.request_date"
            type="date"
            value-format="YYYY-MM-DD"
            size="small"
            style="width: 138px"
            :clearable="false"
          />
          <span v-else>{{ row.request_date }}</span>
        </template>
      </el-table-column>

      <el-table-column
        prop="planned_delivery_date"
        label="计划交期"
        width="150"
        sortable="custom"
      >
        <template #default="{ row }">
          <el-date-picker
            v-if="editingId === row.id"
            v-model="editBuffer.planned_delivery_date"
            type="date"
            value-format="YYYY-MM-DD"
            size="small"
            style="width: 138px"
            :clearable="false"
          />
          <span v-else>{{ row.planned_delivery_date }}</span>
        </template>
      </el-table-column>

      <el-table-column
        prop="system_delivery_date"
        label="系统交期"
        width="150"
        sortable="custom"
      >
        <template #default="{ row }">
          <el-date-picker
            v-if="editingId === row.id"
            v-model="editBuffer.system_delivery_date"
            type="date"
            value-format="YYYY-MM-DD"
            size="small"
            style="width: 138px"
            clearable
          />
          <span v-else>{{ row.system_delivery_date || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column
        prop="order_no"
        label="订单号"
        width="130"
        sortable="custom"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <el-input
            v-if="editingId === row.id"
            v-model="editBuffer.order_no"
            size="small"
          />
          <span v-else>{{ row.order_no || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="备注" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <el-input
            v-if="editingId === row.id"
            v-model="editBuffer.note"
            size="small"
          />
          <span v-else>{{ row.note || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="加急" width="80" align="center">
        <template #default="{ row }">
          <el-switch
            v-if="editingId === row.id"
            v-model="editBuffer.is_urgent"
            size="small"
          />
          <el-tag
            v-else-if="row.is_urgent"
            type="danger"
            effect="plain"
            size="small"
          >加急</el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column
        label="状态"
        width="140"
        align="center"
      >
        <template #header>
          <span class="header-cell">
            <span>状态</span>
            <el-popover
              :width="220"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="statusPopoverVisible"
              @show="syncStatusDraft"
            >
              <template #reference>
                <el-icon
                  class="filter-icon"
                  :class="{ active: statusFilterActive }"
                >
                  <Filter />
                </el-icon>
              </template>
              <div style="margin-bottom: 6px; color: var(--text-secondary); font-size: 12px">
                多选状态 + 「仅加急」叠加加急过滤
              </div>
              <el-checkbox-group v-model="statusDraft">
                <el-checkbox
                  v-for="opt in statusOptions"
                  :key="opt.value"
                  :value="opt.value"
                  :label="opt.label"
                />
              </el-checkbox-group>
              <el-checkbox
                v-model="statusUrgentDraft"
                label="仅加急"
                style="margin-top: 8px; padding-top: 6px; border-top: 1px dashed var(--border-color-lighter)"
              />
              <div class="filter-actions">
                <el-button size="small" link @click="resetStatusDraft">重置</el-button>
                <el-button
                  size="small"
                  type="primary"
                  @click="confirmStatusFilter"
                >确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
        <template #default="{ row }">
          <el-tag
            :type="statusTagType(row.status)"
            effect="plain"
            size="small"
          >
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="客户" min-width="180" show-overflow-tooltip>
        <template #header>
          <span class="header-cell">
            <span>客户</span>
            <el-popover
              :width="280"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="customerPopoverVisible"
              @show="syncCustomerDraft"
            >
              <template #reference>
                <el-icon
                  class="filter-icon"
                  :class="{ active: search.customerId !== '' }"
                >
                  <Filter />
                </el-icon>
              </template>
              <div style="margin-bottom: 6px; color: var(--text-secondary); font-size: 12px">
                选一级客户自动级联其下二级客户
              </div>
              <el-tree-select
                v-model="customerDraft"
                :data="customerTree"
                node-key="id"
                :props="{ label: 'name', children: 'children' }"
                check-strictly
                clearable
                filterable
                placeholder="选择客户"
                :teleported="false"
                style="width: 100%"
                @clear="customerDraft = null"
              />
              <div class="filter-actions">
                <el-button size="small" link @click="resetCustomerDraft">重置</el-button>
                <el-button
                  size="small"
                  type="primary"
                  @click="confirmCustomerFilter"
                >确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
        <template #default="{ row }">
          <span v-if="row.customer_path">{{ row.customer_path }}</span>
          <span v-else-if="row.customer_name" class="muted">{{ row.customer_name }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column label="所在位置" width="150" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.location === 'PRODUCTION_SHELF' && row.shelf_code">
            货架 {{ row.shelf_code }}
          </span>
          <span v-else-if="row.location === 'INSPECTION_SHELF' && row.shelf_code">
            品检 {{ row.shelf_code }}
          </span>
          <span v-else-if="row.location === 'WORKER' && row.worker_name">
            {{ row.worker_name }}
          </span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <template v-if="editingId === row.id">
            <el-button
              link
              type="primary"
              size="small"
              :loading="savingEdit"
              @click="saveEdit(row as PartListItem)"
            >保存</el-button>
            <el-button link size="small" @click="cancelEdit">取消</el-button>
          </template>
          <template v-else>
            <el-button link type="primary" size="small" @click="$router.push(`/parts/${row.id}`)">详情</el-button>
            <el-button
              v-if="canEdit"
              link
              type="warning"
              size="small"
              @click="startEdit(row as PartListItem)"
            >编辑</el-button>
            <el-button
              v-if="!isInspector && row.status === 'PENDING'"
              link
              type="success"
              size="small"
              @click="onDispatch(row as PartListItem)"
            >下发</el-button>
          </template>
        </template>
      </el-table-column>

      <!-- 手机卡片：关键字段 + 操作按钮 -->
      <template #card="{ row }">
        <div class="rl-card-head">
          <router-link :to="`/parts/${row.id}`" class="rl-card-title name-link">
            {{ row.name }}
          </router-link>
          <el-tag :type="statusTagType(row.status)" effect="plain" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </div>
        <div class="rl-card-sub">
          图号 {{ row.drawing_no || '—' }} · 序列号 {{ row.serial_no || '—' }}
        </div>
        <div class="rl-kv">
          <div class="rl-kv__item">
            <span class="rl-kv__key">数量</span>
            <span class="rl-kv__val">{{ row.quantity }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">计划交期</span>
            <span class="rl-kv__val">{{ row.planned_delivery_date || '—' }}</span>
          </div>
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">客户</span>
            <span class="rl-kv__val">{{ row.customer_path || row.customer_name || '—' }}</span>
          </div>
          <div class="rl-kv__item rl-kv__item--full">
            <span class="rl-kv__key">所在位置</span>
            <span class="rl-kv__val">{{ locationText(row) }}</span>
          </div>
        </div>
        <div class="rl-card-actions">
          <el-button link type="primary" size="small" @click="router.push(`/parts/${row.id}`)">详情</el-button>
          <el-button
            v-if="canEdit"
            link
            type="warning"
            size="small"
            @click="startEdit(row as PartListItem)"
          >编辑</el-button>
          <el-button
            v-if="!isInspector && row.status === 'PENDING'"
            link
            type="success"
            size="small"
            @click="onDispatch(row as PartListItem)"
          >下发</el-button>
        </div>
      </template>
    </ResponsiveList>

    <!-- 批量打印 / 批量下发 — 底部 action bar（2026-07-17 打印；2026-07-22 下发；INSPECTOR 不可见） -->
    <div v-if="!isInspector && batchMode" class="batch-bar">
      <div class="bar-info">
        <span>已选 <strong>{{ selectedIds.size }}</strong> 件</span>
        <el-button link size="small" @click="onSelectAllPage">全选当前页</el-button>
        <el-button link size="small" @click="onClearSelection">清空选择</el-button>
      </div>
      <el-button
        v-if="batchAction === 'print'"
        type="primary"
        :loading="batchPrinting"
        :disabled="selectedIds.size === 0"
        @click="onBatchPrint"
      >
        <el-icon><Printer /></el-icon>
        <span>打印预览（{{ selectedIds.size }} 件）</span>
      </el-button>
      <el-button
        v-else
        type="primary"
        :disabled="selectedIds.size === 0"
        @click="onOpenBatchDispatch"
      >
        <el-icon><Promotion /></el-icon>
        <span>批量下发（{{ selectedIds.size }} 件）</span>
      </el-button>
    </div>

    <!-- 隐藏 iframe：批量打印用（仿 FileListCard.vue 的 print 实现） -->
    <iframe
      ref="batchPrintIframeRef"
      style="position: fixed; right: 0; bottom: 0; width: 1px; height: 1px; border: 0; opacity: 0; pointer-events: none;"
      title="批量打印预览"
    />

    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        :layout="paginationLayout"
        :pager-count="isMobile ? 5 : 7"
        background
        size="small"
        @current-change="fetchList"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- 下发对话框（沿用旧 PartsList 的下发流程） -->
    <el-dialog
      v-model="dispatchVisible"
      :title="dispatchMode === 'cnc' ? '发送至 CNC 编程' : '下发零件'"
      :width="dispatchDlg.width.value"
      :top="dispatchDlg.top.value"
      @closed="onDispatchClosed"
    >
      <el-form label-width="96px">
        <el-form-item label="下发方式">
          <el-radio-group v-model="dispatchMode">
            <el-radio value="direct">直接下到生产货架</el-radio>
            <el-radio value="cnc">发送至 CNC 编程</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="dispatchMode === 'direct'">
          <!-- 2026-07-21：先选下一道工序，再选目标货架；货架候选按映射过滤 -->
          <el-form-item label="下一道工序" required>
            <el-select
              v-model="dispatchNextProcessId"
              placeholder="请先选择下一道工序"
              style="width: 100%"
              filterable
              clearable
            >
              <el-option
                v-for="p in filteredProcesses"
                :key="p.id"
                :label="`${p.code} / ${p.name}`"
                :value="p.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="目标货架" required>
            <el-select
              v-model="dispatchShelfId"
              placeholder="先选工序；货架候选按映射过滤"
              style="width: 100%"
              filterable
              clearable
              :disabled="!dispatchNextProcessId"
            >
              <el-option
                v-for="s in filteredShelves"
                :key="s.id"
                :label="s.name"
                :value="s.id"
              />
              <template #empty>
                <span class="muted">
                  {{
                    dispatchNextProcessId
                      ? '当前工序未映射到任何生产货架，请先在「货架管理 → 工序映射」配置'
                      : '请先选择下一道工序'
                  }}
                </span>
              </template>
            </el-select>
          </el-form-item>
        </template>
        <el-form-item v-else>
          <el-alert
            type="info"
            :closable="false"
            title="将零件发送至 CNC 编程环节，零件状态变为「编程中」。"
            description="CNC 编程员在「待编程一览」中下载图纸、上传 G 代码后，会再下发到生产货架。"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dispatchVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="dispatchSubmitting"
          :disabled="dispatchMode === 'direct' && (!dispatchShelfId || !dispatchNextProcessId)"
          @click="onDispatchConfirm"
        >
          {{ dispatchMode === 'cnc' ? '发送至 CNC 编程' : '确认下发' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 批量下发对话框（2026-07-22）：下生产货架 / 发编程 两动作；状态独立于单件下发 -->
    <el-dialog
      v-model="batchDispatchVisible"
      title="批量下发"
      :width="dispatchDlg.width.value"
      :top="dispatchDlg.top.value"
      destroy-on-close
    >
      <el-form label-width="96px">
        <el-form-item label="下发方式">
          <el-radio-group v-model="batchDispatchAction">
            <el-radio-button value="shelf">下生产货架</el-radio-button>
            <el-radio-button value="programming">发编程</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="batchDispatchAction === 'shelf'">
          <el-form-item label="下一道工序" required>
            <el-select
              v-model="batchDispatchNextProcessId"
              placeholder="请先选择下一道工序"
              style="width: 100%"
              filterable
              clearable
            >
              <el-option
                v-for="p in batchFilteredProcesses"
                :key="p.id"
                :label="`${p.code} / ${p.name}`"
                :value="p.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="目标货架" required>
            <el-select
              v-model="batchDispatchShelfId"
              placeholder="先选工序；货架候选按映射过滤"
              style="width: 100%"
              filterable
              clearable
              :disabled="!batchDispatchNextProcessId"
            >
              <el-option
                v-for="s in batchFilteredShelves"
                :key="s.id"
                :label="s.name"
                :value="s.id"
              />
              <template #empty>
                <span class="muted">
                  {{
                    batchDispatchNextProcessId
                      ? '当前工序未映射到任何生产货架，请先在「货架管理 → 工序映射」配置'
                      : '请先选择下一道工序'
                  }}
                </span>
              </template>
            </el-select>
          </el-form-item>
        </template>
        <el-alert
          v-else
          type="info"
          :closable="false"
          title="将所选零件发送至 CNC 编程环节，零件状态变为「编程中」。"
          description="CNC 编程员在「待编程一览」中下载图纸、上传 G 代码后，会再下发到生产货架。"
        />
        <el-form-item>
          <span class="muted">已选 <strong>{{ selectedIds.size }}</strong> 件 PENDING 零件将执行此操作</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchDispatchVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="batchDispatchSubmitting"
          :disabled="
            batchDispatchAction === 'shelf'
            && (!batchDispatchShelfId || !batchDispatchNextProcessId)
          "
          @click="onBatchDispatchConfirm"
        >
          确认
        </el-button>
      </template>
    </el-dialog>

    <!-- 手机筛选抽屉：承载桌面表头 popover 的同款筛选（状态 + 加急 + 客户） -->
    <el-drawer
      v-model="mobileFilterOpen"
      title="筛选"
      direction="btt"
      size="72%"
    >
      <div class="mobile-filter">
        <div class="mf-section">
          <div class="mf-label">状态</div>
          <el-checkbox-group v-model="statusDraft" class="mf-status">
            <el-checkbox
              v-for="opt in statusOptions"
              :key="opt.value"
              :value="opt.value"
              :label="opt.label"
            />
          </el-checkbox-group>
          <el-checkbox v-model="statusUrgentDraft" label="仅加急" class="mf-urgent" />
        </div>
        <div class="mf-section">
          <div class="mf-label">客户</div>
          <el-tree-select
            v-model="customerDraft"
            :data="customerTree"
            node-key="id"
            :props="{ label: 'name', children: 'children' }"
            check-strictly
            clearable
            filterable
            placeholder="选择客户"
            style="width: 100%"
            @clear="customerDraft = null"
          />
        </div>
      </div>
      <template #footer>
        <el-button @click="resetMobileFilter">重置</el-button>
        <el-button type="primary" @click="confirmMobileFilter">确定</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Close,
  Document,
  Filter,
  Printer,
  Promotion,
  RefreshLeft,
  Search,
} from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'
import {
  listParts,
  placeOnShelf,
  printPartDrawingBatch,
  sendToProgramming,
  updatePart,
  type ListPartsParams,
  type PartUpdatePayload,
} from '@/api/parts'
import type { PartListItem, PartSortKey, SortDir } from '@/types/parts'
import { listShelves } from '@/api/shelves'
import type { Shelf } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import type { Process } from '@/types/process'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  PART_SORT_PROP_MAP,
  type OrderStatus,
} from '@/types/parts'
import { useAuthSession } from '@/composables/useAuthSession'
import { usePermissions } from '@/composables/usePermissions'
import { useCustomerTree } from '@/composables/useCustomerTree'
import { useListFilterPersist } from '@/composables/useListFilterPersist'

// ============ 角色 & 默认筛选 ============
const { hasRole } = useAuthSession()
const isCncProgrammer = hasRole('CNC_PROGRAMMER')
// PR-I 2026-07-20：INSPECTOR 看不到导入 / 批量打印 / 下发按钮
const { isInspector } = usePermissions()
// 行内编辑权限：与后端 POST /parts/{id}/update 一致（MANAGER / CLERK）
const canEdit = hasRole('MANAGER') || hasRole('CLERK')
const { tree: customerTree } = useCustomerTree()
const route = useRoute()
const router = useRouter()
const { isMobile } = useBreakpoint()

interface SearchState {
  keyword: string
  orderNo: string
  statuses: OrderStatus[]
  isUrgent: boolean | null
  customerId: string
  /** 2026-07-21 PR-F：请购日期区间（含端点；空串=无限制） */
  requestDateFrom: string
  requestDateTo: string
  /** 2026-07-22：计划交期区间（含端点；空串=无限制） */
  plannedDeliveryDateFrom: string
  plannedDeliveryDateTo: string
  /** 2026-07-21 PR-F：系统交期区间（含端点；空串=无限制） */
  systemDeliveryDateFrom: string
  systemDeliveryDateTo: string
}
function initialSearch(): SearchState {
  return {
    keyword: '',
    orderNo: '',
    statuses: isCncProgrammer
      ? ['PROGRAMMING']
      : ['IN_PROCESS', 'REPAIRING'],
    isUrgent: null,
    customerId: '',
    requestDateFrom: '',
    requestDateTo: '',
    plannedDeliveryDateFrom: '',
    plannedDeliveryDateTo: '',
    systemDeliveryDateFrom: '',
    systemDeliveryDateTo: '',
  }
}
const search = reactive<SearchState>(initialSearch())

const statusOptions: { value: OrderStatus; label: string }[] = (
  Object.keys(ORDER_STATUS_LABEL) as OrderStatus[]
).map((v) => ({ value: v, label: ORDER_STATUS_LABEL[v] }))

const statusFilterActive = computed(
  () => search.statuses.length > 0 || search.isUrgent === true,
)
const customerFilterActive = computed(() => search.customerId !== '')

// ============ 三个日期区间筛选（2026-07-22：内联 daterange） ============
// daterange 的 v-model 绑定 [start, end]；清空时 el 抛 null，getter/setter 兜底。
type DateRange = [string, string] | null
type DateRangeKey =
  | 'requestDateFrom'
  | 'requestDateTo'
  | 'plannedDeliveryDateFrom'
  | 'plannedDeliveryDateTo'
  | 'systemDeliveryDateFrom'
  | 'systemDeliveryDateTo'

function makeRangeModel(fromKey: DateRangeKey, toKey: DateRangeKey) {
  return computed<DateRange>({
    get: () =>
      search[fromKey] || search[toKey]
        ? ([search[fromKey], search[toKey]] as [string, string])
        : null,
    set: (val: DateRange) => {
      search[fromKey] = val?.[0] ?? ''
      search[toKey] = val?.[1] ?? ''
    },
  })
}

const requestDateRange = makeRangeModel('requestDateFrom', 'requestDateTo')
const plannedDateRange = makeRangeModel(
  'plannedDeliveryDateFrom',
  'plannedDeliveryDateTo',
)
const systemDateRange = makeRangeModel(
  'systemDeliveryDateFrom',
  'systemDeliveryDateTo',
)

function onDateRangeChange(): void {
  page.value = 1
  void fetchList()
}

// ============ 状态列头 popover（draft + 确定/重置） ============
// draft 完全用 OrderStatus 类型（用 string 存「仅加急」标记已删除）；
// 加急选项是独立 checkbox，不再混入 status 多选。
const statusPopoverVisible = ref(false)
const statusDraft = ref<OrderStatus[]>([])
const statusUrgentDraft = ref(false)

function syncStatusDraft(): void {
  statusDraft.value = [...search.statuses]
  statusUrgentDraft.value = search.isUrgent === true
}

function resetStatusDraft(): void {
  statusDraft.value = []
  statusUrgentDraft.value = false
  search.statuses = []
  search.isUrgent = null
  statusPopoverVisible.value = false
  onSearch()
}

function confirmStatusFilter(): void {
  // 只装纯 OrderStatus 与 boolean，绝不混入 marker
  search.statuses = [...statusDraft.value]
  search.isUrgent = statusUrgentDraft.value ? true : null
  statusPopoverVisible.value = false
  onSearch()
}

// ============ 客户列头 popover（draft + 确定/重置） ============
const customerPopoverVisible = ref(false)
const customerDraft = ref<string | null>(null)

function syncCustomerDraft(): void {
  customerDraft.value = search.customerId || null
}

function resetCustomerDraft(): void {
  customerDraft.value = null
  search.customerId = ''
  customerPopoverVisible.value = false
  onSearch()
}

function confirmCustomerFilter(): void {
  search.customerId = customerDraft.value ?? ''
  customerPopoverVisible.value = false
  onSearch()
}

// ============ 手机筛选抽屉 ============
const mobileFilterOpen = ref(false)
const anyFilterActive = computed(() => statusFilterActive.value || customerFilterActive.value)

function openMobileFilter(): void {
  syncStatusDraft()
  syncCustomerDraft()
  mobileFilterOpen.value = true
}
function confirmMobileFilter(): void {
  search.statuses = [...statusDraft.value]
  search.isUrgent = statusUrgentDraft.value ? true : null
  search.customerId = customerDraft.value ?? ''
  mobileFilterOpen.value = false
  onSearch()
}
function resetMobileFilter(): void {
  statusDraft.value = []
  statusUrgentDraft.value = false
  customerDraft.value = null
  search.statuses = []
  search.isUrgent = null
  search.customerId = ''
  mobileFilterOpen.value = false
  onSearch()
}

// 手机卡片「所在位置」文案（与桌面列同款逻辑）
function locationText(row: PartListItem): string {
  if (row.location === 'PRODUCTION_SHELF' && row.shelf_code) return `货架 ${row.shelf_code}`
  if (row.location === 'INSPECTION_SHELF' && row.shelf_code) return `品检 ${row.shelf_code}`
  if (row.location === 'WORKER' && row.worker_name) return row.worker_name
  return '—'
}

// ============ 表格 / 排序 ============
const items = ref<PartListItem[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const page = ref(1)
const pageSize = ref(20)
const sortBy = ref<PartSortKey>('PLANNED_DELIVERY_DATE')
const sortDir = ref<SortDir>('ASC')

// ============ 批量打印 / 批量下发（2026-07-22 共享批量模式）============
// 2026-07-22：拆为 batchAction（'print' | 'dispatch'）两个动作；共享 batchMode、selectedIds、
// selectedRows、row-click 切换、PENDING 守卫（isBatchSelectable）。跨页选择由 selectedIds
// 维护真实状态，selectedRows 仅做当前页镜像 + 跨页已选行快照。
const batchMode = ref(false)
const batchAction = ref<'print' | 'dispatch'>('print')
const selectedRows = ref<PartListItem[]>([])
/** 跨页选择真实状态来源：所有已选 PENDING 行的 id（含非当前页）。
 * 2026-07-22 修复：必须用 reactive 包一层，否则模板里的 .size 不响应，count 永远 0、按钮永远 disabled。 */
const selectedIds = reactive(new Set<string>())
const batchPrinting = ref(false)
const batchPrintIframeRef = ref<HTMLIFrameElement | null>(null)
let batchPrintBlobUrl = ''
/** ResponsiveList 内 el-table ref；用于 row-click 切换 / 全选 / 清空时同步 UI */
const partsListRef = ref<InstanceType<typeof ResponsiveList> | null>(null)

function isBatchSelectable(row: PartListItem): boolean {
  return row.status === 'PENDING'
}

function clearAllSelection(): void {
  selectedIds.clear()
  selectedRows.value = []
  partsListRef.value?.elTableRef?.clearSelection()
}

function onEnterBatchMode(): void {
  batchAction.value = 'print'
  batchMode.value = true
  clearAllSelection()
}
function onEnterBatchDispatchMode(): void {
  batchAction.value = 'dispatch'
  batchMode.value = true
  clearAllSelection()
}
function onExitBatchMode(): void {
  batchMode.value = false
  clearAllSelection()
}
function onSelectionChange(rows: PartListItem[]): void {
  // 按 ID 合并：先移除当前页所有 ID（不论是否还在 rows 中），再加入 rows 中 PENDING 行的 ID
  const currentPageIds = new Set(items.value.map((r) => r.id))
  for (const id of [...selectedIds]) {
    if (currentPageIds.has(id)) selectedIds.delete(id)
  }
  for (const r of rows) {
    if (isBatchSelectable(r)) selectedIds.add(r.id)
  }
  rebuildSelectedRows(rows)
}
function onSelectAllPage(): void {
  // 只勾选当前页的 PENDING 行；非 PENDING 不参与
  const table = partsListRef.value?.elTableRef
  if (!table) return
  for (const row of items.value) {
    if (isBatchSelectable(row)) {
      table.toggleRowSelection(row, true)
      selectedIds.add(row.id)
    }
  }
  rebuildSelectedRows(items.value)
}
function onClearSelection(): void {
  clearAllSelection()
}
/** 重新构建 selectedRows：当前页用最新 row 对象，其他页保留既有快照。 */
function rebuildSelectedRows(currentPageRows: PartListItem[]): void {
  const pageMap = new Map(currentPageRows.map((r) => [r.id, r]))
  const next: PartListItem[] = []
  const seen = new Set<string>()
  for (const id of selectedIds) {
    const fromPage = pageMap.get(id)
    if (fromPage) {
      next.push(fromPage)
    } else {
      const fromSnapshot = selectedRows.value.find((r) => r.id === id)
      if (fromSnapshot) next.push(fromSnapshot)
    }
    seen.add(id)
  }
  selectedRows.value = next
}
function onBatchRowClick(
  row: PartListItem,
  _column: unknown,
  _event: MouseEvent,
): void {
  // 非批量模式 / 不可选行不响应
  if (!batchMode.value) return
  if (!isBatchSelectable(row)) return
  const table = partsListRef.value?.elTableRef
  if (!table) return
  const shouldSelect = !selectedIds.has(row.id)
  table.toggleRowSelection(row, shouldSelect)
  // toggleRowSelection 不会同步触发 @selection-change（在已保留勾选状态下切换时
  // 视实现可能不触发），所以这里手动维护 selectedIds/selectedRows。
  if (shouldSelect) {
    selectedIds.add(row.id)
    if (!selectedRows.value.find((r) => r.id === row.id)) {
      selectedRows.value = [...selectedRows.value, row]
    }
  } else {
    selectedIds.delete(row.id)
    selectedRows.value = selectedRows.value.filter((r) => r.id !== row.id)
  }
}
/** fetchList 更新 items 后用 nextTick 恢复当前页 checkbox UI（不主动清空 selectedIds）。 */
function restoreTableSelection(): void {
  if (!batchMode.value) return
  const table = partsListRef.value?.elTableRef
  if (!table) return
  // 清理：移除 selectedIds 中已不在当前 items 中或已变非 PENDING 的 id
  const currentIds = new Set(items.value.map((r) => r.id))
  for (const id of [...selectedIds]) {
    if (!currentIds.has(id)) selectedIds.delete(id)
  }
  for (const r of items.value) {
    if (!isBatchSelectable(r)) selectedIds.delete(r.id)
  }
  rebuildSelectedRows(items.value)
  nextTick(() => {
    if (!partsListRef.value?.elTableRef) return
    partsListRef.value.elTableRef.clearSelection()
    for (const row of items.value) {
      if (selectedIds.has(row.id)) {
        partsListRef.value.elTableRef.toggleRowSelection(row, true)
      }
    }
  })
}

async function onBatchPrint(): Promise<void> {
  if (selectedRows.value.length === 0) return
  batchPrinting.value = true
  try {
    const ids = selectedRows.value.map((r) => r.id)
    const blob = await printPartDrawingBatch(ids)
    if (batchPrintBlobUrl) URL.revokeObjectURL(batchPrintBlobUrl)
    batchPrintBlobUrl = URL.createObjectURL(blob)
    const iframe = batchPrintIframeRef.value
    if (!iframe) {
      ElMessage.error('打印 iframe 未挂载，请刷新页面后重试')
      return
    }
    iframe.src = batchPrintBlobUrl
    iframe.onload = () => {
      try {
        iframe.contentWindow?.focus()
        iframe.contentWindow?.print()
      } catch {
        // sandbox / cross-origin 等极端情况下 fallback 到新窗口打印
        const w = window.open(batchPrintBlobUrl, '_blank')
        if (w) w.print()
      }
    }
  } catch (e) {
    ElMessage.error((e as Error).message ?? '批量打印失败')
  } finally {
    setTimeout(() => { batchPrinting.value = false }, 800)
  }
}

onBeforeUnmount(() => {
  if (batchPrintBlobUrl) {
    URL.revokeObjectURL(batchPrintBlobUrl)
    batchPrintBlobUrl = ''
  }
})

const SORT_PROP_MAP: Record<string, PartSortKey> = PART_SORT_PROP_MAP

type SortOrder = 'ascending' | 'descending'
const defaultSort = computed<{ prop: string; order: SortOrder }>(() => ({
  prop: 'planned_delivery_date',
  order: sortDir.value === 'ASC' ? 'ascending' : 'descending',
}))

const emptyText = computed(() => errorMsg.value ?? '暂无符合条件的零件')

// 手机上分页收窄为 prev/pager/next，桌面保留完整布局
const paginationLayout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
)

function statusLabel(s: OrderStatus): string {
  return ORDER_STATUS_LABEL[s] ?? s
}
function statusTagType(s: OrderStatus): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return ORDER_STATUS_TAG_TYPE[s] ?? 'info'
}

function rowClassName({ row }: { row: PartListItem }): string {
  if (row.is_urgent) return 'row-urgent'
  // PR-G 2026-07-22：已开具送货单（且尚未归档）的零件行用浅蓝染色；
  // DELIVERED / COMPLETED / CANCELLED 后 delivery_note_id 被 service 置 NULL，颜色自然消失。
  if (row.delivery_note_id && row.status !== 'DELIVERED' && row.status !== 'COMPLETED') {
    return 'row-on-delivery-note'
  }
  return ''
}

function buildParams(): ListPartsParams {
  return {
    customer_id: search.customerId || undefined,
    statuses: search.statuses.length > 0 ? search.statuses : undefined,
    is_urgent: search.isUrgent ?? undefined,
    keyword: search.keyword.trim() || undefined,
    order_no: search.orderNo.trim() || undefined,
    request_date_from: search.requestDateFrom || undefined,
    request_date_to: search.requestDateTo || undefined,
    planned_delivery_date_from: search.plannedDeliveryDateFrom || undefined,
    planned_delivery_date_to: search.plannedDeliveryDateTo || undefined,
    system_delivery_date_from: search.systemDeliveryDateFrom || undefined,
    system_delivery_date_to: search.systemDeliveryDateTo || undefined,
    sort_by: sortBy.value,
    sort_dir: sortDir.value,
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
  }
}

async function fetchList(): Promise<void> {
  loading.value = true
  errorMsg.value = null
  try {
    const resp = await listParts(buildParams())
    items.value = resp.items
    total.value = resp.total
    // 批量模式下：剔除已不在当前页的失效勾选 + 恢复 UI（2026-07-22 跨页持久化）
    if (batchMode.value) {
      restoreTableSelection()
    }
  } catch (e) {
    items.value = []
    total.value = 0
    errorMsg.value = (e as Error).message ?? '查询失败'
    ElMessage.error(errorMsg.value)
  } finally {
    loading.value = false
  }
}

const onSearch = (): void => {
  page.value = 1
  void fetchList()
}

function onSortChange({
  prop,
  order,
}: {
  prop: string | null
  order: 'ascending' | 'descending' | null
}): void {
  if (!prop || !order) return
  sortBy.value = SORT_PROP_MAP[prop] ?? 'PLANNED_DELIVERY_DATE'
  sortDir.value = order === 'ascending' ? 'ASC' : 'DESC'
  void fetchList()
}

function onPageSizeChange(size: number): void {
  pageSize.value = size
  page.value = 1
  void fetchList()
}

// ============ 筛选状态持久化（PR-I 2026-07-20）============
const { restore: restorePartsFilter, clear: clearPartsFilter } =
  useListFilterPersist<SearchState>(
    'parts_list_filter',
    { search, sortBy, sortDir, pageSize },
  )

function onReset(): void {
  Object.assign(search, initialSearch())
  sortBy.value = 'PLANNED_DELIVERY_DATE'
  sortDir.value = 'ASC'
  page.value = 1
  clearPartsFilter()
  void fetchList()
}

onMounted(() => {
  // 1) 优先尝试从 URL ?status=PENDING 注入（与批量新建后跳转保持一致）
  const q = route.query.status
  if (typeof q === 'string' && q in ORDER_STATUS_LABEL) {
    search.statuses = [q as OrderStatus]
  } else {
    // 2) 否则从 localStorage 恢复上次的筛选 / 排序 / 分页大小
    const persisted = restorePartsFilter()
    if (persisted) {
      search.keyword = persisted.search.keyword ?? search.keyword
      search.orderNo = persisted.search.orderNo ?? search.orderNo
      search.statuses = Array.isArray(persisted.search.statuses)
        ? persisted.search.statuses
        : search.statuses
      search.isUrgent = persisted.search.isUrgent ?? search.isUrgent
      search.customerId = persisted.search.customerId ?? search.customerId
      search.requestDateFrom =
        persisted.search.requestDateFrom ?? search.requestDateFrom
      search.requestDateTo =
        persisted.search.requestDateTo ?? search.requestDateTo
      search.plannedDeliveryDateFrom =
        persisted.search.plannedDeliveryDateFrom ?? search.plannedDeliveryDateFrom
      search.plannedDeliveryDateTo =
        persisted.search.plannedDeliveryDateTo ?? search.plannedDeliveryDateTo
      search.systemDeliveryDateFrom =
        persisted.search.systemDeliveryDateFrom ?? search.systemDeliveryDateFrom
      search.systemDeliveryDateTo =
        persisted.search.systemDeliveryDateTo ?? search.systemDeliveryDateTo
      // localStorage 存的是 string，恢复时按合法值收敛（默认值兜底）
      sortBy.value = (SORT_PROP_MAP[persisted.sortBy]
        ? persisted.sortBy as PartSortKey
        : 'PLANNED_DELIVERY_DATE')
      sortDir.value = (persisted.sortDir === 'ASC' || persisted.sortDir === 'DESC'
        ? persisted.sortDir as SortDir
        : 'ASC')
      pageSize.value = persisted.pageSize
    }
  }
  void fetchList()
})

// ============ 行内编辑（2026-07-20）============
// editBuffer 是纯前端本地态：只在点「保存」时才发请求写库，
// 因此一个文员编辑不会影响另一个文员看到的列表数据。
interface EditBuffer {
  name: string
  drawing_no: string
  applicant_name: string
  quantity: number
  unit_price: number
  request_date: string
  planned_delivery_date: string
  system_delivery_date: string | null
  order_no: string | null
  note: string | null
  is_urgent: boolean
}
const editingId = ref<string | null>(null)
const savingEdit = ref(false)
const editBuffer = reactive<EditBuffer>({
  name: '',
  drawing_no: '',
  applicant_name: '',
  quantity: 1,
  unit_price: 0,
  request_date: '',
  planned_delivery_date: '',
  system_delivery_date: null,
  order_no: null,
  note: null,
  is_urgent: false,
})

function startEdit(row: PartListItem): void {
  if (editingId.value && editingId.value !== row.id) {
    ElMessage.warning('请先保存或取消当前正在编辑的行')
    return
  }
  editBuffer.name = row.name
  editBuffer.drawing_no = row.drawing_no
  editBuffer.applicant_name = row.applicant_name ?? ''
  editBuffer.quantity = row.quantity
  editBuffer.unit_price = row.unit_price
  editBuffer.request_date = row.request_date
  editBuffer.planned_delivery_date = row.planned_delivery_date
  editBuffer.system_delivery_date = row.system_delivery_date
  editBuffer.order_no = row.order_no
  editBuffer.note = row.note
  editBuffer.is_urgent = row.is_urgent
  editingId.value = row.id
}

function cancelEdit(): void {
  editingId.value = null
}

async function saveEdit(row: PartListItem): Promise<void> {
  const name = editBuffer.name.trim()
  const drawingNo = editBuffer.drawing_no.trim()
  if (!name) { ElMessage.warning('名称不能为空'); return }
  if (!drawingNo) { ElMessage.warning('图号不能为空'); return }
  if (!editBuffer.request_date) { ElMessage.warning('请购日期不能为空'); return }
  if (!editBuffer.planned_delivery_date) { ElMessage.warning('计划交期不能为空'); return }
  if (editBuffer.quantity == null || editBuffer.quantity < 1) {
    ElMessage.warning('数量必须 ≥ 1'); return
  }
  savingEdit.value = true
  try {
    const payload: PartUpdatePayload = {
      name,
      drawing_no: drawingNo,
      applicant_name: editBuffer.applicant_name.trim(),
      quantity: editBuffer.quantity,
      unit_price: editBuffer.unit_price,
      request_date: editBuffer.request_date,
      planned_delivery_date: editBuffer.planned_delivery_date,
      system_delivery_date: editBuffer.system_delivery_date || null,
      order_no: editBuffer.order_no || null,
      note: editBuffer.note || null,
      is_urgent: editBuffer.is_urgent,
    }
    await updatePart(row.id, payload)
    // updatePart 返回 PartOut（不含 applicant_name/request_date/unit_price），
    // 用已知的 buffer 值就地回填该行，避免整表刷新的闪烁。
    Object.assign(row, {
      name,
      drawing_no: drawingNo,
      applicant_name: payload.applicant_name,
      quantity: payload.quantity,
      unit_price: payload.unit_price,
      request_date: payload.request_date,
      planned_delivery_date: payload.planned_delivery_date,
      system_delivery_date: payload.system_delivery_date ?? null,
      order_no: payload.order_no ?? null,
      note: payload.note ?? null,
      is_urgent: payload.is_urgent,
    })
    editingId.value = null
    ElMessage.success('保存成功')
  } catch (e) {
    // 40901 = BIZ_VERSION_CONFLICT（乐观锁冲突）
    if ((e as { code?: number }).code === 40901) {
      ElMessage.warning('该零件已被他人修改，已为你刷新列表')
      editingId.value = null
      void fetchList()
    } else {
      ElMessage.error((e as Error).message ?? '保存失败')
    }
  } finally {
    savingEdit.value = false
  }
}


// ============ 下发对话框 ============
const shelves = ref<Shelf[]>([])
const processes = ref<Process[]>([])
const dispatchVisible = ref(false)
const dispatchDlg = useDialogSize({ desktopWidth: 480 })
const dispatchShelfId = ref<string | null>(null)
const dispatchNextProcessId = ref<string | null>(null)
const dispatchPartId = ref<string | null>(null)
const dispatchSubmitting = ref(false)
const dispatchMode = ref<'direct' | 'cnc'>('direct')
// 2026-07-17：useShelfProcessFilter 双向收窄货架/工序下拉
const {
  filteredShelves,
  filteredProcesses,
  load: loadShelfProcessMap,
} = useShelfProcessFilter(
  shelves,
  processes,
  dispatchShelfId,
  dispatchNextProcessId,
)

async function onDispatch(row: PartListItem): Promise<void> {
  dispatchPartId.value = row.id
  dispatchShelfId.value = null
  dispatchNextProcessId.value = null
  dispatchMode.value = 'direct'
  try {
    const [shelfResp, procResp] = await Promise.all([
      listShelves({ zone: 'PRODUCTION', is_active: true, limit: 200 }),
      listProcesses({ limit: 200 }),
    ])
    shelves.value = shelfResp.items
    processes.value = procResp.items
    // 2026-07-17：弹窗打开后异步加载映射（不阻塞 dialog 出现）
    void loadShelfProcessMap()
  } catch {
    shelves.value = []
    processes.value = []
  }
  dispatchVisible.value = true
}

function onDispatchClosed(): void {
  dispatchPartId.value = null
  dispatchShelfId.value = null
  dispatchNextProcessId.value = null
  dispatchMode.value = 'direct'
}

async function onDispatchConfirm(): Promise<void> {
  if (!dispatchPartId.value) return
  if (dispatchMode.value === 'direct'
      && (!dispatchShelfId.value || !dispatchNextProcessId.value)) return
  dispatchSubmitting.value = true
  try {
    if (dispatchMode.value === 'cnc') {
      await sendToProgramming(dispatchPartId.value)
      ElMessage.success('已发送至 CNC 编程')
    } else {
      await placeOnShelf(
        dispatchPartId.value, dispatchShelfId.value!, dispatchNextProcessId.value!,
      )
      ElMessage.success('下发成功')
    }
    dispatchVisible.value = false
    void fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '下发失败')
  } finally {
    dispatchSubmitting.value = false
  }
}

// ============ 批量下发对话框（2026-07-22）============
// 状态完全独立于单件下发（dispatchShelfId / dispatchNextProcessId），避免互相踩。
const batchDispatchVisible = ref(false)
const batchDispatchAction = ref<'shelf' | 'programming'>('shelf')
const batchDispatchShelfId = ref<string | null>(null)
const batchDispatchNextProcessId = ref<string | null>(null)
const batchDispatchSubmitting = ref(false)
const {
  filteredShelves: batchFilteredShelves,
  filteredProcesses: batchFilteredProcesses,
  load: loadBatchShelfProcessMap,
} = useShelfProcessFilter(
  shelves,
  processes,
  batchDispatchShelfId,
  batchDispatchNextProcessId,
)

async function onOpenBatchDispatch(): Promise<void> {
  if (selectedIds.size === 0) {
    ElMessage.warning('请先选择待下发零件')
    return
  }
  batchDispatchAction.value = 'shelf'
  batchDispatchShelfId.value = null
  batchDispatchNextProcessId.value = null
  // 货架/工序数据复用模块级缓存，按需首次加载
  if (shelves.value.length === 0) {
    try {
      shelves.value = (await listShelves({
        zone: 'PRODUCTION', is_active: true, limit: 200,
      })).items
    } catch { shelves.value = [] }
  }
  if (processes.value.length === 0) {
    try {
      processes.value = (await listProcesses({ limit: 200 })).items
    } catch { processes.value = [] }
  }
  void loadBatchShelfProcessMap()
  batchDispatchVisible.value = true
}

async function onBatchDispatchConfirm(): Promise<void> {
  if (selectedIds.size === 0) return
  if (batchDispatchAction.value === 'shelf'
      && (!batchDispatchShelfId.value || !batchDispatchNextProcessId.value)) return
  // 快照：迭代过程中会修改 selectedIds/selectedRows
  const targets = selectedRows.value
    .filter((r) => selectedIds.has(r.id))
    .map((r) => ({ id: r.id, label: r.serial_no || r.drawing_no || r.id }))
  if (targets.length === 0) {
    ElMessage.warning('当前页没有已选零件，请翻到已选页或重新选择')
    return
  }

  const failures: { label: string; message: string }[] = []
  let successCount = 0
  batchDispatchSubmitting.value = true
  try {
    for (const t of targets) {
      try {
        if (batchDispatchAction.value === 'programming') {
          await sendToProgramming(t.id)
        } else {
          await placeOnShelf(
            t.id, batchDispatchShelfId.value!, batchDispatchNextProcessId.value!,
          )
        }
        successCount++
        // 成功项：移出三个状态源
        selectedIds.delete(t.id)
        const tbl = partsListRef.value?.elTableRef
        const row = items.value.find((r) => r.id === t.id)
        if (tbl && row) tbl.toggleRowSelection(row, false)
        selectedRows.value = selectedRows.value.filter((r) => r.id !== t.id)
      } catch (e) {
        failures.push({
          label: t.label,
          message: (e as Error).message ?? '未知错误',
        })
      }
    }
    if (successCount > 0) ElMessage.success(`成功下发 ${successCount} 件`)
    if (failures.length > 0) {
      ElMessage.error(
        `失败 ${failures.length} 件：${failures
          .map((f) => `${f.label}（${f.message}）`)
          .join('；')}`,
      )
    }
    if (failures.length === 0) batchDispatchVisible.value = false
    await fetchList()  // 内部 nextTick → restoreTableSelection
  } finally {
    batchDispatchSubmitting.value = false
  }
}
</script>

<style lang="scss" scoped>
.parts-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.filter-card {
  :deep(.el-card__body) {
    padding: 12px 16px;
  }
}

/* 2026-07-22：工具栏三组分类排列（搜索 / 日期 / 操作）。
   外层 nowrap 让三组保持一行；组内 wrap 允许单个控件换行。
   手机：整列堆叠，每组 width: 100%。 */
.filter-row {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: nowrap;

  @include until(sm) {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
}

.filter-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;

  @include until(sm) {
    width: 100%;
  }
}

/* 操作组靠右 */
.filter-group--actions {
  margin-left: auto;

  @include until(sm) {
    margin-left: 0;
  }
}

/* 日期组：组内 gap 稍大 */
.filter-group--dates {
  gap: 12px;
}

/* 2026-07-22：内联日期区间筛选（请购/计划/系统交期） */
.date-filter-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.date-filter-label {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.total-hint {
  font-size: 13px;
  color: var(--text-secondary);
}

.sheet-wrapper {
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  padding: 4px;
  overflow-x: auto;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  padding: 0 4px;

  @include until(sm) {
    justify-content: center;
  }
}

/* 手机筛选抽屉 */
.mobile-filter {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.mf-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mf-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.mf-status {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mf-urgent {
  margin-top: 6px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-color);
}

/* 批量打印底部 action bar（仿 DeliveryNoteNew 范式） */
.batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  padding: 10px 14px;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
  border-radius: 6px;
}
.batch-bar .bar-info {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #303133;
  font-size: 13px;
}
.batch-bar .bar-info strong {
  color: #409eff;
  font-weight: 600;
}

.muted {
  color: var(--text-secondary);
}

.name-link {
  color: var(--primary-color);
  text-decoration: none;
  &:hover {
    text-decoration: underline;
  }
}

.header-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  justify-content: center;
}

.filter-icon {
  font-size: 14px;
  color: var(--text-secondary);
  cursor: pointer;
  &.active {
    color: var(--primary-color);
  }
}

.filter-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  border-top: 1px solid var(--border-color-lighter);
  padding-top: 8px;
}

// 加急行：dashboard 同款红底 #fde2e2（与默认 .el-table 浅灰底可叠加）
:deep(.el-table__row.row-urgent) > td.el-table__cell {
  background-color: #fde2e2 !important;
}
:deep(.el-table__row.row-urgent:hover > td.el-table__cell) {
  background-color: #fbcaca !important;
}

// PR-G 2026-07-22：已开过送货单（且尚未 PICKED_UP）的零件行用浅蓝 #e6f4ff 提示
:deep(.el-table__row.row-on-delivery-note) > td.el-table__cell {
  background-color: #e6f4ff !important;
}
:deep(.el-table__row.row-on-delivery-note:hover > td.el-table__cell) {
  background-color: #d0e8ff !important;
}
</style>
