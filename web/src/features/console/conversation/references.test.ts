import { describe, expect, it } from 'vitest'
import { REFERENCE_HEADER, splitReferenceBlock } from './references'

describe('M72 引用段拆分', () => {
  it('returns the whole content when there is no reference block', () => {
    expect(splitReferenceBlock('把日常做了')).toEqual({ text: '把日常做了', references: [] })
    // 只有标题、没有前面的空行时不算分隔符（用户自己写的话）。
    expect(splitReferenceBlock('我说【引用】不是引用段')).toEqual({ text: '我说【引用】不是引用段', references: [] })
  })

  it('splits at the header and keeps only "- " lines', () => {
    expect(splitReferenceBlock(`帮我看下\n\n${REFERENCE_HEADER}\n- 知识「作战记录」\n说明文字\n- 关卡「1-7」\n`))
      .toEqual({ text: '帮我看下', references: ['知识「作战记录」', '关卡「1-7」'] })
  })

  it('splits at the last block when the marker appears twice', () => {
    const content = `第一段\n\n${REFERENCE_HEADER}\n- 旧条目\n\n${REFERENCE_HEADER}\n- 新条目`
    expect(splitReferenceBlock(content)).toEqual({
      text: `第一段\n\n${REFERENCE_HEADER}\n- 旧条目`,
      references: ['新条目'],
    })
  })

  it('handles an empty or header-only block', () => {
    expect(splitReferenceBlock(`正文\n\n${REFERENCE_HEADER}\n`)).toEqual({ text: '正文', references: [] })
    expect(splitReferenceBlock('')).toEqual({ text: '', references: [] })
  })
})
