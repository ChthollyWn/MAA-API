import { describe, expect, it } from 'vitest'
import { ARTIFACT_KIND_LABELS, downloadName, formatArtifactSize } from './format'

describe('M90 产物格式', () => {
  it('maps artifact kinds to labels', () => {
    expect(ARTIFACT_KIND_LABELS).toEqual({
      note: '笔记',
      spill: '外置输出',
      report: '报告',
      chart: '图表',
      table: '表格',
      file: '文件',
      form: '表单',
      dashboard: '看板',
    })
  })

  it('formats sizes', () => {
    expect(formatArtifactSize(812)).toBe('812 B')
    expect(formatArtifactSize(3.4 * 1024)).toBe('3.4 KB')
    expect(formatArtifactSize(1.2 * 1024 * 1024)).toBe('1.2 MB')
    expect(formatArtifactSize(0)).toBe('0 B')
  })

  it('builds download names', () => {
    expect(downloadName('作战报告', 'text/markdown')).toBe('作战报告.md')
    expect(downloadName('本周表单', 'application/vnd.maa.view+json')).toBe('本周表单.json')
    expect(downloadName('数据', 'application/json')).toBe('数据.json')
    expect(downloadName('表格', 'text/csv; charset=utf-8')).toBe('表格.csv')
    expect(downloadName('日志', 'text/plain')).toBe('日志.txt')
    expect(downloadName('压缩包', 'application/zip')).toBe('压缩包.bin')

    expect(downloadName(' a/b\\c:d*e?f"g<h>i|j k ', 'text/plain')).toBe('abcdefghijk.txt')
    expect(downloadName('   ', 'text/plain')).toBe('artifact.txt')
    expect(downloadName('', 'application/pdf')).toBe('artifact.bin')
  })
})
