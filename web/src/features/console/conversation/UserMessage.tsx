import { Badge } from '@/components/ui/badge'

export interface UserMessageProps {
  text: string
  references: readonly string[]
  messageId?: number
  /** 排队中的消息：还没被服务端受理，徽章提示用户它不会被当成已发送。 */
  queued?: boolean
}

/** 用户气泡：正文 + 末尾的引用段折叠成「引用 N 项」（M72 §4.6.3、11 §3.3）。 */
export function UserMessage({ text, references, messageId, queued }: UserMessageProps) {
  return (
    <div className="flex justify-end pl-8 sm:pl-16" data-message-id={messageId} data-queued={queued ? '' : undefined}>
      <div className="min-w-0 max-w-[42rem] space-y-2 rounded-2xl rounded-br-md bg-primary-soft px-4 py-2.5">
        <span className="sr-only">你：</span>
        {queued ? (
          <div className="flex items-center justify-end gap-2">
            <Badge variant="warning">排队中</Badge>
          </div>
        ) : null}
        <p className="whitespace-pre-wrap break-words text-body text-foreground">{text}</p>
        {references.length > 0 ? (
          <details className="rounded-lg bg-surface/60 px-2.5 py-1.5">
            <summary className="cursor-pointer list-none text-footnote font-medium text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
              引用 {references.length} 项
            </summary>
            <ul className="mt-1.5 space-y-1 border-t border-border/60 pt-1.5 text-footnote text-muted-foreground">
              {references.map((reference, index) => (
                <li key={`${index}-${reference}`} className="break-words">{reference}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </div>
  )
}
