/** 产物种类的中文（M90 §4.6.5）；未知取值原样显示。 */
export const ARTIFACT_KIND_LABELS: Record<string, string> = {
  note: '笔记',
  spill: '外置输出',
  report: '报告',
  chart: '图表',
  table: '表格',
  file: '文件',
  form: '表单',
  dashboard: '看板',
}

/** 字节数 → 人读的大小；列表里只做粗略展示。 */
export function formatArtifactSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

/** 下载文件名：去掉不能出现在文件名里的字符，按 MIME 给扩展名。 */
export function downloadName(title: string, mime: string): string {
  const stem = title.replace(/[\\/:*?"<>|\s]+/g, '').slice(0, 64) || 'artifact'
  const ext = /markdown/.test(mime) ? 'md'
    : /json/.test(mime) ? 'json'
    : /^text\/csv/.test(mime) ? 'csv'
    : /^text\/plain/.test(mime) ? 'txt'
    : 'bin'
  return `${stem}.${ext}`
}
