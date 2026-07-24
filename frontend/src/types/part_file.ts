// types/part_file.ts
//
// 2026-07-10 起统一的零件 / 装配体文件类型（与后端 schema/part_file.PartFileOut 对齐）。
// 取代旧的 DrawingFileItem / CncProgramItem。Assembly master / 零件图纸 / 3D 模型 /
// G 代码 / 设定单 / CAD 源文件 全部走这一个类型，用 `kind` 字段区分。
//
// 2026-07-14 扩展：DRAWING 同时接受 9 种图片格式（PNG/JPG/.../HEIC）；
// 新增 CAD_2D kind（DWG/DXF）；所有 row 带可选 `content_sha256` 用于去重提示。

/** 文件类型枚举（与后端 PartFileKind 的字符串值对齐） */
export type PartFileKind =
  | 'DRAWING'
  | '3D_MODEL'
  | 'G_CODE'
  | 'SETUP_SHEET'
  | 'ASSEMBLY_MASTER'
  | 'CAD_2D'

/** 统一文件项 */
export interface PartFileItem {
  id: string
  /** 乐观锁版本号；每次 UPDATE 自增 */
  version: number
  /** polymorphic owner_id：真实 t_part.id 或 t_assembly.id（kind=ASSEMBLY_MASTER） */
  owner_id: string
  kind: PartFileKind
  /** 扩展名大写：PDF / PNG / STEP / NC / DWG / DXF / ... */
  file_type: string
  original_filename: string
  file_size: number
  content_type: string
  upload_status: string
  /** SHA-256 hex（64 chars）；NULL = 历史记录未计算。用于判断去重命中。 */
  content_sha256: string | null
  created_at: string
  /** COS 临时签名 URL；每次请求即时签发 */
  download_url: string
  /** 关联的配对文件 ID（G_CODE <-> SETUP_SHEET 双向关联）；NULL=未配对 */
  paired_file_id: string | null
}

// ---------- 向后兼容 alias（旧 import 路径仍可用） ----------

/** @deprecated 用 PartFileItem 替代 */
export type DrawingFileItem = PartFileItem
/** @deprecated 用 PartFileItem 替代 */
export type CncProgramItem = PartFileItem