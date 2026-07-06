/** CNC 程序（G 代码）文件项，与后端 CncProgramOut 对齐 */
export interface CncProgramItem {
  id: string
  part_id: string
  file_type: string
  original_filename: string
  file_size: number
  content_type: string
  download_url: string
  upload_status: string
  created_at: string
}
