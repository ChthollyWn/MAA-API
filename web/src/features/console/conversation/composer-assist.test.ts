import { describe, expect, it } from 'vitest'
import {
  activeMentions,
  findAssistToken,
  insertMention,
  MAX_MENTIONS,
  STAGE_QUERY,
} from './composer-assist'
import { matchSlashCommands, parseSlashCommand } from './commands'
import type { MentionDraft } from './types'

describe('M72 composer assist', () => {
  it('detects a slash command only at the start of the draft', () => {
    expect(findAssistToken('/st', 3)).toEqual({ trigger: '/', query: 'st', start: 0, end: 3 })
    expect(findAssistToken('/', 1)).toEqual({ trigger: '/', query: '', start: 0, end: 1 })
    expect(findAssistToken('请 /stop', 7)).toBeNull()
    expect(findAssistToken('hello /stop', 11)).toBeNull()
    // 命令只认草稿开头的裸斜杠词，带空白就只是普通文本。
    expect(findAssistToken('/stop 一下', 10)).toBeNull()
    expect(findAssistToken('/STOP', 5)).toBeNull()

    expect(matchSlashCommands('/').map((command) => command.name)).toEqual(['stop', 'tickets', 'help'])
    expect(matchSlashCommands('/st').map((command) => command.name)).toEqual(['stop'])
    expect(matchSlashCommands('/stop 一下')).toEqual([])
    expect(matchSlashCommands('hello /')).toEqual([])
  })

  it('detects an @ token before the caret', () => {
    expect(findAssistToken('@', 1)).toEqual({ trigger: '@', query: '', start: 0, end: 1 })
    expect(findAssistToken('把 @1-7 做了', 6)).toEqual({ trigger: '@', query: '1-7', start: 2, end: 6 })
    expect(findAssistToken('line\n@abc', 9)).toEqual({ trigger: '@', query: 'abc', start: 5, end: 9 })
    // 词中、空查询后带空白、或 24 字以上都不算。
    expect(findAssistToken('abc@def', 7)).toBeNull()
    expect(findAssistToken('@ab cd', 6)).toBeNull()
    const query24 = 'a'.repeat(24)
    expect(findAssistToken(`@${query24}`, 25)).toEqual({ trigger: '@', query: query24, start: 0, end: 25 })
    expect(findAssistToken(`@${'a'.repeat(25)}`, 26)).toBeNull()
  })

  it('inserts a mention and keeps only mentions still in the text', () => {
    const value = '把 @日做了'
    const token = findAssistToken(value, 4)
    expect(token).not.toBeNull()
    expect(insertMention(value, token!, '日常')).toEqual({ value: '把 @日常 做了', caret: 6 })

    const mentions: MentionDraft[] = [
      { type: 'ticket', id: 't1', label: '日常' },
      { type: 'ticket', id: 't1', label: '日常' },
      { type: 'knowledge', id: 'k1', label: '剧情' },
      { type: 'operator', id: 'op1', label: '阿米娅' },
    ]
    expect(activeMentions('把 @日常 做了，@阿米娅', mentions)).toEqual([
      { type: 'ticket', id: 't1', label: '日常' },
      { type: 'operator', id: 'op1', label: '阿米娅' },
    ])
    // 标签被删掉一部分：这条提及不再随消息发出。
    expect(activeMentions('把 @日 做了', mentions)).toEqual([])

    const many: MentionDraft[] = Array.from({ length: 10 }, (_, index) => ({
      type: 'ticket',
      id: `t${index}`,
      label: `标签${index}`,
    }))
    const text = many.map((mention) => `@${mention.label}`).join(' ')
    expect(activeMentions(text, many)).toHaveLength(MAX_MENTIONS)
  })

  it('parses exact slash commands and leaves other slashes as text', () => {
    expect(parseSlashCommand('/stop')?.name).toBe('stop')
    expect(parseSlashCommand(' /tickets ')?.name).toBe('tickets')
    expect(parseSlashCommand('/help')?.name).toBe('help')
    expect(parseSlashCommand('/stop 一下')).toBeNull()
    expect(parseSlashCommand('/stop\n一下')).toBeNull()
    expect(parseSlashCommand('/')).toBeNull()
    expect(STAGE_QUERY.test('1-7')).toBe(true)
  })
})
