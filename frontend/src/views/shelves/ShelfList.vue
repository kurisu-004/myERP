<template>
  <div class="shelf-page">
    <div class="page-header">
      <h2>货架管理</h2>
      <el-button type="primary" @click="showCreate = true">新增货架</el-button>
    </div>
    <ResponsiveList
      :items="items"
      :loading="loading"
      row-key="id"
      empty-text="暂无货架"
      stripe
      :default-sort="{ prop: 'display_order', order: 'ascending' }"
    >
      <template #toolbar>
        <ColumnVisibilityPopover
          :defs="columnDefs"
          :model-value="columnVisibility.currentMap" @update:model-value="columnVisibility.update"
          @reset="columnVisibility.showAll"
        />
      </template>
      <el-table-column
        v-if="columnVisibility.isVisible('code')"
        prop="code" label="代码" min-width="110" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('name')"
        prop="name" label="名称" min-width="140" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('zone')"
        label="区域" min-width="90" align="center"
      >
        <template #default="{ row }"><el-tag :type="row.zone === 'PRODUCTION' ? 'primary' : 'warning'" size="small">{{ row.zone === 'PRODUCTION' ? '生产' : '品检' }}</el-tag></template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('location')"
        prop="location" label="位置" min-width="120" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('display_order')"
        prop="display_order" label="物理顺序" min-width="100" align="center" sortable
      >
        <template #default="{ row }">
          <el-tag
            :type="row.display_order > 0 ? 'info' : 'warning'"
            size="small"
            effect="plain"
          >
            {{ row.display_order > 0 ? row.display_order : '未设置' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="columnVisibility.isVisible('account_count')"
        prop="account_count" label="账号数" min-width="80" align="center"
      />
      <el-table-column
        v-if="columnVisibility.isVisible('is_active')"
        label="状态" min-width="80" align="center"
      >
        <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'danger'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" min-width="160" fixed="right" align="center">
        <template #default="{ row }">
          <el-button link size="small" @click="editShelf(row)">编辑</el-button>
          <el-popconfirm v-if="row.is_active" title="确认停用？" @confirm="doDeactivate(String(row.id))">
            <template #reference><el-button link size="small" type="danger">停用</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>

      <!-- 手机卡片 -->
      <template #card="{ row }">
        <div class="rl-card-head">
          <span class="rl-card-title">{{ row.name }}</span>
          <el-tag :type="row.is_active ? 'success' : 'danger'" size="small" effect="plain">
            {{ row.is_active ? '启用' : '停用' }}
          </el-tag>
        </div>
        <div class="rl-card-sub">
          {{ row.code }} · {{ row.zone === 'PRODUCTION' ? '生产区' : '品检区' }}
        </div>
        <div class="rl-kv">
          <div class="rl-kv__item">
            <span class="rl-kv__key">位置</span>
            <span class="rl-kv__val">{{ row.location || '—' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">物理顺序</span>
            <span class="rl-kv__val">{{ row.display_order > 0 ? row.display_order : '未设置' }}</span>
          </div>
          <div class="rl-kv__item">
            <span class="rl-kv__key">账号数</span>
            <span class="rl-kv__val">{{ row.account_count ?? 0 }}</span>
          </div>
        </div>
        <div class="rl-card-actions">
          <el-button link size="small" @click="editShelf(row)">编辑</el-button>
          <el-popconfirm v-if="row.is_active" title="确认停用？" @confirm="doDeactivate(String(row.id))">
            <template #reference><el-button link size="small" type="danger">停用</el-button></template>
          </el-popconfirm>
        </div>
      </template>
    </ResponsiveList>

    <el-dialog
      v-model="showCreate"
      :title="editingShelf ? '编辑货架' : '新增货架'"
      :width="shelfDlg.width.value"
      :top="shelfDlg.top.value"
      :fullscreen="shelfDlg.fullscreen.value"
      @closed="resetForm"
    >
      <el-form ref="shelfFormRef" :model="shelfForm" :rules="shelfRules" label-width="80px">
        <el-form-item label="代码" prop="code"><el-input v-model="shelfForm.code" :disabled="!!editingShelf" placeholder="如 PROD-A1" /></el-form-item>
        <el-form-item label="名称" prop="name"><el-input v-model="shelfForm.name" placeholder="如 生产区-A1 货架" /></el-form-item>
        <el-form-item label="区域" prop="zone">
          <el-select v-model="shelfForm.zone" style="width:100%">
            <el-option label="生产区" value="PRODUCTION" /><el-option label="品检区" value="INSPECTION" />
          </el-select>
        </el-form-item>
        <el-form-item label="位置" prop="location"><el-input v-model="shelfForm.location" placeholder="可选的自由文本" /></el-form-item>
        <el-form-item label="物理顺序" prop="display_order">
          <el-input-number
            v-model="shelfForm.display_order"
            :min="0"
            :step="1"
            controls-position="right"
            placeholder="0=未设置"
          />
          <span class="field-hint">用于共享 HMI 卡片网格 picker 的物理顺序；0=未设置</span>
        </el-form-item>
        <el-form-item label="工序">
          <el-select
            v-model="selectedProcessIds"
            multiple filterable
            placeholder="选择该货架可执行的工序（可多选）"
            style="width:100%"
          >
            <el-option
              v-for="p in allProcesses"
              :key="p.id"
              :label="`${p.code} — ${p.name}`"
              :value="p.id"
            >
              <span style="font-weight:600">{{ p.code }}</span>
              <span style="margin-left:4px">{{ p.name }}</span>
              <el-tag :type="p.category === 'INHOUSE' ? 'primary' : 'warning'" size="small" style="margin-left:6px">{{ PROCESS_CATEGORY_LABEL[p.category] }}</el-tag>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="saveShelf" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import ResponsiveList from '@/components/ResponsiveList.vue'
import ColumnVisibilityPopover from '@/components/ColumnVisibilityPopover.vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useColumnVisibility } from '@/composables/useColumnVisibility'
import { useDialogSize } from '@/composables/useDialogSize'
import { listShelves, createShelf, updateShelf, deactivateShelf, getShelfProcesses, setShelfProcesses } from '@/api/shelves'
import { listProcesses } from '@/api/process'
import type { Shelf } from '@/types/shelf'
import type { Process } from '@/types/process'
import { PROCESS_CATEGORY_LABEL } from '@/types/process'

const { isMobile } = useBreakpoint()

// ============ 列可见性 ============
// 「操作」列不放进 defs → 始终可见
const columnDefs = [
  { key: 'code', label: '代码' },
  { key: 'name', label: '名称' },
  { key: 'zone', label: '区域' },
  { key: 'location', label: '位置' },
  { key: 'display_order', label: '物理顺序' },
  { key: 'account_count', label: '账号数' },
  { key: 'is_active', label: '状态' },
] as const
const columnVisibility = useColumnVisibility(columnDefs, { listKey: 'shelf_list' })

const items = ref<Shelf[]>([])
const loading = ref(false)
const shelfDlg = useDialogSize({ desktopWidth: 400 })

const showCreate = ref(false)
const saving = ref(false)
const allProcesses = ref<Process[]>([])
const selectedProcessIds = ref<string[]>([])
const editingShelf = ref<Shelf | null>(null)
const shelfFormRef = ref()
const shelfForm = reactive({ code: '', name: '', zone: 'PRODUCTION' as string, location: '', display_order: 0 })
const shelfRules = {
  code: [{ required: true, message: '必填' }],
  name: [{ required: true, message: '必填' }],
  zone: [{ required: true, message: '必选' }],
}

async function fetchData() {
  loading.value = true
  try { items.value = (await listShelves({ limit: 200 })).items }
  finally { loading.value = false }
}

function resetForm() { shelfForm.code = ''; shelfForm.name = ''; shelfForm.zone = 'PRODUCTION'; shelfForm.location = ''; shelfForm.display_order = 0; selectedProcessIds.value = []; editingShelf.value = null }
async function editShelf(s: any) { const sh = s as Shelf; editingShelf.value = sh; shelfForm.code = sh.code; shelfForm.name = sh.name; shelfForm.zone = sh.zone; shelfForm.location = sh.location ?? ''; shelfForm.display_order = sh.display_order ?? 0; showCreate.value = true; try { const sp = await getShelfProcesses(String(sh.id)); selectedProcessIds.value = sp.processes.map((p) => p.process_id) } catch { selectedProcessIds.value = [] } }

async function saveShelf() {
  const valid = await shelfFormRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    let shelfId: string
    if (editingShelf.value) {
      await updateShelf(String(editingShelf.value.id), {
        name: shelfForm.name,
        location: shelfForm.location || undefined,
        display_order: shelfForm.display_order,
      })
      shelfId = String(editingShelf.value.id)
    } else {
      const created = await createShelf({
        code: shelfForm.code,
        name: shelfForm.name,
        zone: shelfForm.zone,
        location: shelfForm.location || undefined,
        display_order: shelfForm.display_order,
      })
      shelfId = String(created.id)
    }
    await setShelfProcesses(shelfId, { process_ids: selectedProcessIds.value })
    showCreate.value = false
    await fetchData()
    ElMessage.success('已保存')
  } catch (e: any) { ElMessage.error(e?.message || '保存失败') }
  finally { saving.value = false }
}

async function doDeactivate(id: string) { await deactivateShelf(id); await fetchData(); ElMessage.success('已停用') }

onMounted(async () => {
  await fetchData()
  try { const res = await listProcesses({ limit: 200 }); allProcesses.value = res.items } catch { /* ignore */ }
})
</script>

<style lang="scss" scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; h2 { margin: 0; font-size: 18px; } }
.field-hint {
  margin-left: 12px;
  font-size: 12px;
  color: #909399;
}
</style>
