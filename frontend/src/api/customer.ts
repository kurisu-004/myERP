// api/customer.ts
//
// 客户 API 封装。Excel 批量导入零件时用 listCustomers() 拉全量客户，
// 客户端按 `parent_name / name` 唯一定位叶子客户的 id。

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

async function unwrap<T>(resp: Response): Promise<T> {
  const json = (await resp.json()) as ApiEnvelope<T>
  if (json.code !== 0) {
    throw new Error(json.message || `API error code=${json.code}`)
  }
  return json.data
}

export interface Customer {
  id: string
  name: string
  parent_id: string | null
  /** 仅叶子节点的二级客户有 parent_name；一级节点为 null。 */
  parent_name: string | null
}

export async function listCustomers(): Promise<Customer[]> {
  const resp = await fetch('/api/v1/customers')
  return unwrap<Customer[]>(resp)
}