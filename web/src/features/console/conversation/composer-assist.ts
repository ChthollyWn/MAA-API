import type { MentionDraft } from './types'

export type { MentionDraft, MentionType } from './types'

/**
 * 输入栏里的辅助触发片段（M72 §4.5.6）。
 *
 * - 命令：`/` 在草稿开头，`start` 固定 0；
 * - 提及：`@` 在词首，`start` 是 `@` 的位置，`end` 是光标。
 */
export interface AssistToken {
  trigger: '@' | '/'
  query: string
  start: number
  end: number
}

/** 关卡代号的本地形状：字母/数字开头，最多 16 位，允许中间有 `-`。 */
export const STAGE_QUERY = /^[A-Za-z0-9][A-Za-z0-9-]{0,15}$/

/** 一条消息最多带多少个提及（与服务端 `MAX_MENTIONS` 一致）。 */
export const MAX_MENTIONS = 8

/**
 * 找光标前的辅助片段。
 *
 * `/` 只认草稿开头（`value.slice(0, caret)` 整段必须是斜杠加小写字母）；
 * `@` 只认词首（行首或空白之后），查询不超过 24 个字符且不能含空白或 `@`。
 */
export function findAssistToken(value: string, caret: number): AssistToken | null {
  const end = Math.max(0, Math.min(caret, value.length))
  const beforeCaret = value.slice(0, end)

  if (/^\/[a-z]*$/.test(beforeCaret)) {
    return { trigger: '/', query: beforeCaret.slice(1), start: 0, end }
  }

  const match = /(^|\s)@([^\s@]{0,24})$/.exec(beforeCaret)
  if (match) {
    const query = match[2] ?? ''
    return { trigger: '@', query, start: end - query.length - 1, end }
  }

  return null
}

/** 把触发片段替换成 `@标签 `，光标落在插入的空格之后。 */
export function insertMention(value: string, token: AssistToken, label: string): { value: string; caret: number } {
  const inserted = `@${label} `
  return {
    value: value.slice(0, token.start) + inserted + value.slice(token.end),
    caret: token.start + inserted.length,
  }
}

/**
 * 提交时还生效的提及：正文里还有 `@标签` 的才发；按 `type+id` 去重，最多 8 条。
 * 标签被删掉一部分时这条提及自动不带（服务端也就不会展开它）。
 */
export function activeMentions(value: string, mentions: readonly MentionDraft[]): MentionDraft[] {
  const seen = new Set<string>()
  const active: MentionDraft[] = []
  for (const mention of mentions) {
    if (!value.includes(`@${mention.label}`)) continue
    const key = `${mention.type}:${mention.id}`
    if (seen.has(key)) continue
    seen.add(key)
    active.push(mention)
    if (active.length >= MAX_MENTIONS) break
  }
  return active
}
