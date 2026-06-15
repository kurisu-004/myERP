export type PartCategory = '紧固件' | '轴承' | '传动件' | '电气件' | '油液' | '其他'
export type PartStatus = '启用' | '停用'

export interface PartItem {
  id: number
  partNo: string
  partName: string
  category: PartCategory
  spec: string
  unit: string
  stockQty: number
  safetyStock: number
  unitPrice: number
  supplier: string
  status: PartStatus
  updatedAt: string
}

export interface PartSearchForm {
  partNo: string
  partName: string
  category: PartCategory | ''
  spec: string
  supplier: string
  status: PartStatus | ''
}

export interface OptionItem<T = string> {
  value: T
  label: string
}
