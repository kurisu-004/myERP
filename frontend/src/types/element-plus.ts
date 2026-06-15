export interface TableSortCtx<T = Record<string, unknown>> {
  prop: keyof T | string | null
  order: 'ascending' | 'descending' | null
  column?: unknown
}
