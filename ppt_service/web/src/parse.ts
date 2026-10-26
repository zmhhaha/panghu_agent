import type { Asset } from './domain';

export const pendingParseStatuses = new Set(['queued', 'parsing', 'stopping']);
const labels: Record<string, string> = {
  queued: '等待解析', parsing: '解析中', stopping: '正在停止解析',
  ready: '解析完成', failed: '解析失败', removed: '原资料已移除',
  // Compatibility with the previous UI contract; never infer success from content.
  parsed: '解析完成', pending: '等待解析',
};
export function parseStatusLabel(status: string) { return labels[status] ?? '解析状态待核对'; }
export function hasPendingParses(assets: Asset[]) { return assets.some(asset => pendingParseStatuses.has(asset.parse_status)); }
export function hasObservedParser(assets: Asset[]) { return assets.some(asset => ['parsing', 'stopping', 'ready', 'failed'].includes(asset.parse_status)); }
const reasons: Record<string, string> = {
  scanned_pdf_unsupported: '暂不支持扫描 PDF 的 OCR，请上传带文本层的 PDF 或文字文档。',
  pdf_page_limit: 'PDF 页数超过解析限制，请拆分文档后重新上传。',
  pdf_active_form: 'PDF 含不支持的交互表单，请导出为普通文本 PDF 后重新上传。',
  unsupported_pdf: 'PDF 格式不受支持或已加密，请重新导出带文本层、未加密的 PDF。',
  text_limit: '文本超过解析限制，请缩短或拆分资料后重新上传。',
  zip_expansion_limit: '文档解压后内容过大，请简化或拆分文档后重新上传。',
  zip_entry_limit: '文档包含过多内部文件，请简化或重新导出后上传。',
  duplicate_zip_part: '文档内部结构异常，请重新导出后上传。',
  active_office_content: '文档包含不支持的活动内容，请导出为不含宏或嵌入程序的普通 DOCX。',
  external_office_reference: '文档包含外部引用，请将所需内容嵌入普通 DOCX 后重新上传。',
  unsafe_xml: '文档内部结构不受支持，请重新导出普通 DOCX 后上传。',
  xml_limit: '文档结构超过解析限制，请简化或拆分后上传。',
  invalid_docx: 'DOCX 无效或已损坏，请重新导出后上传。',
  invalid_text: '文字文件无法读取，请保存为 UTF-8 文本后重新上传。',
  image_limit: '图片尺寸超过解析限制，请缩小图片后重新上传。',
  unsupported_media: '文件类型暂不支持，请改用 Markdown、文本 PDF、DOCX、PNG 或 JPEG。',
  source_hash_mismatch: '资料内容与上传记录不一致，请重新上传并刷新核对。',
  snapshot_limit: '资料快照超过解析限制，请精简资料后重新上传。',
  unsupported_media_type: '文件类型暂不支持，请改用 Markdown、文本 PDF、DOCX、PNG 或 JPEG。',
  unsupported_file_type: '文件类型暂不支持，请更换受支持的文件。',
  invalid_file: '文件内容无效或已损坏，请重新导出后上传。',
  invalid_document: '文档无效或已损坏，请重新导出后上传。',
  invalid_pdf: 'PDF 无法读取，请重新导出带文本层的 PDF。',
  encrypted_pdf: '暂不支持加密 PDF，请解除加密后重新上传。',
  pdf_encrypted: '暂不支持加密 PDF，请解除加密后重新上传。',
  no_text: '未提取到可用文本。扫描件暂不支持 OCR，请上传带文本层的文档。',
  no_extractable_text: '未提取到可用文本。扫描件暂不支持 OCR，请上传带文本层的文档。',
  empty_text: '未提取到可用文本，请检查资料内容或更换文件。',
  parse_timeout: '解析超过时限，请缩小文件或简化内容后重新上传。',
  parse_limit_exceeded: '文档超过解析限制，请拆分或缩小文件后重新上传。',
  document_limit_exceeded: '文档超过解析限制，请拆分或缩小文件后重新上传。',
  image_invalid: '图片无法读取，请重新导出为 PNG 或 JPEG。',
  parse_interrupted: '解析已中断，请刷新核对状态；若仍失败，请重新上传。',
  parse_failed: '资料解析失败，请检查文件内容或重新导出后上传。',
};
// Do not echo an unknown code, message, path, stack, token or lease from the service.
export function parseFailureMessage(code?: string | null) {
  return typeof code === 'string' ? reasons[code.toLowerCase()] ?? '资料解析未完成，请检查文件后重新上传；若持续失败，请联系管理员。' : '资料解析失败，请检查文件后重新上传。';
}
export function parseSummary(asset: Asset) {
  const value = 'text_summary' in asset ? asset.text_summary : asset.summary;
  return typeof value === 'string' && ['ready', 'parsed'].includes(asset.parse_status) ? value.slice(0, 2000) : null;
}
