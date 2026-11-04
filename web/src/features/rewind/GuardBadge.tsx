import { Badge } from '@/components/ui/badge'
import { InfoHint } from '@/components/ui/info-hint'
import { InlineAlert } from '@/components/ui/inline-alert'
import { isRecord } from '@/features/agent/format'

/**
 * 工具卡上的提示注入标记（M80 §4.6）。
 *
 * 历史消息从 tool 消息 JSON 的 `guard` 键取完整标记，直播从 SSE `tool_result` 的
 * `guard` 摘要取；两边的 `action` / `categories` / `excerpt` 同形。
 */
export interface ToolGuard {
  action: string
  categories: string[]
  excerpt: string | null
}

/** 从工具结果正文（`{result, error?, guard?}`）里读出 guard 摘要；没有返回 undefined。 */
export function parseToolGuard(body: unknown): ToolGuard | undefined {
  if (!isRecord(body)) return undefined
  const raw = body.guard
  if (!isRecord(raw)) return undefined
  const action = typeof raw.action === 'string' ? raw.action : ''
  if (!action) return undefined
  const categories = Array.isArray(raw.categories)
    ? raw.categories.flatMap((item) => (typeof item === 'string' ? [item] : []))
    : []
  return {
    action,
    categories,
    excerpt: typeof raw.excerpt === 'string' ? raw.excerpt : null,
  }
}

/**
 * 工具卡上的「疑似注入」/「已拦截」徽标与说明（M80 §4.6）。
 * `marked` 只是打过标，不打扰用户，什么都不渲染。
 */
export function GuardBadge({ guard }: { guard?: ToolGuard }) {
  if (guard?.action === 'flagged') {
    return (
      <span className="flex min-w-0 flex-wrap items-center gap-1.5">
        <Badge variant="warning">疑似注入</Badge>
        <InfoHint label="疑似注入说明">
          这段外部内容里有像指令的文字：「{guard.excerpt ?? ''}」。Agent 不会照做，本轮之后的写操作会逐次请你确认。
        </InfoHint>
      </span>
    )
  }
  if (guard?.action === 'blocked') {
    return (
      <span className="flex min-w-0 flex-wrap items-center gap-1.5">
        <Badge variant="danger">已拦截</Badge>
        <InfoHint label="已拦截说明">外部内容含可疑指令，没有交给 Agent。原文在审计记录里。</InfoHint>
      </span>
    )
  }
  return null
}

/**
 * 降权期间的强制确认卡顶部的来源提示（M80 §4.6）。
 * 全局确认面板与会话内确认卡共用；`payload.guard` 不存在时什么都不渲染。
 */
export function ConfirmationGuardAlert({ guard }: { guard?: unknown }) {
  if (!isRecord(guard)) return null
  const sourceTool = typeof guard.source_tool === 'string' ? guard.source_tool : ''
  const excerpt = typeof guard.excerpt === 'string' ? guard.excerpt : ''
  return (
    <InlineAlert tone="warning" title="读到可疑外部内容之后发起">
      来自 {sourceTool}：「{excerpt}」。确认前先看清楚这一步是不是你要的。
    </InlineAlert>
  )
}
