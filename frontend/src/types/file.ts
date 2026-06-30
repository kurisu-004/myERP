// types/file.ts
//
// 与后端 schema/drawing.py 对齐。

/** 图纸文件项（与后端 DrawingFileOut 对齐） */
export interface DrawingFileItem {
  id: number
  /** "assembly" | "part" */
  owner_type: string
  owner_id: number
  /** PDF / STEP / DWG / DXF */
  file_type: string
  original_filename: string
  file_size: number
  content_type: string
  /** 多页 PDF 时该行指向的页码；非 PDF 场景为 null */
  page_index: number | null
  /** COS 临时签名 URL；每次请求即时签发 */
  download_url: string
  upload_status: string
  created_at: string
}
