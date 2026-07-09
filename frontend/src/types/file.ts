// types/file.ts — 2026-07-10 起统一到 types/part_file.ts
export type { DrawingFileItem, PartFileItem } from './part_file'
import type { DrawingFileItem } from './part_file'
// 重导出仅为兼容旧 import：export { DrawingFileItem }