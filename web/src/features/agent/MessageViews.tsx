import { Markdown } from './markdown.tsx'
import { ToolCallBar } from './ToolCallBar'
import type { TranscriptEntry } from './transcript'

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
      {/* Agent 回复用 Markdown 渲染（列表、加粗、代码块、链接）。流式中间态由
          Markdown 内部按「不丢内容」处理，见 markdown.ts 与 markdown.robustness.test.ts。 */}
      <Markdown text={text} />
    </div>
  )
}

export function TranscriptEntryView({ entry }: { entry: TranscriptEntry }) {
  if (entry.kind === 'user') return <UserBubble text={entry.message.content ?? ''} messageId={entry.message.id} />
  if (entry.kind === 'assistant') return <AssistantText text={entry.message.content ?? ''} messageId={entry.message.id} />
  return <ToolCallBar tool={entry.tool} />
}

export function TypingIndicator() {
  return (
    <div aria-hidden="true" className="flex h-6 items-center gap-1 pl-0.5">
      {[0, 150, 300].map((delay) => (
        <span key={delay} className="size-1.5 animate-pulse rounded-full bg-muted-foreground/60 motion-reduce:animate-none" style={{ animationDelay: `${delay}ms` }} />
      ))}
    </div>
  )
}
