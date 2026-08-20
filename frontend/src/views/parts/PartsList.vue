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
        <!-- 2026-08-20：行类型（ALL/PART/ASSEMBLY）保留在顶部 filter-card；
             其余查询字段全部下放到各列表头 popover 内。 -->
        <div class="filter-group filter-group--rowtype">
          <el-select
            v-model="search.rowType"
            placeholder="类型"
            style="width: 140px"
            @change="onRowTypeChange"
          >
            <el-option label="全部" value="ALL" />
            <el-option label="仅零件" value="PART" />
            <el-option label="仅装配件" value="ASSEMBLY" />
          </el-select>

          <el-button @click="onReset">
            <el-icon><RefreshLeft /></el-icon>
            <span>重置</span>
          </el-button>
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

          <!-- INSPECTOR 看不到导入按钮（PR-I 2026-07-20）；
               2026-08-05：CNC 与 INSPECTOR 同样对待（看不到导入/批量/下发） -->
          <el-button
            v-if="canEdit"
            @click="router.push('/parts/new?tab=pdf')"
          >
            <el-icon><Document /></el-icon>
            <span>从 PDF/Excel 批量导入</span>
          </el-button>

          <!-- 2026-08-12：采购订单 Excel 导入（解析系统交期和订单号；同 canEdit 闸门） -->
          <el-button
            v-if="canEdit"
            @click="orderImportVisible = true"
          >
            <el-icon><Upload /></el-icon>
            <span>解析系统交期和订单号</span>
          </el-button>

          <!-- 批量打印 / 批量下发 toggle（2026-07-17 打印；2026-07-22 下发；INSPECTOR 不可见；手机隐藏） -->
          <template v-if="canEdit && !isMobile">
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
      :key="tableKey"
      :items="items"
      :loading="loading"
      :row-key="rowKey"
      :empty-text="emptyText"
      :card-class="(row: PartListItem) => (row.is_urgent ? 'rl-card--urgent' : '')"
      stripe
      border
      size="small"
      :default-sort="defaultSort"
      :row-class-name="rowClassName"
      :row-style="{ cursor: batchMode ? 'pointer' : 'default' }"
      :show-summary="canEdit"
      :summary-method="totalPriceSummary"
      lazy
      :load="loadChildren"
      :tree-props="{ hasChildren: 'has_children', children: 'children' }"
      @sort-change="onSortChange"
      @selection-change="onSelectionChange"
      @row-click="onBatchRowClick"
      @row-dblclick="onRowDblClick"
    >
      <template #toolbar>
        <ColumnVisibilityPopover
          :defs="columnDefs"
          :model-value="columnVisibility.currentMap" @update:model-value="columnVisibility.update"
          @reset="columnVisibility.showAll"
        />
      </template>
      <el-table-column
        v-if="batchMode"
        type="selection"
        width="55"
        :reserve-selection="true"
        :selectable="isBatchSelectable"
      />

      <el-table-column
        v-if="columnVisibility.isVisible('serial_no')"
        prop="serial_no"
        label="序列号"
        min-width="110"
        fixed="left"
        sortable="custom"
        show-overflow-tooltip align="center">
        <template #header>
          <span class="header-cell" :class="{ 'is-active': serialNoFilterActive }">
            <span>{{ serialNoFilterActive ? '序列号(1)' : '序列号' }}</span>
            <el-popover
              :width="280"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="serialNoPopoverVisible"
              @show="syncSerialNoDraft"
            >
              <template #reference>
                <el-icon class="filter-icon" :class="{ active: serialNoFilterActive }">
                  <Filter />
                </el-icon>
              </template>
              <el-input
                v-model="serialNoDraft"
                placeholder="序列号（ILIKE 子串）"
                clearable
                :class="{ 'scan-flash': serialNoFlash }"
                size="small"
                @keyup.enter="confirmSerialNoFilter"
              >
                <template #prefix><el-icon><Search /></el-icon></template>
              </el-input>
              <div class="filter-actions">
                <el-button size="small" link @click="resetSerialNoDraft">重置</el-button>
                <el-button size="small" type="primary" @click="confirmSerialNoFilter">确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
        <template #default="{ row }">
          <span :class="{ muted: !row.serial_no }">{{ row.serial_no || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('order_no')"
        prop="order_no"
        label="订单号"
        min-width="130"
        sortable="custom"
        show-overflow-tooltip align="center">
        <template #header>
          <span class="header-cell" :class="{ 'is-active': orderNoFilterActive }">
            <span>{{ orderNoFilterActive ? '订单号(1)' : '订单号' }}</span>
            <el-popover
              :width="280"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="orderNoPopoverVisible"
              @show="syncOrderNoDraft"
            >
              <template #reference>
                <el-icon class="filter-icon" :class="{ active: orderNoFilterActive }">
                  <Filter />
                </el-icon>
              </template>
              <div style="margin-bottom: 6px; color: var(--text-secondary); font-size: 12px">
                订单号子串搜索；勾选「仅空白」覆盖输入
              </div>
              <div class="filter-input-row">
                <el-input
                  v-model="orderNoDraft"
                  placeholder="订单号（ILIKE 子串）"
                  clearable
                  size="small"
                  style="flex: 1"
                  @keyup.enter="confirmOrderNoFilter"
                >
                  <template #prefix><el-icon><Search /></el-icon></template>
                </el-input>
                <el-checkbox
                  :model-value="orderNoIsNullDraft === true"
                  @update:model-value="(v) => (orderNoIsNullDraft = v ? true : undefined)"
                >仅空白</el-checkbox>
              </div>
              <div class="filter-actions">
                <el-button size="small" link @click="resetOrderNoDraft">重置</el-button>
                <el-button size="small" type="primary" @click="confirmOrderNoFilter">确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
        <template #default="{ row }">
          <el-input
            v-if="editingId === row.id"
            v-model="editBuffer.order_no"
            size="small"
          />
          <span v-else>{{ row.order_no || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('drawing_no')"
        prop="drawing_no"
        label="图号"
        min-width="130"
        fixed="left"
        sortable="custom"
        show-overflow-tooltip align="center">
        <template #header>
          <span class="header-cell" :class="{ 'is-active': drawingNoFilterActive }">
            <span>{{ drawingNoFilterActive ? '图号(1)' : '图号' }}</span>
            <el-popover
              :width="280"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="drawingNoPopoverVisible"
              @show="syncDrawingNoDraft"
            >
              <template #reference>
                <el-icon class="filter-icon" :class="{ active: drawingNoFilterActive }">
                  <Filter />
                </el-icon>
              </template>
              <el-input
                v-model="drawingNoDraft"
                placeholder="图号（ILIKE 子串）"
                clearable
                size="small"
                @keyup.enter="confirmDrawingNoFilter"
              >
                <template #prefix><el-icon><Search /></el-icon></template>
              </el-input>
              <div class="filter-actions">
                <el-button size="small" link @click="resetDrawingNoDraft">重置</el-button>
                <el-button size="small" type="primary" @click="confirmDrawingNoFilter">确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
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
        v-if="columnVisibility.isVisible('name')"
        prop="name"
        label="名称"
        min-width="200"
        sortable="custom"
        show-overflow-tooltip align="center">
        <template #header>
          <span class="header-cell" :class="{ 'is-active': nameFilterActive }">
            <span>{{ nameFilterActive ? '名称(1)' : '名称' }}</span>
            <el-popover
              :width="280"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="namePopoverVisible"
              @show="syncNameDraft"
            >
              <template #reference>
                <el-icon class="filter-icon" :class="{ active: nameFilterActive }">
                  <Filter />
                </el-icon>
              </template>
              <el-input
                v-model="nameDraft"
                placeholder="名称（ILIKE 子串）"
                clearable
                size="small"
                @keyup.enter="confirmNameFilter"
              >
                <template #prefix><el-icon><Search /></el-icon></template>
              </el-input>
              <div class="filter-actions">
                <el-button size="small" link @click="resetNameDraft">重置</el-button>
                <el-button size="small" type="primary" @click="confirmNameFilter">确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
        <template #default="{ row }">
          <el-input
            v-if="editingId === row.id"
            v-model="editBuffer.name"
            size="small"
          />
          <template v-else>
            <el-tag v-if="row.row_type === 'ASSEMBLY'" type="warning" size="small" effect="plain" style="margin-right: 4px;">
              装配件
            </el-tag>
            <router-link :to="row.row_type === 'ASSEMBLY' ? `/assemblies/${row.id}` : `/parts/${row.id}`" class="name-link">
              {{ row.name }}
            </router-link>
          </template>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('customer')"
        label="客户" min-width="180" show-overflow-tooltip align="center">
        <template #header>
          <span class="header-cell" :class="{ 'is-active': customerFilterActive }">
            <span>{{ customerFilterActive ? '客户(1)' : '客户' }}</span>
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
                  :class="{ active: customerFilterActive }"
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


      <el-table-column
        v-if="columnVisibility.isVisible('applicant')"
        label="申请人" min-width="160" show-overflow-tooltip align="center">
        <template #default="{ row }">
          <el-autocomplete
            v-if="editingId === row.id"
            v-model="editBuffer.applicant_name"
            value-key="name"
            :fetch-suggestions="applicantSuggest"
            :trigger-on-focus="true"
            :debounce="0"
            :loading="applicantLoading"
            placeholder="选择或输入申请人姓名"
            clearable
            size="small"
            style="width: 100%"
          />
          <span v-else>{{ row.applicant_name || '—' }}</span>
        </template>
      </el-table-column>

<el-table-column
        v-if="columnVisibility.isVisible('status')"
        label="状态"
        min-width="140"
        align="center"
      >
        <template #header>
          <span class="header-cell" :class="{ 'is-active': statusFilterActive }">
            <span>{{ statusFilterActive ? `状态(${statusSelectedCount})` : '状态' }}</span>
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
          <el-tag
            v-if="row.has_been_repaired"
            type="warning"
            size="small"
            effect="dark"
            style="margin-left: 4px"
          >
            返修
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('quantity')"
        prop="quantity"
        label="数量" min-width="110" sortable="custom" align="right">
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

      <el-table-column
        v-if="canEdit && columnVisibility.isVisible('unit_price')"
        prop="unit_price"
        label="单价" min-width="120" sortable="custom" align="right">
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

      <!-- 2026-07-24 v2 调整：总价 = quantity × unit_price **前端实时计算**
     （编辑态下改 unit_price / quantity 立即反映在总价列，无需等保存） -->
      <el-table-column
        v-if="canEdit && columnVisibility.isVisible('total_price')"
        prop="total_price"
        label="总价" min-width="120" sortable="custom" align="right">
        <template #default="{ row }">
          <span>{{ displayTotalPrice(row as PartListItem) }}</span>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('request_date')"
        prop="request_date"
        label="请购日期"
        min-width="150"
        sortable="custom" align="center">
        <template #header>
          <span class="header-cell" :class="{ 'is-active': requestDateFilterActive }">
            <span>{{ requestDateFilterActive ? '请购日期(1)' : '请购日期' }}</span>
            <el-popover
              :width="280"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="requestDatePopoverVisible"
            >
              <template #reference>
                <el-icon class="filter-icon" :class="{ active: requestDateFilterActive }">
                  <Filter />
                </el-icon>
              </template>
              <el-date-picker
                v-model="requestDateRange"
                type="daterange"
                value-format="YYYY-MM-DD"
                range-separator="~"
                start-placeholder="起点"
                end-placeholder="终点"
                unlink-panels
                clearable
                size="small"
                style="width: 100%"
              />
              <div class="filter-actions">
                <el-button size="small" link @click="resetRequestDateDraft">重置</el-button>
                <el-button size="small" type="primary" @click="confirmRequestDateFilter">确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
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
        v-if="columnVisibility.isVisible('planned_delivery_date')"
        prop="planned_delivery_date"
        label="计划交期"
        min-width="150"
        sortable="custom" align="center">
        <template #header>
          <span class="header-cell" :class="{ 'is-active': plannedDateFilterActive }">
            <span>{{ plannedDateFilterActive ? '计划交期(1)' : '计划交期' }}</span>
            <el-popover
              :width="280"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="plannedDatePopoverVisible"
            >
              <template #reference>
                <el-icon class="filter-icon" :class="{ active: plannedDateFilterActive }">
                  <Filter />
                </el-icon>
              </template>
              <el-date-picker
                v-model="plannedDateRange"
                type="daterange"
                value-format="YYYY-MM-DD"
                range-separator="~"
                start-placeholder="起点"
                end-placeholder="终点"
                unlink-panels
                clearable
                size="small"
                style="width: 100%"
              />
              <div class="filter-actions">
                <el-button size="small" link @click="resetPlannedDateDraft">重置</el-button>
                <el-button size="small" type="primary" @click="confirmPlannedDateFilter">确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
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
        v-if="columnVisibility.isVisible('system_delivery_date')"
        prop="system_delivery_date"
        label="系统交期"
        min-width="150"
        sortable="custom" align="center">
        <template #header>
          <span class="header-cell" :class="{ 'is-active': systemDateFilterActive }">
            <span>{{ systemDateFilterActive ? '系统交期(1)' : '系统交期' }}</span>
            <el-popover
              :width="300"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="systemDatePopoverVisible"
              @show="syncSystemDateDraft"
            >
              <template #reference>
                <el-icon class="filter-icon" :class="{ active: systemDateFilterActive }">
                  <Filter />
                </el-icon>
              </template>
              <div style="margin-bottom: 6px; color: var(--text-secondary); font-size: 12px">
                区间 + 「仅空白」checkbox；勾选后区间失效
              </div>
              <div class="filter-input-row">
                <el-date-picker
                  v-model="systemDateRange"
                  type="daterange"
                  value-format="YYYY-MM-DD"
                  range-separator="~"
                  start-placeholder="起点"
                  end-placeholder="终点"
                  unlink-panels
                  clearable
                  size="small"
                  style="flex: 1"
                />
                <el-checkbox
                  :model-value="systemDateIsNullDraft === true"
                  @update:model-value="(v) => (systemDateIsNullDraft = v ? true : undefined)"
                >仅空白</el-checkbox>
              </div>
              <div class="filter-actions">
                <el-button size="small" link @click="resetSystemDateDraft">重置</el-button>
                <el-button size="small" type="primary" @click="confirmSystemDateFilter">确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
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
        v-if="columnVisibility.isVisible('delivered_quantity')"
        prop="delivered_quantity"
        label="已送数量"
        min-width="100"
        align="right">
        <template #default="{ row }">
          <span v-if="row.row_type === 'ASSEMBLY'" class="muted">—</span>
          <span v-else>{{ row.delivered_quantity ?? 0 }}</span>
        </template>
      </el-table-column>

            <el-table-column
              v-if="columnVisibility.isVisible('is_urgent')"
              label="加急" min-width="80" align="center">
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

      <!-- 2026-08-01：下一道工序列（位置：location 列左侧；带多选筛选 popover） -->
      <el-table-column
        v-if="columnVisibility.isVisible('next_process')"
        label="下一道工序"
        min-width="130"
        align="center"
      >
        <template #header>
          <span class="header-cell" :class="{ 'is-active': nextProcessFilterActive }">
            <span>{{ nextProcessFilterActive ? `下一道工序(${nextProcessSelectedCount})` : '下一道工序' }}</span>
            <el-popover
              :width="240"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="nextProcessPopoverVisible"
              @show="onNextProcessPopoverShow"
            >
              <template #reference>
                <el-icon
                  class="filter-icon"
                  :class="{ active: nextProcessFilterActive }"
                >
                  <Filter />
                </el-icon>
              </template>
              <div style="margin-bottom: 6px; color: var(--text-secondary); font-size: 12px">
                多选下一道工序；装配行无工序不参与筛选
              </div>
              <el-checkbox-group v-model="nextProcessDraft" style="max-height: 280px; overflow-y: auto">
                <el-checkbox
                  v-for="opt in nextProcessOptions"
                  :key="opt.value"
                  :value="opt.value"
                  :label="opt.label"
                />
              </el-checkbox-group>
              <div class="filter-actions">
                <el-button size="small" link @click="resetNextProcessDraft">重置</el-button>
                <el-button
                  size="small"
                  type="primary"
                  @click="confirmNextProcessFilter"
                >确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
        <template #default="{ row }">
          <span v-if="row.row_type === 'ASSEMBLY'" class="muted">—</span>
          <span v-else-if="row.next_process_name">{{ row.next_process_name }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <!-- 2026-08-01：所在位置列加多选筛选 popover -->
      <el-table-column
        v-if="columnVisibility.isVisible('location')"
        label="所在位置" min-width="150" show-overflow-tooltip align="center"
      >
        <template #header>
          <span class="header-cell" :class="{ 'is-active': locationFilterActive }">
            <span>{{ locationFilterActive ? `所在位置(${locationSelectedCount})` : '所在位置' }}</span>
            <el-popover
              :width="260"
              placement="bottom-start"
              trigger="click"
              :show-arrow="false"
              v-model:visible="locationPopoverVisible"
              @show="onLocationPopoverShow"
            >
              <template #reference>
                <el-icon
                  class="filter-icon"
                  :class="{ active: locationFilterActive }"
                >
                  <Filter />
                </el-icon>
              </template>
              <div style="margin-bottom: 6px; color: var(--text-secondary); font-size: 12px">
                选大类命中该类全部；选叶子精确到货架/工人/外协公司
              </div>
              <el-tree-select
                v-model="locationDraft"
                :data="locationTree"
                node-key="id"
                :props="{ label: 'name', children: 'children' }"
                multiple
                show-checkbox
                check-strictly
                check-on-click-node
                clearable
                filterable
                :teleported="false"
                placeholder="选择位置"
                style="width: 100%"
                @clear="locationDraft = []"
              />
              <div class="filter-actions">
                <el-button size="small" link @click="resetLocationDraft">重置</el-button>
                <el-button
                  size="small"
                  type="primary"
                  @click="confirmLocationFilter"
                >确定</el-button>
              </div>
            </el-popover>
          </span>
        </template>
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
          <span v-else-if="row.location === 'OUTSOURCE_COMPANY' && row.outsource_company_name">
            外协 {{ row.outsource_company_name }}
          </span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>

      <el-table-column
        v-if="columnVisibility.isVisible('note')"
        label="备注" min-width="160" show-overflow-tooltip align="center">
        <template #default="{ row }">
          <el-input
            v-if="editingId === row.id"
            v-model="editBuffer.note"
            size="small"
          />
          <span v-else>{{ row.note || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="操作" min-width="160" fixed="right" align="center">
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
            <el-button link type="primary" size="small" @click="$router.push(row.row_type === 'ASSEMBLY' ? `/assemblies/${row.id}` : `/parts/${row.id}`)">详情</el-button>
            <el-button
              v-if="canEdit"
              link
              type="warning"
              size="small"
              @click="startEdit(row as PartListItem)"
            >编辑</el-button>
            <el-button
              v-if="canEdit && row.status === 'PENDING' && row.row_type !== 'ASSEMBLY'"
              link
              type="success"
              size="small"
              @click="onDispatch(row as PartListItem)"
            >下发</el-button>
            <!-- 2026-08-05 召回：M/C 召回 ON_SHELF/PROGRAMMING → PENDING -->
            <el-button
              v-if="canRecallToPending(row as PartListItem)"
              link
              type="danger"
              size="small"
              @click="onRecallToPending(row as PartListItem)"
            >召回(待生产)</el-button>
            <!-- 2026-08-05 召回：M/CNC 召回 ON_SHELF → PROGRAMMING -->
            <el-button
              v-if="canRecallToProgramming(row as PartListItem)"
              link
              type="warning"
              size="small"
              @click="onRecallToProgramming(row as PartListItem)"
            >召回(待编程)</el-button>
          </template>
        </template>
      </el-table-column>

      <!-- 手机卡片：关键字段 + 操作按钮 -->
      <template #card="{ row }">
        <div class="rl-card-head">
          <router-link :to="row.row_type === 'ASSEMBLY' ? `/assemblies/${row.id}` : `/parts/${row.id}`" class="rl-card-title name-link">
            <el-tag v-if="row.row_type === 'ASSEMBLY'" type="warning" size="small" effect="plain" style="margin-right: 4px;">
              装配件
            </el-tag>
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
          <el-button link type="primary" size="small" @click="router.push(row.row_type === 'ASSEMBLY' ? `/assemblies/${row.id}` : `/parts/${row.id}`)">详情</el-button>
          <el-button
            v-if="canEdit"
            link
            type="warning"
            size="small"
            @click="startEdit(row as PartListItem)"
          >编辑</el-button>
          <el-button
            v-if="canEdit && row.status === 'PENDING' && row.row_type !== 'ASSEMBLY'"
            link
            type="success"
            size="small"
            @click="onDispatch(row as PartListItem)"
          >下发</el-button>
          <!-- 2026-08-05 召回：M/C 召回 ON_SHELF/PROGRAMMING → PENDING -->
          <el-button
            v-if="canRecallToPending(row as PartListItem)"
            link
            type="danger"
            size="small"
            @click="onRecallToPending(row as PartListItem)"
          >召回(待生产)</el-button>
          <!-- 2026-08-05 召回：M/CNC 召回 ON_SHELF → PROGRAMMING -->
          <el-button
            v-if="canRecallToProgramming(row as PartListItem)"
            link
            type="warning"
            size="small"
            @click="onRecallToProgramming(row as PartListItem)"
          >召回(待编程)</el-button>
        </div>
      </template>
    </ResponsiveList>

    <!-- 批量打印 / 批量下发 — 底部 action bar（2026-07-17 打印；2026-07-22 下发；INSPECTOR 不可见；CNC 同样不可见） -->
    <div v-if="canEdit && batchMode" class="batch-bar">
      <div class="bar-info">
        <span v-if="batchSelectedPartCount > 0">
          零件 <strong>{{ batchSelectedPartCount }}</strong> 件
        </span>
        <span v-if="batchSelectedAssemblyCount > 0" class="bar-info__assembly">
          装配件 <strong>{{ batchSelectedAssemblyCount }}</strong> 件
          <el-tooltip placement="top" :show-after="0">
            <template #content>
              勾选装配件行将打印该装配件的<b>全部子件</b>图纸
            </template>
            <el-icon class="batch-hint"><WarningFilled /></el-icon>
          </el-tooltip>
        </span>
        <el-button link size="small" @click="onSelectAllPage">全选当前页</el-button>
        <el-button link size="small" @click="onClearSelection">清空选择</el-button>
      </div>
      <!-- 打印进度 -->
      <div v-if="batchPrintTotal > 0" class="batch-print-progress">
        <el-progress
          :percentage="batchPrintProgress"
          :stroke-width="16"
          :text-inside="true"
          :show-text="true"
        />
        <div class="batch-print-progress__text">
          正在生成打印文件 {{ batchPrintCurrent }}/{{ batchPrintTotal }}
        </div>
      </div>

      <el-button
        v-if="batchAction === 'print'"
        type="primary"
        :loading="batchPrinting"
        :disabled="selectedIds.size === 0 || batchPrintTotal > 0"
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
          <span class="muted">
            已选 <strong>{{ selectedIds.size }}</strong> 件
            <template v-if="batchAction === 'print'">零件将执行此操作</template>
            <template v-else>PENDING 零件将执行此操作</template>
          </span>
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

    <!-- 手机筛选抽屉：承载桌面表头 popover 的同款筛选（状态 + 加急 + 客户 + 查询） -->
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
        <!-- 2026-08-20：手机端筛选抽屉只承载 status + customer；查询入口下放到各列表头 popover。
             手机端列头被 ResponsiveList 卡片视图隐藏，列头 popover 不可点 → 走顶部「筛选」按钮。
              后续若 QA 反馈查询入口手机端缺失，再单独加 mobile-spec 抽屉补齐。 -->
      </div>
      <template #footer>
        <el-button @click="resetMobileFilter">重置</el-button>
        <el-button type="primary" @click="confirmMobileFilter">确定</el-button>
      </template>
    </el-drawer>

    <!-- 2026-08-12：采购订单 Excel 导入对话框（解析系统交期和订单号） -->
    <PurchaseOrderImportDialog
      v-model="orderImportVisible"
      @success="fetchList"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { PDFDocument } from 'pdf-lib'
import type { SummaryMethod } from 'element-plus'
import {
  Close,
  Document,
  Filter,
  Printer,
  Promotion,
  RefreshLeft,
  Search,
  Upload,
  WarningFilled,
} from '@element-plus/icons-vue'
import ResponsiveList from '@/components/ResponsiveList.vue'
import PurchaseOrderImportDialog from './components/PurchaseOrderImportDialog.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useDialogSize } from '@/composables/useDialogSize'
import {
  listParts,
  placeOnShelf,
  printPartDrawingBatch,
  recallToPending,
  recallToProgramming,
  sendToProgramming,
  updatePart,
  type ListPartsParams,
  type PartUpdatePayload,
} from '@/api/parts'
import { getAssembly, updateAssembly } from '@/api/assembly'
import type { PartListItem, PartRowTypeFilter, PartSortKey, SortDir } from '@/types/parts'
import { listShelves } from '@/api/shelves'
import type { Shelf } from '@/types/shelf'
import { listProcesses } from '@/api/process'
import { useShelfProcessFilter } from '@/composables/useShelfProcessFilter'
import type { Process } from '@/types/process'
import {
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TAG_TYPE,
  PART_SORT_KEY_SET,
  PART_SORT_KEY_TO_PROP,
  PART_SORT_PROP_MAP,
  type OrderStatus,
} from '@/types/parts'
import { useAuthSession } from '@/composables/useAuthSession'
import { usePermissions } from '@/composables/usePermissions'
import { useCustomerTree } from '@/composables/useCustomerTree'
import { useApplicantSearch } from '@/composables/useApplicantSearch'
import type { Applicant } from '@/types/applicant'
import {
  splitLocationSelection,
  usePartLocationTree,
} from '@/composables/usePartLocationTree'
import { useListFilterPersist } from '@/composables/useListFilterPersist'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useBarcodeScanner } from '@/composables/useBarcodeScanner'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'

// ============ 角色 & 默认筛选 ============
const { hasRole } = useAuthSession()
const isCncProgrammer = hasRole('CNC_PROGRAMMER')
// PR-I 2026-07-20：INSPECTOR 看不到导入 / 批量打印 / 下发按钮
const { isInspector } = usePermissions()
// 行内编辑权限：与后端 POST /parts/{id}/update 一致（MANAGER / CLERK）
const canEdit = hasRole('MANAGER') || hasRole('CLERK')
// 2026-08-05 召回权限：与后端 POST /parts/{id}/recall-* 一致
const canRecallToPendingAuth = hasRole('MANAGER') || hasRole('CLERK')
const canRecallToProgrammingAuth =
  hasRole('MANAGER') || hasRole('CNC_PROGRAMMER')

/** 召回按钮可见性：与后端 `_resolve_target_batch` expect 保持一致。
 *  不显式判定 status=='PROGRAMMING'：PROGRAMMING 是 PROGRAMMING DB status；
 *  行 location 在该态下为 'OFFICE'，自然被排除。
 */
function canRecallToPending(row: PartListItem): boolean {
  if (!canRecallToPendingAuth) return false
  if (row.row_type === 'ASSEMBLY') return false
  if (row.status === 'PROGRAMMING') return true
  return row.status === 'IN_PROCESS' && row.location === 'PRODUCTION_SHELF'
}

function canRecallToProgramming(row: PartListItem): boolean {
  if (!canRecallToProgrammingAuth) return false
  if (row.row_type === 'ASSEMBLY') return false
  return row.status === 'IN_PROCESS' && row.location === 'PRODUCTION_SHELF'
}
const { tree: customerTree } = useCustomerTree()

// 申请人补全（PR-2026-08-20）：行内编辑态下复用 PartBatchNew/AssemblyCreate 的同款
// useApplicantSearch，按「当前编辑行所在客户」懒加载申请人全集。
// PartListItem 不含 customer_id，只能按 customer_name 在 customerTree 里反查一级客户 id。
const {
  applicants: applicantOptions,
  loading: applicantLoading,
  loadForCustomer,
  querySearch,
} = useApplicantSearch({
  resolveRootCustomerId: (pickedId: string | null): string | null => {
    if (!pickedId) return null
    const walk = (nodes: typeof customerTree.value): string | null => {
      for (const n of nodes) {
        if (String(n.id) === pickedId) return String(n.id)
        const found = walk(n.children ?? [])
        if (found) return found
      }
      return null
    }
    return walk(customerTree.value)
  },
})

/** 2026-08-20：按 PartListItem.customer_name 在 customerTree 中反查到一级客户 id。
 *  行无 customer_name（极少；如老数据 / 系统装配）→ 返回 null，autocomplete 走「自由输入」。
 *  命中叶子客户 → 返回其所属一级客户的 id；命中一级客户 → 返回自身。 */
function resolveRootCustomerForRow(row: PartListItem): string | null {
  const name = row.customer_name
  if (!name) return null
  const walk = (
    nodes: typeof customerTree.value,
    rootId: string,
  ): string | null => {
    for (const n of nodes) {
      if (n.name === name) return rootId
      const found = walk(n.children ?? [], rootId)
      if (found !== null) return found
    }
    return null
  }
  for (const root of customerTree.value) {
    const found = walk(root.children ?? [], String(root.id))
    if (found !== null) return found
  }
  return null
}

/** 申请人 autocomplete 在编辑态下的可用性：有缓存或允许自由输入时为 true。
 *  querySearch 对空 query 回退全缓存，所以有 root 解析但缓存为空时仍允许输入并提交。 */
const applicantEditingReady = computed(() => applicantOptions.value.length > 0)

// 2026-08-20：el-autocomplete 的 :fetch-suggestions 期望 (q, cb) => void 签名；
// Vue 模板里写 TS 类型注解会被模板编译器拒解析，故包一层并显式标注。
function applicantSuggest(
  queryString: string,
  callback: (items: Applicant[]) => void,
): void {
  querySearch(queryString, callback)
}
const route = useRoute()
const router = useRouter()
const { isMobile } = useBreakpoint()

interface SearchState {
  keyword: string
  /** 2026-08-20：图号 / 名称独立筛选（替换旧 keyword 单一字段）。
   *  后端 ILIKE 子串包含；同时设两个 ⇒ AND 联合（drawing_no ILIKE AND name ILIKE）。 */
  drawingNo: string
  name: string
  orderNo: string
  /** 2026-07-31：序列号独立搜索（ILIKE 包含；装配件子序列号自动带出母装配件） */
  serialNo: string
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
  /**
   * 2026-08-11：订单号空白筛选。
   * - true  ⇒ 仅空白（NULL OR ''），覆盖 order_no 子串搜索
   * - undefined ⇒ 任意（cleanParams 不发送该字段）
   */
  orderNoIsNull: boolean | undefined
  /**
   * 2026-08-11：系统交期空白筛选。
   * - true  ⇒ 仅空白（NULL），区间失效
   * - undefined ⇒ 任意（cleanParams 不发送该字段）
   */
  systemDeliveryDateIsNull: boolean | undefined
  /** 2026-08-01：下一道工序 id 多选（雪花 ID 字符串；空数组=全部） */
  nextProcessIds: string[]
  /** 2026-08-01：物理位置大类多选（OFFICE/PRODUCTION_SHELF/WORKER/INSPECTION_SHELF/OUTSOURCE_COMPANY；空数组=全部） */
  locations: string[]
  /** 2026-08-05：物理位置具体 holder 多选（货架/工人/外协公司 雪花 ID 字符串；与 `locations` 是 OR 关系；空数组=全部） */
  holderIds: string[]
  /** 2026-08-05：行类型筛选（ALL=全部/PART=仅零件/ASSEMBLY=仅装配件） */
  rowType: PartRowTypeFilter
}
function initialSearch(): SearchState {
  return {
    keyword: '',
    drawingNo: '',
    name: '',
    orderNo: '',
    serialNo: '',
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
    orderNoIsNull: undefined,  // 2026-08-11
    systemDeliveryDateIsNull: undefined,  // 2026-08-11
    nextProcessIds: [],
    locations: [],
    holderIds: [],
    rowType: 'ALL',
  }
}
const search = reactive<SearchState>(initialSearch())
// 2026-08-04：扫码命中序列号时给输入框加 0.6s 脉冲动画（视觉反馈）
const serialNoFlash = ref(false)

const statusOptions: { value: OrderStatus; label: string }[] = (
  Object.keys(ORDER_STATUS_LABEL) as OrderStatus[]
).map((v) => ({ value: v, label: ORDER_STATUS_LABEL[v] }))

const statusFilterActive = computed(
  () => search.statuses.length > 0 || search.isUrgent === true,
)
const customerFilterActive = computed(() => search.customerId !== '')
// 2026-07-31：表头「状态(N)」计数同步于已确认的搜索条件（与 statusFilterActive 共享来源）；
// draft（statusDraft）是 popover 内未提交状态，不计入
const statusSelectedCount = computed(() => search.statuses.length)

// 2026-08-06 bugfix：装配件位置类筛选切换时 el-table remount key。
// Element Plus 2.14 el-table 的 lazy tree 把「已加载子件」按 row-key 缓存在内部
// lazyTreeNodeMap；items 整体替换（filter 切换）不会清空该缓存，导致已展开装配件
// 仍展示上一次筛选的命中子件。给 ResponsiveList 加 :key 让这四个影响子件显示的
// 筛选变化时整体 remount，强制走 loadChildren 拿到当前 matched_children。
// 不含 keyword/排序/状态/日期等不影响子件显示的筛选 —— 保留滚动位置与排序高亮。
const tableKey = computed(
  () =>
    [
      search.locations.join(','),
      search.holderIds.join(','),
      search.nextProcessIds.join(','),
      search.rowType,
    ].join('|'),
)

// ============ 三个日期区间筛选（2026-07-22：内联 daterange） ============
// daterange 的 v-model 绑定 [start, end]；清空时 el 抛 null，getter/setter 兜底。
// 2026-08-20：各列表头 popover 通过 makeRangeModel 直接绑 search.*（无草稿层），
// 用户在弹层内修改日期 → setter 写回 search.* → 触发 onSearch()。
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
  // 2026-07-31：改筛选即清空批量选择（与「翻页保留」对应）
  if (batchMode.value) clearAllSelection()
  onSearch()
}

// ============ 2026-08-01：下一道工序列头 popover ============
const nextProcessPopoverVisible = ref(false)
const nextProcessDraft = ref<string[]>([])

const nextProcessOptions = computed<{ value: string; label: string }[]>(() =>
  processes.value
    .map((p) => ({ value: String(p.id), label: `${p.code} / ${p.name}` })),
)

async function ensureProcessesLoadedForFilter(): Promise<void> {
  if (processes.value.length > 0) return
  try {
    processes.value = (await listProcesses({ limit: 200 })).items
  } catch {
    processes.value = []
  }
}

async function onNextProcessPopoverShow(): Promise<void> {
  await ensureProcessesLoadedForFilter()
  nextProcessDraft.value = [...search.nextProcessIds]
}

function syncNextProcessDraft(): void {
  nextProcessDraft.value = [...search.nextProcessIds]
}

function resetNextProcessDraft(): void {
  nextProcessDraft.value = []
  search.nextProcessIds = []
  nextProcessPopoverVisible.value = false
  onSearch()
}

function confirmNextProcessFilter(): void {
  search.nextProcessIds = [...nextProcessDraft.value]
  nextProcessPopoverVisible.value = false
  if (batchMode.value) clearAllSelection()
  onSearch()
}

const nextProcessFilterActive = computed(() => search.nextProcessIds.length > 0)
const nextProcessSelectedCount = computed(() => search.nextProcessIds.length)

// ============ 2026-08-05：所在位置列头 popover（el-tree-select 多选树）============
// 树数据由 usePartLocationTree 提供（5 个 PartLocation 大类 + 具体 holder 叶子）。
// 懒加载：仅在打开 popover 时调 loadLocationTree()，避免每次进页面无谓请求。
// 选中值同时包含父（PartLocation）与叶（雪花 ID），由 splitLocationSelection 拆开。
const { tree: locationTree, load: loadLocationTree } = usePartLocationTree()

const locationPopoverVisible = ref(false)
const locationDraft = ref<string[]>([])

function onLocationPopoverShow(): void {
  // 1. 懒加载（幂等：模块级 Promise 缓存）
  void loadLocationTree()
  // 2. draft 回写已确认筛选（大类 + holder 叶子合并），确保再次打开看到原状
  locationDraft.value = [...search.locations, ...search.holderIds]
}

function syncLocationDraft(): void {
  locationDraft.value = [...search.locations, ...search.holderIds]
}

function resetLocationDraft(): void {
  locationDraft.value = []
  search.locations = []
  search.holderIds = []
  locationPopoverVisible.value = false
  onSearch()
}

function confirmLocationFilter(): void {
  const split = splitLocationSelection(locationDraft.value)
  search.locations = split.locations
  search.holderIds = split.holderIds
  locationPopoverVisible.value = false
  if (batchMode.value) clearAllSelection()
  onSearch()
}

const locationFilterActive = computed(
  () => search.locations.length > 0 || search.holderIds.length > 0,
)
const locationSelectedCount = computed(
  () => search.locations.length + search.holderIds.length,
)

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
  // 2026-07-31：改筛选即清空批量选择（与「翻页保留」对应）
  if (batchMode.value) clearAllSelection()
  onSearch()
}

// ============ 2026-08-20：7 个查询字段按列拆分到对应表头 popover ============
// 沿用现有 draft → 确定/重置 模式；每个 popover 独立维护自己的 ref<boolean>。
// 「确定」时直接写 search.*（无中间草稿层），避免与 daterange 的 setter 重复桥接。
const drawingNoPopoverVisible = ref(false)
const drawingNoDraft = ref('')
const drawingNoFilterActive = computed(() => search.drawingNo.trim() !== '')
function syncDrawingNoDraft(): void { drawingNoDraft.value = search.drawingNo }
function resetDrawingNoDraft(): void {
  drawingNoDraft.value = ''
  search.drawingNo = ''
  drawingNoPopoverVisible.value = false
  onSearch()
}
function confirmDrawingNoFilter(): void {
  search.drawingNo = drawingNoDraft.value.trim()
  drawingNoPopoverVisible.value = false
  if (batchMode.value) clearAllSelection()
  onSearch()
}

const namePopoverVisible = ref(false)
const nameDraft = ref('')
const nameFilterActive = computed(() => search.name.trim() !== '')
function syncNameDraft(): void { nameDraft.value = search.name }
function resetNameDraft(): void {
  nameDraft.value = ''
  search.name = ''
  namePopoverVisible.value = false
  onSearch()
}
function confirmNameFilter(): void {
  search.name = nameDraft.value.trim()
  namePopoverVisible.value = false
  if (batchMode.value) clearAllSelection()
  onSearch()
}

const orderNoPopoverVisible = ref(false)
const orderNoDraft = ref('')
const orderNoIsNullDraft = ref<boolean | undefined>(undefined)
const orderNoFilterActive = computed(
  () => search.orderNo.trim() !== '' || search.orderNoIsNull === true,
)
function syncOrderNoDraft(): void {
  orderNoDraft.value = search.orderNo
  orderNoIsNullDraft.value = search.orderNoIsNull
}
function resetOrderNoDraft(): void {
  orderNoDraft.value = ''
  orderNoIsNullDraft.value = undefined
  search.orderNo = ''
  search.orderNoIsNull = undefined
  orderNoPopoverVisible.value = false
  onSearch()
}
function confirmOrderNoFilter(): void {
  search.orderNo = orderNoDraft.value.trim()
  search.orderNoIsNull = orderNoIsNullDraft.value
  orderNoPopoverVisible.value = false
  if (batchMode.value) clearAllSelection()
  onSearch()
}

const serialNoPopoverVisible = ref(false)
const serialNoDraft = ref('')
const serialNoFilterActive = computed(() => search.serialNo.trim() !== '')
function syncSerialNoDraft(): void { serialNoDraft.value = search.serialNo }
function resetSerialNoDraft(): void {
  serialNoDraft.value = ''
  search.serialNo = ''
  serialNoPopoverVisible.value = false
  onSearch()
}
function confirmSerialNoFilter(): void {
  search.serialNo = serialNoDraft.value.trim()
  serialNoPopoverVisible.value = false
  if (batchMode.value) clearAllSelection()
  onSearch()
}

// 三个日期区间直接复用 makeRangeModel 的 computed setter（已在行 1296 附近定义）：
//   requestDateRange / plannedDateRange / systemDateRange —— setter 写回 search.*。
// 重置/确认 handler 各包一层，弹层关闭 + onSearch()。
const requestDatePopoverVisible = ref(false)
const plannedDatePopoverVisible = ref(false)
const systemDatePopoverVisible = ref(false)
const requestDateFilterActive = computed(
  () => search.requestDateFrom !== '' || search.requestDateTo !== '',
)
const plannedDateFilterActive = computed(
  () => search.plannedDeliveryDateFrom !== '' || search.plannedDeliveryDateTo !== '',
)
const systemDateFilterActive = computed(
  () => search.systemDeliveryDateFrom !== ''
    || search.systemDeliveryDateTo !== ''
    || search.systemDeliveryDateIsNull === true,
)
const systemDateIsNullDraft = ref<boolean | undefined>(undefined)
function syncSystemDateDraft(): void {
  systemDateIsNullDraft.value = search.systemDeliveryDateIsNull
}
function resetRequestDateDraft(): void {
  search.requestDateFrom = ''
  search.requestDateTo = ''
  requestDatePopoverVisible.value = false
  onDateRangeChange()
}
function confirmRequestDateFilter(): void {
  requestDatePopoverVisible.value = false
  if (batchMode.value) clearAllSelection()
  onDateRangeChange()
}
function resetPlannedDateDraft(): void {
  search.plannedDeliveryDateFrom = ''
  search.plannedDeliveryDateTo = ''
  plannedDatePopoverVisible.value = false
  onDateRangeChange()
}
function confirmPlannedDateFilter(): void {
  plannedDatePopoverVisible.value = false
  if (batchMode.value) clearAllSelection()
  onDateRangeChange()
}
function resetSystemDateDraft(): void {
  search.systemDeliveryDateFrom = ''
  search.systemDeliveryDateTo = ''
  search.systemDeliveryDateIsNull = undefined
  systemDateIsNullDraft.value = undefined
  systemDatePopoverVisible.value = false
  onDateRangeChange()
}
function confirmSystemDateFilter(): void {
  search.systemDeliveryDateIsNull = systemDateIsNullDraft.value
  systemDatePopoverVisible.value = false
  if (batchMode.value) clearAllSelection()
  onDateRangeChange()
}

// ============ 手机筛选抽屉 ============
// 2026-08-20：手机端仅承载 status + customer 筛选；查询入口已下放到列表头 popover。
const mobileFilterOpen = ref(false)
const anyFilterActive = computed(
  () => statusFilterActive.value || customerFilterActive.value,
)

// 2026-08-12：采购订单 Excel 导入对话框可见性
const orderImportVisible = ref(false)

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
  if (row.location === 'OUTSOURCE_COMPANY' && row.outsource_company_name)
    return `外协 ${row.outsource_company_name}`
  return '—'
}

// 2026-07-30：树表 row-key（避免顶层与子件 id 冲突）
function rowKey(row: PartListItem): string {
  if (row.row_type === 'ASSEMBLY') return `ASM_${row.id}`
  if ((row as any).__is_child) return `CHILD_${row.id}`
  return `PART_${row.id}`
}

// 2026-07-30：懒加载装配件子件
// 2026-08-05 C2：优先消费 row.matched_children（位置类筛选激活时后端已带出
// 命中子件全集），避免每次展开都触发 /assemblies/{id} 详情查询。
async function loadChildren(
  row: PartListItem,
  _treeNode: unknown,
  resolve: (children: PartListItem[]) => void,
): Promise<void> {
  if (row.row_type !== 'ASSEMBLY') {
    resolve([])
    return
  }
  if (row.matched_children) {
    resolve(
      row.matched_children.map((c) => ({
        ...c,
        __is_child: true,
        row_type: 'PART' as const,
        has_children: false,
      })),
    )
    return
  }
  try {
    const detail = await getAssembly(row.id)
    const children = (detail.children ?? []).map((child) => ({
      ...child,
      __is_child: true,
      row_type: 'PART' as const,
      has_children: false,
    })) as PartListItem[]
    resolve(children)
  } catch {
    resolve([])
  }
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
// selectedRows、row-click 切换、selectable 守卫（isBatchSelectable，print 全状态可
// 选、dispatch 仅 PENDING）。跨页选择由 selectedIds 维护真实状态。
const batchMode = ref(false)
const batchAction = ref<'print' | 'dispatch'>('print')
const selectedRows = ref<PartListItem[]>([])
/** 跨页选择真实状态来源：所有已选行的 id（含非当前页）。
 * 2026-07-22 修复：必须用 reactive 包一层，否则模板里的 .size 不响应，count 永远 0、按钮永远 disabled。 */
const selectedIds = reactive(new Set<string>())
const batchPrinting = ref(false)
const batchPrintProgress = ref(0)
const batchPrintCurrent = ref(0)
const batchPrintTotal = ref(0)
const batchPrintIframeRef = ref<HTMLIFrameElement | null>(null)
let batchPrintBlobUrl = ''
/** ResponsiveList 内 el-table ref；用于 row-click 切换 / 全选 / 清空时同步 UI */
const partsListRef = ref<InstanceType<typeof ResponsiveList> | null>(null)

function isBatchSelectable(row: PartListItem): boolean {
  if (batchAction.value === 'print') {
    // 2026-08-01 (revised)：批量打印允许勾选所有顶层行——
    //   独立零件（row_type='PART' && !__is_child）+ 装配件行（row_type='ASSEMBLY'）。
    // 顶层行在 el-table 中即为最外层可见行（items.value 顶层）；子件 row_key 是
    // CHILD_${id}，loadChildren 设了 __is_child=true，必须禁用避免双重打印。
    // 单个子件打印走 PartDetail 详情页（FileListCard → printPartDrawing）。
    return !(row as { __is_child?: boolean }).__is_child
  }
  // 下发模式：仅未下发零件（PENDING）；保持原语义，装配件+子件都不能整批下发。
  return row.status === 'PENDING' && row.row_type !== 'ASSEMBLY'
}

/** 2026-07-30：记录每个选中 id 的行类型，用于批量打印拆分 */
const selectedRowTypes = reactive(new Map<string, 'PART' | 'ASSEMBLY'>())

// 2026-07-31：批量栏拆分计数（零件 / 装配件）。两个 computed 双校验：
// 只把「仍在 selectedIds 中 + 有类型记录」的 id 计进来，避免 selectedRowTypes
// 残留 id 被算成有效计数。
const batchSelectedPartCount = computed(() => {
  let n = 0
  for (const [id, t] of selectedRowTypes) {
    if (selectedIds.has(id) && t !== 'ASSEMBLY') n++
  }
  return n
})
const batchSelectedAssemblyCount = computed(() => {
  let n = 0
  for (const [id, t] of selectedRowTypes) {
    if (selectedIds.has(id) && t === 'ASSEMBLY') n++
  }
  return n
})

function clearAllSelection(): void {
  selectedIds.clear()
  selectedRows.value = []
  selectedRowTypes.clear()
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
  // 按 ID 合并：先移除当前页所有 ID（不论是否还在 rows 中），再加入 rows 中可选行的 ID
  // 关键：必须同步清理 selectedRowTypes，否则取消勾选会在 Map 里残留，
  // onBatchPrint 遍历 selectedRowTypes 时会把残留 id 当成有效选择送给后端（Bug 3）。
  const currentPageIds = new Set(items.value.map((r) => r.id))
  for (const id of [...selectedIds]) {
    if (currentPageIds.has(id)) {
      selectedIds.delete(id)
      selectedRowTypes.delete(id)
    }
  }
  for (const r of rows) {
    if (isBatchSelectable(r)) {
      selectedIds.add(r.id)
      selectedRowTypes.set(r.id, r.row_type === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART')
    }
  }
  rebuildSelectedRows(rows)
}
function onSelectAllPage(): void {
  // 只勾选当前页的可选行
  const table = partsListRef.value?.elTableRef
  if (!table) return
  for (const row of items.value) {
    if (isBatchSelectable(row)) {
      table.toggleRowSelection(row, true)
      selectedIds.add(row.id)
      selectedRowTypes.set(row.id, row.row_type === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART')
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
  // 视实现可能不触发），所以这里手动维护 selectedIds/selectedRows/selectedRowTypes。
  if (shouldSelect) {
    selectedIds.add(row.id)
    selectedRowTypes.set(row.id, row.row_type === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART')
    if (!selectedRows.value.find((r) => r.id === row.id)) {
      selectedRows.value = [...selectedRows.value, row]
    }
  } else {
    selectedIds.delete(row.id)
    selectedRowTypes.delete(row.id)
    selectedRows.value = selectedRows.value.filter((r) => r.id !== row.id)
  }
}
/** fetchList 更新 items 后用 nextTick 恢复当前页 checkbox UI。
 * 2026-07-31：保留跨页选择（不主动剔除「不在当前页」的 id）；仅清理不可选项。
 * 必须同步清理 selectedRowTypes，否则 onBatchPrint 遍历 Map 时残留 id 会被当成
 * 有效选择送给后端（Bug 3 路径 2）。 */
function restoreTableSelection(): void {
  if (!batchMode.value) return
  const table = partsListRef.value?.elTableRef
  if (!table) return
  // 清理：仅剔除当前页里已变不可选的 id；翻页保留。
  for (const r of items.value) {
    if (!isBatchSelectable(r)) {
      selectedIds.delete(r.id)
      selectedRowTypes.delete(r.id)
    }
  }
  // 同步 row types（防止 items 刷新后类型变化，如装配件状态翻转）
  for (const r of items.value) {
    if (selectedIds.has(r.id)) {
      selectedRowTypes.set(r.id, r.row_type === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART')
    }
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
  if (selectedIds.size === 0) return

  const PART_BATCH_SIZE = 20
  const ASSEMBLY_BATCH_SIZE = 2
  const CONCURRENCY = 3

  batchPrinting.value = true
  try {
    const partIds: string[] = []
    const assemblyIds: string[] = []
    // 2026-07-31：以 selectedIds 为唯一来源遍历（之前用 selectedRowTypes 当主源
    // 会让取消勾选的残留 id 仍然送进后端，UI 计数 ≠ 实际打印集合）。
    for (const id of selectedIds) {
      const t = selectedRowTypes.get(id)
      if (t === 'ASSEMBLY') assemblyIds.push(id)
      else partIds.push(id)
    }

    // 构建批次队列：先零件后装配体
    const batches: { partIds: string[]; assemblyIds?: string[] }[] = []
    for (let i = 0; i < partIds.length; i += PART_BATCH_SIZE) {
      batches.push({ partIds: partIds.slice(i, i + PART_BATCH_SIZE) })
    }
    for (let i = 0; i < assemblyIds.length; i += ASSEMBLY_BATCH_SIZE) {
      batches.push({
        partIds: [],
        assemblyIds: assemblyIds.slice(i, i + ASSEMBLY_BATCH_SIZE),
      })
    }

    if (batches.length === 0) return

    batchPrintTotal.value = batches.length
    batchPrintCurrent.value = 0
    batchPrintProgress.value = 0

    let doneCount = 0
    const batchBlobs: Blob[] = new Array(batches.length)

    const tasks = batches.map((b, idx) => async () => {
      const blob = await printPartDrawingBatch(
        b.partIds,
        b.assemblyIds && b.assemblyIds.length > 0 ? b.assemblyIds : undefined,
      )
      batchBlobs[idx] = blob
      doneCount++
      batchPrintCurrent.value = doneCount
      batchPrintProgress.value = Math.round((doneCount / batches.length) * 100)
    })

    // 简易并发池（最多 CONCURRENCY 个并发）
    let nextIdx = 0
    async function worker(): Promise<Error | null> {
      while (nextIdx < tasks.length) {
        const i = nextIdx++
        try {
          await tasks[i]()
        } catch (err) {
          return err as Error
        }
      }
      return null
    }
    const errors = await Promise.all(Array.from({ length: CONCURRENCY }, () => worker()))
    const firstError = errors.find((e) => e !== null)
    if (firstError) {
      throw new Error(`第 ${batchPrintCurrent.value + 1} 批生成失败：${firstError.message}`)
    }

    // 浏览器端按批次顺序合并 PDF
    const mergedPdf = await PDFDocument.create()
    for (let i = 0; i < batchBlobs.length; i++) {
      const buf = await batchBlobs[i].arrayBuffer()
      const pdf = await PDFDocument.load(buf)
      const pages = await mergedPdf.copyPages(pdf, pdf.getPageIndices())
      for (const page of pages) {
        mergedPdf.addPage(page)
      }
    }
    const mergedBytes = await mergedPdf.save()
    const mergedBlob = new Blob([mergedBytes.buffer as ArrayBuffer], { type: 'application/pdf' })

    if (batchPrintBlobUrl) URL.revokeObjectURL(batchPrintBlobUrl)
    batchPrintBlobUrl = URL.createObjectURL(mergedBlob)

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
    await ElMessageBox.alert(
      (e as Error).message ?? '批量打印失败',
      '错误',
      { confirmButtonText: '确定', type: 'error' },
    )
  } finally {
    batchPrintTotal.value = 0
    batchPrintCurrent.value = 0
    batchPrintProgress.value = 0
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
  prop: PART_SORT_KEY_TO_PROP[sortBy.value] ?? 'planned_delivery_date',
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
    // 2026-08-20：图号 / 名称拆为两个独立 ILIKE 子串参数；同时设 ⇒ AND 联合。
    drawing_no: search.drawingNo.trim() || undefined,
    name: search.name.trim() || undefined,
    // 2026-08-20：keyword 字段由 /parts 已弃用；保留 search.keyword 是为了兼容
    // useListFilterPersist 旧快照与潜在外部 caller（不发送到后端）。
    // keyword: search.keyword.trim() || undefined,
    order_no: search.orderNo.trim() || undefined,
    // 2026-07-31：序列号独立搜索（ILIKE 包含；装配件子序列号自动带出母装配件）
    serial_no: search.serialNo.trim() || undefined,
    request_date_from: search.requestDateFrom || undefined,
    request_date_to: search.requestDateTo || undefined,
    planned_delivery_date_from: search.plannedDeliveryDateFrom || undefined,
    planned_delivery_date_to: search.plannedDeliveryDateTo || undefined,
    system_delivery_date_from: search.systemDeliveryDateFrom || undefined,
    system_delivery_date_to: search.systemDeliveryDateTo || undefined,
    // 2026-08-11：可空列空白筛选。`=== true` 守卫：未勾选（undefined）不发参数，
    // 由 cleanParams 自然 strip；显式发送 true/false 仅在 UI 真勾选/显式 false 时。
    order_no_is_null: search.orderNoIsNull === true ? true : undefined,
    system_delivery_date_is_null:
      search.systemDeliveryDateIsNull === true ? true : undefined,
    // 2026-08-05：下一道工序 / 物理位置多选筛选。
    // 雪花 ID 一律以字符串直接传给后端（CLAUDE.md §3）——禁止 Number()，
    // 否则 19 位 ID 在 JS Number（MAX_SAFE_INTEGER≈9.007e15）丢精度，IN 永不命中。
    // 空数组 = undefined（不发参数，保留现有清空过滤行为）。
    next_process_ids:
      search.nextProcessIds.length > 0 ? search.nextProcessIds : undefined,
    locations: search.locations.length > 0 ? search.locations : undefined,
    holder_ids: search.holderIds.length > 0 ? search.holderIds : undefined,
    row_type: search.rowType !== 'ALL' ? search.rowType : undefined,
    sort_by: sortBy.value,
    sort_dir: sortDir.value,
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
    include_assemblies: true,
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
  // 2026-07-31：关键词/订单号/日期区间变化即视为「改筛选」，清空批量选择。
  // onSearch 也是 confirmStatusFilter / confirmCustomerFilter / resetStatusDraft /
  // resetCustomerDraft / confirmMobileFilter / resetMobileFilter / onReset 的统一入口，
  // 但各 popover 内「重置」分支已自己拼 clearAllSelection 之外的逻辑；这里再覆盖一层防御。
  if (batchMode.value) clearAllSelection()
  void fetchList()
}

// 2026-08-05：行类型切换——ALL↔PART/ASSEMBLY 视为筛选条件变化，复用 onSearch 入口
// （清空批量选择 + fetchList）。
function onRowTypeChange(): void {
  onSearch()
}

// 2026-08-04：扫码直接按序列号搜索——清空其它筛选条件（用户决定），只保留 serialNo 搜索。
// 用户在 serialNo 输入框聚焦时由 useBarcodeScanner 的 isInTextField 守卫自动跳过；
// 行内编辑中也不要打断，所以 editingId 非空时静默返回。
function onSerialNoScan(rawCode: string): void {
  const code = rawCode.trim()
  if (!code) return
  if (editingId.value !== null) return
  // 清空所有筛选（keyword/orderNo/drawingNo/name/serialNo/statuses/isUrgent/customerId/
  // 3 个日期区间/nextProcessIds/locations），只保留 serialNo 搜索。
  search.keyword = ''
  search.drawingNo = ''
  search.name = ''
  search.orderNo = ''
  search.orderNoIsNull = undefined
  search.serialNo = code
  search.statuses = []
  search.isUrgent = null
  search.customerId = ''
  search.requestDateFrom = ''
  search.requestDateTo = ''
  search.plannedDeliveryDateFrom = ''
  search.plannedDeliveryDateTo = ''
  search.systemDeliveryDateFrom = ''
  search.systemDeliveryDateTo = ''
  search.systemDeliveryDateIsNull = undefined
  search.nextProcessIds = []
  search.locations = []
  search.holderIds = []
  // 同步刷新 popover 内 draft 状态（避免下次打开还看到旧的）。
  statusDraft.value = []
  statusUrgentDraft.value = false
  nextProcessDraft.value = []
  customerDraft.value = null
  locationDraft.value = []
  drawingNoDraft.value = ''
  nameDraft.value = ''
  orderNoDraft.value = ''
  orderNoIsNullDraft.value = undefined
  serialNoDraft.value = code
  systemDateIsNullDraft.value = undefined
  // 序列号 popover 自动打开，便于用户看到 scan-flash 0.6s 脉冲动画。
  serialNoPopoverVisible.value = true
  // 持久化（与 onReset 同步写 localStorage）。
  snapshotPartsFilter()
  // 触发查询（onSearch 内会清空批量选择 + fetchList）。
  onSearch()
  // 视觉反馈：serialNo 输入框脉冲动画 0.6s。
  serialNoFlash.value = true
  setTimeout(() => { serialNoFlash.value = false }, 600)
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
const { restore: restorePartsFilter, clear: clearPartsFilter, snapshot: snapshotPartsFilter } =
  useListFilterPersist<SearchState>(
    'parts_list_filter',
    { search, sortBy, sortDir, pageSize },
  )

// ============ 列可见性 ============
// 「操作」和 batch 模式下的「selection」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'serial_no', label: '序列号' },
  { key: 'order_no', label: '订单号' },
  { key: 'drawing_no', label: '图号' },
  { key: 'name', label: '名称' },
  { key: 'customer', label: '客户' },
  { key: 'applicant', label: '申请人' },
  { key: 'status', label: '状态' },
  { key: 'quantity', label: '数量' },
  { key: 'unit_price', label: '单价' },
  { key: 'total_price', label: '总价' },
  { key: 'request_date', label: '请购日期' },
  { key: 'planned_delivery_date', label: '计划交期' },
  { key: 'system_delivery_date', label: '系统交期' },
  { key: 'delivered_quantity', label: '已送数量' },
  { key: 'is_urgent', label: '加急' },
  { key: 'next_process', label: '下一道工序' },  // 2026-08-01 新增
  { key: 'location', label: '所在位置' },
  { key: 'note', label: '备注' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'parts_list_columns' })

function onReset(): void {
  // 2026-07-29 PR-fix-0.2.0：重置只清两个查询框 + 三个日期区间，保留 status / customer
  // popover 选择、排序、分页大小。表头排序、列过滤器不受重置影响。
  search.keyword = ''
  search.drawingNo = ''
  search.name = ''
  search.orderNo = ''
  search.serialNo = ''
  search.requestDateFrom = ''
  search.requestDateTo = ''
  search.plannedDeliveryDateFrom = ''
  search.plannedDeliveryDateTo = ''
  search.systemDeliveryDateFrom = ''
  search.systemDeliveryDateTo = ''
  // 2026-08-11：清空两个空白筛选 checkbox。
  search.orderNoIsNull = undefined
  search.systemDeliveryDateIsNull = undefined
  page.value = 1
  // 2026-07-31：重置按钮清空批量选择（与「改筛选即清空」语义一致）
  if (batchMode.value) clearAllSelection()
  // 写回 localStorage：保留 sortBy / sortDir / pageSize / statuses / isUrgent / customerId，
  // 仅清空 keyword / orderNo / 三个日期区间。下次刷新页面恢复的就是这种"半清空"状态。
  snapshotPartsFilter()
  void fetchList()
}

// 2026-08-20：可空列空白筛选 checkbox 改在对应列 popover 内通过
// `:model-value` + `@update:model-value` 内联桥接（search.orderNoIsNull
// / systemDeliveryDateIsNull），不再需要 onOrderNoIsNullChange /
// onSystemDeliveryDateIsNullChange 这两个独立 handler。
// / systemDeliveryDateIsNull），不再需要 onOrderNoIsNullChange /
// onSystemDeliveryDateIsNullChange 这两个独立 handler。

onMounted(async () => {
  // 1) 优先尝试从 URL ?status=PENDING 注入（与批量新建后跳转保持一致）
  const q = route.query.status
  if (typeof q === 'string' && q in ORDER_STATUS_LABEL) {
    search.statuses = [q as OrderStatus]
  } else {
    // 2) 否则从 localStorage 恢复上次的筛选 / 排序 / 分页大小
    const persisted = restorePartsFilter()
    if (persisted) {
      search.keyword = persisted.search.keyword ?? search.keyword
      // 2026-08-20：drawingNo / name 旧快照缺失走 '' 兜底。
      search.drawingNo = persisted.search.drawingNo ?? search.drawingNo
      search.name = persisted.search.name ?? search.name
      search.orderNo = persisted.search.orderNo ?? search.orderNo
      // 2026-07-31：序列号独立搜索字段恢复
      search.serialNo = persisted.search.serialNo ?? search.serialNo
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
      // 2026-08-01：下一道工序 / 物理位置多选恢复（lenient：旧快照缺字段=空数组）
      search.nextProcessIds = Array.isArray(persisted.search.nextProcessIds)
        ? persisted.search.nextProcessIds
        : []
      search.locations = Array.isArray(persisted.search.locations)
        ? persisted.search.locations
        : []
      // 2026-08-05：holder 叶子多选恢复（lenient：旧快照缺字段=空数组）
      search.holderIds = Array.isArray(persisted.search.holderIds)
        ? persisted.search.holderIds
        : []
      // 2026-08-05：行类型筛选恢复（合法值收敛，默认 ALL）
      search.rowType =
        persisted.search?.rowType === 'PART' || persisted.search?.rowType === 'ASSEMBLY'
          ? persisted.search.rowType
          : 'ALL'
      // localStorage 存的是 string，恢复时按合法值收敛（默认值兜底）
      sortBy.value = PART_SORT_KEY_SET.has(persisted.sortBy as PartSortKey)
        ? (persisted.sortBy as PartSortKey)
        : 'PLANNED_DELIVERY_DATE'
      sortDir.value = (persisted.sortDir === 'ASC' || persisted.sortDir === 'DESC'
        ? persisted.sortDir as SortDir
        : 'ASC')
      pageSize.value = persisted.pageSize
    }
  }
  void fetchList()
  // 2026-07-29 PR-fix-0.2.0：表头排序箭头要等 el-table 挂载后手动调一次 sort()，
  // 否则离开页面再回来时 refs 已恢复但表头不显示箭头（:default-sort 是 one-time prop）。
  await nextTick()
  const sortProp = PART_SORT_KEY_TO_PROP[sortBy.value] ?? 'planned_delivery_date'
  const sortOrder = sortDir.value === 'ASC' ? 'ascending' : 'descending'
  partsListRef.value?.elTableRef?.sort(sortProp, sortOrder)
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
  // 2026-08-20：申请人 autocomplete 按行所在客户懒加载全集。
  // loadForCustomer 内部对同 rootCustomerId 幂等，切到不同行时自动 refetch。
  void loadForCustomer(resolveRootCustomerForRow(row))
}

// 2026-07-24：双击行进入编辑（仅 MANAGER/CLERK + 非批量模式）
// 2026-07-31：装配件行同样支持行内编辑（AssemblyUpdatePayload 字段与 PartUpdatePayload
// 一致，saveEdit 按 row.row_type 分流到 updateAssembly）。终态由后端 BIZ_INVALID_TRANSITION
// 拦截。
function onRowDblClick(row: PartListItem): void {
  if (!canEdit) return
  if (batchMode.value) return  // 批量模式下双击由 onBatchRowClick 处理，不进编辑
  startEdit(row)
}

// 2026-07-24：编辑态下回车键保存
// 黑名单：搜索框（.filter-card）/ 日期 picker / 下拉 popper
const ENTER_BLACKLIST = [
  '.filter-card',
  '.el-popper.is-light',
  '.el-select-dropdown',
  '.el-tree-select__popper',
  '.el-cascader__dropdown',
  '.el-date-picker',
]
function onEditEnter(e: KeyboardEvent): void {
  // ESC: cancel edit (same blacklist as Enter to avoid stealing from dropdowns/date-pickers)
  if (e.key === 'Escape') {
    if (editingId.value == null) return
    const target = e.target as HTMLElement | null
    if (target && ENTER_BLACKLIST.some((sel) => target.closest(sel))) return
    e.preventDefault()
    cancelEdit()
    return
  }
  if (e.key !== 'Enter') return
  if (editingId.value == null) return
  const target = e.target as HTMLElement | null
  if (target && ENTER_BLACKLIST.some((sel) => target.closest(sel))) return
  e.preventDefault()
  const row = items.value.find((r) => r.id === editingId.value)
  if (row) void saveEdit(row)
}

watch(editingId, (val) => {
  if (typeof document === 'undefined') return
  if (val != null) {
    document.addEventListener('keydown', onEditEnter)
  } else {
    document.removeEventListener('keydown', onEditEnter)
  }
})

// 2026-08-04：扫码枪扫描序列号直接搜索（与 onReset 类似但保留 serialNo）。
const { onScan } = useBarcodeScanner()
const unsubPartsListScan = onScan((code) => { onSerialNoScan(code) })

onBeforeUnmount(() => {
  if (typeof document === 'undefined') return
  document.removeEventListener('keydown', onEditEnter)
  unsubPartsListScan()
})

// 2026-07-24 v2：总价列响应式显示（编辑态用 editBuffer，非编辑态用 row）
function displayTotalPrice(row: PartListItem): string {
  // 编辑态：从 editBuffer 实时算（数量/单价改动立刻反映在总价列）
  if (editingId.value === row.id) {
    const q = Number(editBuffer.quantity ?? row.quantity)
    const p = Number(editBuffer.unit_price ?? row.unit_price)
    return Number.isFinite(q) && Number.isFinite(p) ? (q * p).toFixed(2) : '—'
  }
  // 非编辑态：用行内字段实时算（与后端落库的 row.total_price 一致或更准）
  const q = Number(row.quantity)
  const p = Number(row.unit_price)
  return Number.isFinite(q) && Number.isFinite(p) ? (q * p).toFixed(2) : '—'
}

// 2026-07-24 v2：表格底部合计行（仅总价列求和）
const totalPriceSummary: SummaryMethod<PartListItem> = ({ columns, data }) => {
  return columns.map((col, index) => {
    if (col.label === '总价') {
      const total = data.reduce((sum, row) => {
        const q = Number(row.quantity ?? 0)
        const p = Number(row.unit_price ?? 0)
        return sum + (Number.isFinite(q) && Number.isFinite(p) ? q * p : 0)
      }, 0)
      return total.toFixed(2)
    }
    // 第一列（序列号 / selection）放"合计"label，其他列空字符串
    if (index === 0) return '合计'
    return ''
  })
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
    // 2026-07-31：装配件字段名相同，按 row.row_type 复用同一 buffer 路由。
    if (row.row_type === 'ASSEMBLY') {
      await updateAssembly(row.id, payload)
    } else {
      await updatePart(row.id, payload)
    }
    // updatePart 返回 PartOut（不含 applicant_name/request_date/unit_price），
    // updateAssembly 返回 AssemblyDetail（顶层 + 子件）。就地回填该行用 buffer 值，
    // 避免整表刷新的闪烁。
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
    // 40901 = BIZ_VERSION_CONFLICT（乐观锁冲突）；装配件 update 不发 409，
    // 但保留分支以兼容未来 OCC 接入。
    if ((e as { code?: number }).code === 40901) {
      ElMessage.warning('该记录已被他人修改，已为你刷新列表')
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

// ============ 召回（2026-08-05）============
async function onRecallToPending(row: PartListItem): Promise<void> {
  const label = row.serial_no || row.drawing_no || row.id
  try {
    await ElMessageBox.confirm(
      `确认召回「${label}」为待生产？`,
      '召回确认',
      { type: 'warning', confirmButtonText: '确认召回', cancelButtonText: '取消' },
    )
  } catch {
    // 用户取消
    return
  }
  try {
    await recallToPending(row.id, { batch_id: row.batch_id ?? null })
    ElMessage.success('已召回为待生产')
    void fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '召回失败')
  }
}

async function onRecallToProgramming(row: PartListItem): Promise<void> {
  const label = row.serial_no || row.drawing_no || row.id
  try {
    await ElMessageBox.confirm(
      `确认召回「${label}」为待编程？`,
      '召回确认',
      { type: 'warning', confirmButtonText: '确认召回', cancelButtonText: '取消' },
    )
  } catch {
    // 用户取消
    return
  }
  try {
    await recallToProgramming(row.id, { batch_id: row.batch_id ?? null })
    ElMessage.success('已召回为待编程')
    void fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '召回失败')
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
        // 成功项：移出三个状态源（selectedIds / selectedRowTypes / selectedRows）
        selectedIds.delete(t.id)
        selectedRowTypes.delete(t.id)
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

/* 2026-08-20：原 .filter-group--dates / .date-filter-item / .date-filter-label /
   .filter-blank 已被「查询」表头 popover 取代，残留样式删除。 */

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
/* 2026-07-31：装配件计数与提示图标 */
.batch-bar .bar-info__assembly {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.batch-bar .batch-hint {
  color: var(--el-color-warning);
  cursor: help;
  font-size: 14px;
}
.batch-print-progress {
  flex: 1;
  margin: 0 12px;
}
.batch-print-progress__text {
  text-align: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
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
  // 2026-07-31：激活态列标题同步变蓝加粗（与 filter-icon.active 共享视觉信号）
  &.is-active {
    color: var(--primary-color);
    font-weight: 600;
  }
}

.filter-icon {
  font-size: 14px;
  color: var(--text-secondary);
  cursor: pointer;
  position: relative; // 为 ::after 圆点做定位锚点
  &.active {
    color: var(--primary-color);
  }
  // 2026-07-31：激活态右上角加蓝圆点（与图标颜色、文字加粗三重信号）
  &.active::after {
    content: '';
    position: absolute;
    top: -2px;
    right: -2px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--primary-color);
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

// 2026-08-20：列头 popover 内的单行排版（input + 仅空白 checkbox）。
// 与外层 .filter-row（顶部三组分类）同名冲突，故单独命名。
.filter-input-row {
  display: flex;
  align-items: center;
  gap: 8px;
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

// 2026-08-04：扫码命中序列号时输入框 0.6s 脉冲动画
@keyframes scanFlash {
  0%   { box-shadow: 0 0 0 0 rgba(64, 158, 255, 0.5); }
  100% { box-shadow: 0 0 0 6px rgba(64, 158, 255, 0);   }
}
.scan-flash { animation: scanFlash 0.6s ease-out; }
</style>
