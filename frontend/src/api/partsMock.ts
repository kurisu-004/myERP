import { ref } from 'vue'
import type { OptionItem, PartCategory, PartItem, PartStatus } from '@/types/parts'

const list = ref<PartItem[]>([
  {
    id: 1, partNo: 'P-0001', partName: '六角螺栓 M8x20', category: '紧固件',
    spec: 'M8x20', unit: '个', stockQty: 1250, safetyStock: 500,
    unitPrice: 0.85, supplier: '上海紧固件有限公司', status: '启用', updatedAt: '2026-06-10 14:23',
  },
  {
    id: 2, partNo: 'P-0002', partName: '深沟球轴承 6204', category: '轴承',
    spec: '6204-2RS', unit: '套', stockQty: 86, safetyStock: 50,
    unitPrice: 28.5, supplier: 'SKF 中国', status: '启用', updatedAt: '2026-06-09 10:11',
  },
  {
    id: 3, partNo: 'P-0003', partName: '齿轮 Z=24 m=2', category: '传动件',
    spec: 'Z24-m2', unit: '件', stockQty: 24, safetyStock: 30,
    unitPrice: 156.0, supplier: '杭州精工齿轮', status: '启用', updatedAt: '2026-06-08 16:45',
  },
  {
    id: 4, partNo: 'P-0004', partName: '电机 380V 1.5kW', category: '电气件',
    spec: 'Y2-80M2-4', unit: '台', stockQty: 5, safetyStock: 3,
    unitPrice: 1280.0, supplier: 'ABB 电气', status: '启用', updatedAt: '2026-06-07 09:30',
  },
  {
    id: 5, partNo: 'P-0005', partName: '液压油 ISO VG46', category: '油液',
    spec: 'VG46-200L', unit: '桶', stockQty: 12, safetyStock: 6,
    unitPrice: 980.0, supplier: '美孚工业', status: '启用', updatedAt: '2026-06-05 11:20',
  },
  {
    id: 6, partNo: 'P-0006', partName: '平键 8x7x30', category: '紧固件',
    spec: '8x7x30', unit: '个', stockQty: 320, safetyStock: 100,
    unitPrice: 3.2, supplier: '上海紧固件有限公司', status: '停用', updatedAt: '2026-05-28 13:55',
  },
  {
    id: 7, partNo: 'P-0007', partName: '链轮 08B-1 Z20', category: '传动件',
    spec: '08B-1-Z20', unit: '件', stockQty: 18, safetyStock: 10,
    unitPrice: 78.0, supplier: '杭州精工齿轮', status: '启用', updatedAt: '2026-06-01 08:00',
  },
  {
    id: 8, partNo: 'P-0008', partName: '传感器 LJ12A3-4-Z/BX', category: '电气件',
    spec: 'LJ12A3-4', unit: '个', stockQty: 65, safetyStock: 20,
    unitPrice: 45.0, supplier: '欧姆龙自动化', status: '启用', updatedAt: '2026-06-11 17:42',
  },
])

const categoryOptions: OptionItem<PartCategory>[] = [
  { value: '紧固件', label: '紧固件' },
  { value: '轴承', label: '轴承' },
  { value: '传动件', label: '传动件' },
  { value: '电气件', label: '电气件' },
  { value: '油液', label: '油液' },
  { value: '其他', label: '其他' },
]

const statusOptions: OptionItem<PartStatus>[] = [
  { value: '启用', label: '启用' },
  { value: '停用', label: '停用' },
]

export function usePartsMock() {
  return { list, categoryOptions, statusOptions }
}

export function getNextId(): number {
  return list.value.length === 0 ? 1 : Math.max(...list.value.map((i) => i.id)) + 1
}
