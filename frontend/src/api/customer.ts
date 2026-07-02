// 客户 API（走 @/api/http 统一 axios 客户端）。
// Excel 批量导入零件时用 listCustomers() 拉全量客户，
// 客户端按 `parent_name / name` 唯一定位叶子客户的 id。

import { api } from '@/api/http'

export interface Customer {
  id: string
  name: string
  parent_id: string | null
  /** 仅叶子节点的二级客户有 parent_name；一级节点为 null。 */
  parent_name: string | null
}

export async function listCustomers(): Promise<Customer[]> {
  const resp = await api.get<Customer[]>('/customers')
  return resp.data
}