/** 输入栏的斜杠命令（M72 §4.5.6）。 */
export type SlashCommandName = 'stop' | 'tickets' | 'help'

export interface SlashCommand {
  name: SlashCommandName
  label: string
  description: string
}

export const SLASH_COMMANDS: readonly SlashCommand[] = [
  { name: 'stop', label: '/stop', description: '停止本轮' },
  { name: 'tickets', label: '/tickets', description: '打开工单列表' },
  { name: 'help', label: '/help', description: '查看命令与引用' },
]

/**
 * 命令面板的候选：草稿以 `/` 开头且没有空白时按前缀匹配，否则不出候选。
 * 命令只认草稿开头的 `/`，所以「/stop 一下」不是命令。
 */
export function matchSlashCommands(draft: string): SlashCommand[] {
  if (!draft.startsWith('/') || /\s/.test(draft)) return []
  return SLASH_COMMANDS.filter((command) => command.label.startsWith(draft))
}

/** 提交时才算命令：`draft.trim()` 恰好等于某个 label 才是命令，其余斜杠文本照常发送。 */
export function parseSlashCommand(draft: string): SlashCommand | null {
  const trimmed = draft.trim()
  return SLASH_COMMANDS.find((command) => command.label === trimmed) ?? null
}
