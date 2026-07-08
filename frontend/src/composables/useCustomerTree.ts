// useCustomerTree.ts
//
// 复用两个地方使用的「客户级联树」逻辑：列表页表头 el-cascader + 申请表。
//
// 注意：
// - 数据只一次性加载（onMounted），不变更；
// - 返回 cascader 选项数组，匹配 PartBatchNew / AssemblyCreate / AssemblyList
//   现有的 emitPath:false + checkStrictly:true 用法；
// - 错误用 Element Plus 全局 ElMessage 提示（沿用项目惯例）。

import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listCustomers, type Customer } from '@/api/customer'

export interface CustomerCascaderNode {
  id: string
  name: string
  children?: CustomerCascaderNode[]
  [key: string]: unknown
}

export function useCustomerTree() {
  const customers = ref<Customer[]>([])
  const loading = ref(false)

  const tree = computed<CustomerCascaderNode[]>(() => {
    const all = customers.value
    const roots = all.filter((c) => c.parent_id === null)
    return roots.map((r) => ({
      id: r.id,
      name: r.name,
      children: all
        .filter((c) => c.parent_id === r.id)
        .map((c) => ({ id: c.id, name: c.name })),
    }))
  })

  /** Resolve picked cascader node id back to its root customer id (L1 group)。 */
  function resolveRootCustomerId(pickedId: string | null): string | null {
    if (!pickedId) return null
    const found = customers.value.find((c) => c.id === pickedId)
    if (!found) return null
    return found.parent_id ?? found.id
  }

  async function load(): Promise<void> {
    loading.value = true
    try {
      customers.value = await listCustomers()
    } catch (e) {
      ElMessage.error((e as Error).message ?? '客户列表加载失败')
      customers.value = []
    } finally {
      loading.value = false
    }
  }

  onMounted(() => {
    void load()
  })

  return { customers, tree, loading, load, resolveRootCustomerId }
}
