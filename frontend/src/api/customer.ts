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

export interface CustomerCreatePayload {
  name: string
  /** null = 一级客户；非空 = 挂在某个一级客户下的二级客户。 */
  parent_id: string | null
}

export interface CustomerUpdatePayload {
  name?: string
  parent_id?: string | null
}

export async function listCustomers(): Promise<Customer[]> {
  const resp = await api.get<Customer[]>('/customers')
  return resp.data
}

export async function getCustomer(id: string): Promise<Customer> {
  const resp = await api.get<Customer>(`/customers/${id}`)
  return resp.data
}

export async function createCustomer(
  payload: CustomerCreatePayload,
): Promise<Customer> {
  const resp = await api.post<Customer>('/customers', payload)
  return resp.data
}

export async function updateCustomer(
  id: string,
  payload: CustomerUpdatePayload,
): Promise<Customer> {
  const resp = await api.post<Customer>(`/customers/${id}/update`, payload)
  return resp.data
}

export async function softDeleteCustomer(id: string): Promise<void> {
  await api.post(`/customers/${id}/soft-delete`)
}