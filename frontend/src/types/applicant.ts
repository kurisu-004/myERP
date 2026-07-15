// 申请人 (Applicant) 类型定义。

export interface Applicant {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  name: string
  customer_id: string
  customer_name: string | null
  created_at: string
  updated_at: string
}

export interface ApplicantCreatePayload {
  name: string
  customer_id: string
}

export interface ApplicantUpdatePayload {
  name?: string
  customer_id?: string
}

export interface ApplicantSearchParams {
  customer_id: string
  name_prefix?: string
  limit?: number
}

export interface ApplicantListResult {
  items: Applicant[]
  total: number
  limit: number
  offset: number
}