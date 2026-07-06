<!--
  CustomerList.vue

  /customers — 客户管理 → 客户一览。

  展示形式：el-tree 一级/二级树形结构，每个节点 hover 出现「+/编辑/删除」按钮：
    - 一级节点：可「+」加二级子节点，可编辑/删除（无子节点且未被引用时）
    - 二级节点：可编辑/删除（未被零件或装配体引用时）

  数据：调 `listCustomers()` 拉全量平铺数据，前端拼成 2 级树。
-->
<template>
  <div class="customer-list">
    <el-card shadow="never" class="filter-card">
      <el-form inline>
        <el-form-item label="客户名">
          <el-input
            v-model="search.keyword"
            placeholder="按客户名过滤"
            clearable
            style="width: 220px"
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
      :title="editing ? '编辑客户' : '新增客户'"
      width="480px"
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
        <el-form-item label="父客户" prop="parentId">
          <el-select
            v-model="form.parentId"
            placeholder="不选 = 一级客户；选了一个一级客户 = 二级"
            clearable
            filterable
            style="width: 100%"
          >
            <el-option
              v-for="r in rootOptions"
              :key="r.id"
              :label="r.name"
              :value="r.id"
            />
          </el-select>
          <p class="form-hint">
            二级客户必须挂在一级客户下；编辑根时清空此项。
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
  children: TreeNode[]
}

const loading = ref(false)
const saving = ref(false)
const customers = ref<Customer[]>([])
const search = reactive({ keyword: '' })

// ===== 树形组装（沿用 AssemblyList / PartBatchNew 的模式） =====
const tree = computed<TreeNode[]>(() => {
  const all = customers.value
  const roots = all.filter((c) => c.parent_id === null)
  return roots.map((r) => ({
    id: r.id,
    name: r.name,
    parent_id: r.parent_id,
    parent_name: r.parent_name,
    children: all
      .filter((c) => c.parent_id === r.id)
      .map((c) => ({
        id: c.id,
        name: c.name,
        parent_id: c.parent_id,
        parent_name: r.name,
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
interface FormState {
  name: string
  parentId: string | null
}
const formRef = ref<FormInstance>()
const dialogVisible = ref(false)
const editing = ref<Customer | null>(null)
const form = reactive<FormState>({ name: '', parentId: null })

const rules: FormRules = {
  name: [{ required: true, message: '请输入客户名', trigger: 'blur' }],
}

function onNewRoot(): void {
  editing.value = null
  form.name = ''
  form.parentId = null
  dialogVisible.value = true
}

function onAddChild(parent: TreeNode): void {
  if (parent.parent_id !== null) return  // 仅一级节点可加子
  editing.value = null
  form.name = ''
  form.parentId = parent.id
  dialogVisible.value = true
}

function onEdit(node: TreeNode): void {
  const cust = customers.value.find((c) => c.id === node.id)
  if (!cust) return
  editing.value = cust
  form.name = cust.name
  form.parentId = cust.parent_id
  dialogVisible.value = true
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
    }
    if (editing.value) {
      await updateCustomer(editing.value.id, payload)
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
  form.name = ''
  form.parentId = null
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

// ===== el-tree 自定义节点渲染（hover 显示按钮） =====
// 已在模板中用 <template #default> 内联实现，避免与 el-tree 内部类型不兼容。

onMounted(fetchList)
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
</style>