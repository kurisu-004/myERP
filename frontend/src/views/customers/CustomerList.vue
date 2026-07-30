<!--
  CustomerList.vue

  /customers — 客户管理 → 客户一览。

  展示形式：el-tree 一级/二级树形结构，每个节点 hover 出现「+/编辑/删除」按钮：
    - 一级节点：可「+」加二级子节点，可编辑/删除（无子节点且未被引用时）
    - 二级节点：可编辑/删除（未被零件或装配体引用时）

  数据：调 `listCustomers()` 拉全量平铺数据，前端拼成 2 级树。

  2026-07-09 起一级客户新增 `serial_prefix`（A-Z 单字符）字段：
  - 弹窗 el-form 多一个 `el-form-item`，仅在 parentId 为空时启用 + 必填；
    切到二级（选了父客户）时禁用并清空。
  - 树行 hover 槽内，根节点名前挂一个 `el-tag` 显示当前前缀字母。
    叶子节点不显示 tag。
-->
<template>
  <div class="customer-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline class="customer-filter-form">
        <el-form-item label="客户名">
          <el-input
            v-model="search.keyword"
            placeholder="按客户名过滤"
            clearable
            style="width: 100%; max-width: 260px"
            @keyup.enter="applyFilter"
            @clear="applyFilter"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="applyFilter">
            <el-icon><Search /></el-icon><span>查询</span>
          </el-button>
          <el-button @click="onReset">
            <el-icon><RefreshLeft /></el-icon><span>重置</span>
          </el-button>
          <el-button type="success" @click="onNewRoot">
            <el-icon><Plus /></el-icon><span>新增一级客户</span>
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" v-loading="loading">
      <el-tree
        v-if="filteredTree.length > 0"
        :data="filteredTree"
        :props="{ label: 'name', children: 'children' }"
        node-key="id"
        default-expand-all
        :expand-on-click-node="false"
      >
        <template #default="{ data }">
          <div class="tree-row">
            <!-- 一级客户：前缀 tag 挂在名字前面 -->
            <el-tag
              v-if="(data as TreeNode).parent_id === null && (data as TreeNode).serial_prefix"
              size="small"
              effect="dark"
              type="primary"
              class="tree-row__prefix"
            >
              {{ (data as TreeNode).serial_prefix }}
            </el-tag>
            <el-tag
              v-else-if="(data as TreeNode).parent_id === null"
              size="small"
              effect="plain"
              type="info"
              class="tree-row__prefix"
            >
              未设置
            </el-tag>
            <span class="tree-row__name">{{ (data as TreeNode).name }}</span>
            <span class="tree-row__actions">
              <el-button
                v-if="(data as TreeNode).parent_id === null"
                link
                type="primary"
                size="small"
                @click="onAddChild(data as TreeNode)"
              >
                + 子客户
              </el-button>
              <el-button link type="primary" size="small" @click="onEdit(data as TreeNode)">编辑</el-button>
              <el-button link type="danger" size="small" @click="onDelete(data as TreeNode)">删除</el-button>
            </span>
          </div>
        </template>
      </el-tree>
      <el-empty v-else description="暂无客户" />
    </el-card>

    <!-- 新增 / 编辑 Dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      :width="customerDlg.width.value"
      :top="customerDlg.top.value"
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
        <el-form-item label="客户名" prop="name">
          <el-input
            v-model="form.name"
            placeholder="例如：法拉电子 / 母排厂一组"
            maxlength="100"
            show-word-limit
          />
        </el-form-item>

        <!-- 父客户：
             - newRoot 模式下整项隐藏（parent_id 隐式为 NULL）；
             - newChild 模式下显示为禁用的 el-input，展示锁定的父客户名；
             - edit 模式下保留 el-select（clearable，可改）。 -->
        <el-form-item
          v-if="mode !== 'newRoot'"
          label="父客户"
          prop="parentId"
        >
          <el-input
            v-if="mode === 'newChild'"
            :model-value="lockedParentName"
            disabled
          />
          <el-select
            v-else
            v-model="form.parentId"
            placeholder="不选 = 一级客户；选了一个一级客户 = 二级"
            clearable
            filterable
            style="width: 100%"
            @change="onParentChange"
          >
            <el-option
              v-for="r in rootOptions"
              :key="r.id"
              :label="r.name"
              :value="r.id"
            />
          </el-select>
          <p v-if="mode === 'edit'" class="form-hint">
            二级客户必须挂在一级客户下；编辑根时清空此项。
          </p>
        </el-form-item>

        <!-- 序列号前缀：
             - newChild 模式下整项隐藏（叶子继承父，无需设置）；
             - newRoot 必填；
             - edit 模式下根客户可改，叶子客户禁用（保持原行为）。 -->
        <el-form-item
          v-if="mode !== 'newChild'"
          label="序列号前缀"
          prop="serialPrefix"
        >
          <el-select
            v-model="form.serialPrefix"
            placeholder="一级客户必填 A-Z"
            clearable
            filterable
            :disabled="mode === 'edit' && form.parentId !== null"
            style="width: 100%"
          >
            <el-option
              v-for="p in serialPrefixOptions"
              :key="p"
              :label="p"
              :value="p"
            />
          </el-select>
          <p class="form-hint">
            一级客户的序列号前缀 A-Z；新建后可在客户一览再次修改（仅影响后续创建的零件/装配体流水号）。
          </p>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import {
  Plus,
  RefreshLeft,
  Search,
} from '@element-plus/icons-vue'
import { useDialogSize } from '@/composables/useDialogSize'
import { useListStatePersist } from '@/composables/useListFilterPersist'
import {
  createCustomer,
  listCustomers,
  softDeleteCustomer,
  updateCustomer,
  type Customer,
} from '@/api/customer'

interface TreeNode {
  id: string
  name: string
  parent_id: string | null
  parent_name: string | null
  serial_prefix: string | null
  children: TreeNode[]
}

const loading = ref(false)
const saving = ref(false)
const customers = ref<Customer[]>([])
const search = reactive({ keyword: '' })

// ============ 筛选状态持久化 ============
const { restore: restoreCustomerFilter, clear: clearCustomerFilter } = useListStatePersist(
  'customer_list',
  { search },
)

// 弹窗尺寸：桌面 480px，手机 92vw + 6vh
const customerDlg = useDialogSize({ desktopWidth: 480 })

// ===== 树形组装（沿用 AssemblyList / PartBatchNew 的模式） =====
const tree = computed<TreeNode[]>(() => {
  const all = customers.value
  const roots = all.filter((c) => c.parent_id === null)
  return roots.map((r) => ({
    id: r.id,
    name: r.name,
    parent_id: r.parent_id,
    parent_name: r.parent_name,
    serial_prefix: r.serial_prefix,
    children: all
      .filter((c) => c.parent_id === r.id)
      .map((c) => ({
        id: c.id,
        name: c.name,
        parent_id: c.parent_id,
        parent_name: r.name,
        serial_prefix: null,  // 叶子节点不展示 prefix
        children: [],
      })),
  }))
})

// 关键字过滤：父或子匹配则保留；保留父节点的祖先链
const filteredTree = computed<TreeNode[]>(() => {
  const kw = search.keyword.trim().toLowerCase()
  if (!kw) return tree.value
  const match = (n: { name: string }): boolean =>
    n.name.toLowerCase().includes(kw)
  return tree.value
    .map((r) => {
      const rootHit = match(r)
      const kids = r.children.filter(match)
      if (rootHit) return r
      if (kids.length > 0) return { ...r, children: kids }
      return null
    })
    .filter((n): n is TreeNode => n !== null)
})

const rootOptions = computed(() =>
  customers.value
    .filter((c) => c.parent_id === null)
    .map((c) => ({ id: c.id, name: c.name })),
)

// A-Z 单字符序列号前缀候选项（运行时生成，硬编码 26 行太啰嗦）
const SERIAL_PREFIX_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
const serialPrefixOptions = computed<string[]>(() =>
  SERIAL_PREFIX_LETTERS.split(''),
)

async function fetchList(): Promise<void> {
  loading.value = true
  try {
    customers.value = await listCustomers()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '客户列表加载失败')
  } finally {
    loading.value = false
  }
}

function applyFilter(): void {
  // 过滤是 computed 自动响应的；这里留 hook 给未来扩展
}
function onReset(): void {
  search.keyword = ''
}

// ===== Dialog 表单 =====
type DialogMode = 'newRoot' | 'newChild' | 'edit'

interface FormState {
  name: string
  parentId: string | null
  serialPrefix: string | null
}
const formRef = ref<FormInstance>()
const dialogVisible = ref(false)
const editing = ref<Customer | null>(null)
const mode = ref<DialogMode>('newRoot')
// 新增子客户场景：锁定显示的父客户名（不可改）。
const lockedParentName = ref<string>('')
const form = reactive<FormState>({ name: '', parentId: null, serialPrefix: null })

// 弹窗标题按 mode 分支
const dialogTitle = computed(() =>
  mode.value === 'newRoot'
    ? '新增一级客户'
    : mode.value === 'newChild'
      ? '新增子客户'
      : '编辑客户',
)

const rules: FormRules = {
  name: [{ required: true, message: '请输入客户名', trigger: 'blur' }],
  serialPrefix: [
    {
      validator: (_rule, value, callback) => {
        // 新增子客户：表单里没这字段，跳过
        if (mode.value === 'newChild') {
          callback()
          return
        }
        // 编辑叶子客户：前缀 disabled，service 层忽略，跳过
        if (mode.value === 'edit' && form.parentId !== null) {
          callback()
          return
        }
        // 新增一级客户 / 编辑一级客户：必填 A-Z
        if (!value) {
          callback(new Error('一级客户必须指定序列号前缀 A-Z'))
          return
        }
        if (
          typeof value !== 'string' ||
          value.length !== 1 ||
          value < 'A' ||
          value > 'Z'
        ) {
          callback(new Error('序列号前缀必须是 A-Z 单字符'))
          return
        }
        callback()
      },
      trigger: 'change',
    },
  ],
}

function onNewRoot(): void {
  mode.value = 'newRoot'
  editing.value = null
  form.name = ''
  form.parentId = null
  form.serialPrefix = null
  lockedParentName.value = ''
  dialogVisible.value = true
}

function onAddChild(parent: TreeNode): void {
  if (parent.parent_id !== null) return  // 仅一级节点可加子
  mode.value = 'newChild'
  editing.value = null
  form.name = ''
  form.parentId = parent.id
  form.serialPrefix = null
  lockedParentName.value = parent.name
  dialogVisible.value = true
}

function onEdit(node: TreeNode): void {
  const cust = customers.value.find((c) => c.id === node.id)
  if (!cust) return
  mode.value = 'edit'
  editing.value = cust
  form.name = cust.name
  form.parentId = cust.parent_id
  // 一级客户：带出原 prefix；叶子客户：永远 null
  form.serialPrefix = cust.parent_id === null ? cust.serial_prefix : null
  lockedParentName.value = ''
  dialogVisible.value = true
}

// 切换父客户时（仅 edit 模式可达）：清空 prefix，避免残值。
function onParentChange(_value: string | null): void {
  form.serialPrefix = null
  formRef.value?.clearValidate('serialPrefix')
}

async function onSave(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      parent_id: form.parentId || null,
      // 一级客户：传当前 prefix（可能 null = 让后端报必填校验）；
      // 叶子客户：service 层会忽略，payload 仍带 null 保持类型一致。
      serial_prefix: form.serialPrefix || null,
    }
    if (editing.value) {
      // update 时未传 prefix 即视为不改；只有显式非空才放进 payload
      const updatePayload: Parameters<typeof updateCustomer>[1] = {
        name: payload.name,
        parent_id: payload.parent_id,
      }
      if (payload.serial_prefix) {
        updatePayload.serial_prefix = payload.serial_prefix
      }
      await updateCustomer(editing.value.id, updatePayload)
      ElMessage.success('已保存')
    } else {
      await createCustomer(payload)
      ElMessage.success('已新增')
    }
    dialogVisible.value = false
    await fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '保存失败')
  } finally {
    saving.value = false
  }
}

function onDialogClosed(): void {
  editing.value = null
  mode.value = 'newRoot'
  form.name = ''
  form.parentId = null
  form.serialPrefix = null
  lockedParentName.value = ''
}

async function onDelete(node: TreeNode): Promise<void> {
  const isRoot = node.parent_id === null
  const childCount = isRoot ? node.children.length : 0
  const warn =
    childCount > 0
      ? `（一级客户「${node.name}」仍有 ${childCount} 个二级子节点，删除将一起拒绝）`
      : ''
  try {
    await ElMessageBox.confirm(
      `确认软删除客户「${node.name}」？${warn}`,
      '删除客户',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await softDeleteCustomer(node.id)
    ElMessage.success('已删除')
    await fetchList()
  } catch (e) {
    ElMessage.error((e as Error).message ?? '删除失败')
  }
}

onMounted(() => {
  // 先尝试恢复 localStorage 中的搜索条件
  const persisted = restoreCustomerFilter()
  if (persisted) {
    Object.assign(search, persisted.search)
  }
  void fetchList()
})
</script>

<style lang="scss" scoped>
.customer-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.filter-card :deep(.el-card__body) {
  padding: 16px 20px;
}
.form-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}
:deep(.tree-row) {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 2px 4px;
  gap: 8px;
}
:deep(.tree-row__prefix) {
  flex-shrink: 0;
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
:deep(.tree-row__name) {
  flex: 1;
}
:deep(.tree-row__actions) {
  opacity: 0;
  transition: opacity 0.15s ease-in-out;
}
:deep(.el-tree-node__content:hover .tree-row__actions) {
  opacity: 1;
}

/* 手机/平板无 hover：常显行尾操作按钮，方便触屏点击 */
@include until(md) {
  :deep(.tree-row__actions) {
    opacity: 1 !important;
  }
}

/* 筛选表单在窄屏宽度自适应：form-item 内按钮过多时可换行 */
.customer-filter-form :deep(.el-form-item__content) {
  flex-wrap: wrap;
  gap: 6px;
}
</style>