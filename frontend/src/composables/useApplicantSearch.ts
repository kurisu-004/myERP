// 申请人搜索 composable。
//
// 用途：PartBatchNew / AssemblyCreate 对话框里「申请人」自动补全共用。
//
// 设计要点：
// - **只在客户切换时** 拉一次全集（limit=200）→ 缓存到 `applicants` ref。
// - 暴露 `querySearch(queryString, callback)` 作为 `el-autocomplete` 的
//   `:fetch-suggestions` 绑定：客户端同步子串过滤已缓存的 200 条，**不**触发
//   网络请求。`PartBatchNew.vue` / `AssemblyCreate.vue` 模板的 autocomplete 上
//   配 `:debounce="0"`，避免在纯内存过滤场景下叠加 Element Plus 默认的 300ms。
// - 没有 debounce、没有 :remote-method、没有 :remote-method 触发的 setTimeout。
//
// 使用：
// ```ts
// const {
//   applicants,
//   loading,
//   rootCustomerId,
//   loadForCustomer,
//   querySearch,   // 绑给 el-autocomplete 的 :fetch-suggestions
// } = useApplicantSearch({
//   resolveRootCustomerId: (pickedId) => { ... },
// })
// onCustomerChange(pickedId) { ... await loadForCustomer(pickedId) }
// ```
import { ref, type Ref } from 'vue'
import { searchApplicants } from '@/api/applicant'
import type { Applicant } from '@/types/applicant'

export interface UseApplicantSearchOptions {
  /**
   * 把 cascader 选中的客户 id 解析到所属一级客户 id（一级 → 自己；二级 → parent）。
   * 返回 null 表示无法解析（如未选 / 客户不存在）。
   */
  resolveRootCustomerId: (pickedId: string | null) => string | null
}

export function useApplicantSearch(opts: UseApplicantSearchOptions) {
  const applicants: Ref<Applicant[]> = ref<Applicant[]>([])
  const loading = ref(false)
  /** 当前缓存对应的一级客户 id；切客户时若相同则不重拉。 */
  const rootCustomerId: Ref<string | null> = ref<string | null>(null)

  /**
   * 拉取指定客户下的申请人全集。
   * - 切到空客户：清空缓存。
   * - 切到同一客户（id 未变）：不重拉。
   * - 切到新客户：拉一次全集。
   */
  async function loadForCustomer(pickedId: string | null): Promise<void> {
    if (pickedId === null || pickedId === undefined || pickedId === '') {
      rootCustomerId.value = null
      applicants.value = []
      return
    }
    const rootId = opts.resolveRootCustomerId(pickedId)
    if (rootId === null) {
      // 选中的客户解析不到一级（理论上不该发生；兜底）
      rootCustomerId.value = null
      applicants.value = []
      return
    }
    if (rootCustomerId.value === rootId && applicants.value.length > 0) {
      // 同一客户，已有缓存 → 不重拉
      return
    }
    rootCustomerId.value = rootId
    loading.value = true
    try {
      applicants.value = await searchApplicants({
        customer_id: rootId,
        limit: 200,   // 全集（典型一级客户下 < 100 个）
      })
    } catch {
      // 错误由 ElMessage 提示
      applicants.value = []
    } finally {
      loading.value = false
    }
  }

  /**
   * el-autocomplete 的 fetch-suggestions 回调。
   * 客户端同步过滤已缓存的 200 条申请人（loadForCustomer 在切客户时拉一次）。
   * 空 query 时返回全集，便于「聚焦即看全表」UX。
   * 子串匹配而非前缀匹配，是 autocomplete 的自然交互。
   */
  function querySearch(
    queryString: string,
    callback: (items: Applicant[]) => void,
  ): void {
    const q = queryString.trim().toLowerCase()
    const all = applicants.value
    if (!q) {
      callback(all)
      return
    }
    callback(all.filter((a) => a.name.toLowerCase().includes(q)))
  }

  return { applicants, loading, rootCustomerId, loadForCustomer, querySearch }
}