import type { ReactNode } from 'react'
import { Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ToolCallBar } from './ToolCallBar'
import { liveToolView, type TranscriptEntry } from './transcript'
import type { StreamingTurn } from './types'

export const QUICK_PROMPTS = ['查看设备状态和队列', '分析最近一次失败的流水线', '帮我保存一个 Copilot 作业'] as const

export function UserBubble({ text, messageId }: { text: string; messageId?: number }) {
  return (
    <div className="flex justify-end pl-8 sm:pl-16" data-message-id={messageId}>
      <div className="min-w-0 rounded-2xl rounded-br-md bg-primary-soft px-4 py-2.5">
        <span className="sr-only">你：</span>
        <p className="whitespace-pre-wrap break-words text-body text-foreground">{text}</p>
      </div>
    </div>
  )
}

export function AssistantText({ text, messageId }: { text: string; messageId?: number }) {
  return (
    <div className="pr-4 sm:pr-10" data-message-id={messageId}>
      <span className="sr-only">Agent：</span>
      <p className="whitespace-pre-wrap break-words text-body leading-relaxed">{text}</p>
    </div>
  )
}

export function TranscriptEntryView({ entry }: { entry: TranscriptEntry }) {
  if (entry.kind === 'user') return <UserBubble text={entry.message.content ?? ''} messageId={entry.message.id} />
  if (entry.kind === 'assistant') return <AssistantText text={entry.message.content ?? ''} messageId={entry.message.id} />
  return <ToolCallBar tool={entry.tool} />
}

function TypingIndicator() {
  return (
    <div aria-hidden="true" className="flex h-6 items-center gap-1 pl-0.5">
      {[0, 150, 300].map((delay) => (
        <span key={delay} className="size-1.5 animate-pulse rounded-full bg-muted-foreground/60 motion-reduce:animate-none" style={{ animationDelay: `${delay}ms` }} />
      ))}
    </div>
  )
}

/** 本轮的实时副本：用户的话、按到达顺序的回复与工具调用、确认卡。流结束并刷新历史后由页面丢弃。 */
export function LiveTurn({ turn, confirmation }: { turn: StreamingTurn; confirmation?: ReactNode }) {
  const tools = new Map(turn.tools.map((tool) => [tool.toolCallId, tool]))
  return (
    <section aria-label="本轮对话" aria-busy={turn.running} className="space-y-3">
      <UserBubble text={turn.userText} />
      {turn.segments.map((segment, index) => {
        if (segment.kind === 'text') return <AssistantText key={`text-${index}`} text={segment.text} />
        const tool = tools.get(segment.toolCallId)
        return tool ? <ToolCallBar key={`tool-${segment.toolCallId}`} tool={liveToolView(tool, turn.running)} /> : null
      })}
      {confirmation}
      {turn.running && !turn.pendingConfirmation ? <TypingIndicator /> : null}
    </section>
  )
}

export function QuickPrompts({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="mx-auto max-w-lg space-y-4 py-8 text-center">
      <span className="mx-auto grid size-12 place-items-center rounded-full bg-primary-soft text-primary-text">
        <Sparkles aria-hidden="true" className="size-6" />
      </span>
      <div className="space-y-1">
        <h2 className="text-headline font-semibold">你可以这样开始</h2>
        <p className="text-footnote text-muted-foreground">点一条填入输入框，改好再发送。</p>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-center">
        {QUICK_PROMPTS.map((prompt) => (
          <Button key={prompt} type="button" variant="outline" className="h-auto whitespace-normal rounded-full py-2" onClick={() => onPick(prompt)}>
            {prompt}
          </Button>
        ))}
      </div>
      <p className="text-footnote text-muted-foreground">需要执行的操作会先经过策略检查；资源消耗和破坏性操作逐次等待确认。</p>
    </div>
  )
}
