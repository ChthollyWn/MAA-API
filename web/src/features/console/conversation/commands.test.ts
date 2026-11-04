import { describe, expect, it } from 'vitest'
import { matchSlashCommands, parseSlashCommand, SLASH_COMMANDS } from './commands'

describe('M72 slash commands', () => {
  it('lists the three commands in display order', () => {
    expect(SLASH_COMMANDS.map((command) => command.label)).toEqual(['/stop', '/tickets', '/help'])
    expect(SLASH_COMMANDS.map((command) => command.description)).toEqual([
      '停止本轮',
      '打开工单列表',
      '查看命令与引用',
    ])
  })

  it('matches commands by prefix only while the draft is a bare slash word', () => {
    expect(matchSlashCommands('/')).toHaveLength(3)
    expect(matchSlashCommands('/st').map((command) => command.name)).toEqual(['stop'])
    expect(matchSlashCommands('/t').map((command) => command.name)).toEqual(['tickets'])
    expect(matchSlashCommands('/help').map((command) => command.name)).toEqual(['help'])
    expect(matchSlashCommands('/zzz')).toEqual([])
    expect(matchSlashCommands('hello /stop')).toEqual([])
    expect(matchSlashCommands('/stop 一下')).toEqual([])
  })

  it('parses only the exact command text', () => {
    expect(parseSlashCommand('/stop')?.name).toBe('stop')
    expect(parseSlashCommand('/tickets')?.name).toBe('tickets')
    expect(parseSlashCommand('/help')?.name).toBe('help')
    expect(parseSlashCommand('  /stop  ')?.name).toBe('stop')
    expect(parseSlashCommand('/stop 一下')).toBeNull()
    expect(parseSlashCommand('/tickets/1')).toBeNull()
    expect(parseSlashCommand('/helpme')).toBeNull()
    expect(parseSlashCommand('/')).toBeNull()
    expect(parseSlashCommand('你好')).toBeNull()
  })
})
