import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import { ArrowUp, CircleStop, LoaderCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { COARSE_POINTER_QUERY, isCoarsePointer, useMediaQuery } from '@/lib/media'

export interface ComposerProps {
  value: string
  onValueChange: (value: string) => void
  onSend: () => void
  /** 本轮还在进行（流式或正在提交确认）：发送按钮禁用，Enter 也不再发送。 */
  busy: boolean
  /**
   * 有可中断的一轮（本地正在流式，或后端 `turn_running` 说还在跑）时传入：busy 期间
   * 发送按钮**原地**换成「停止」，不再新增按钮位置。未传时 busy 的表现与从前一致
   * （禁用发送）。M22。
   */
  onStop?: () => void
  /** 停止请求已发出、正在等后端收尾：按钮显示 spinner 并禁用，防重复点击。 */
  stopping?: boolean
  /** 输入栏上方的一行状态，例如「Agent 正在处理…」。 */
  status?: ReactNode
  /** 手机：固定在 Tab 栏之上并随虚拟键盘上移；桌面：放在对话区底部。 */
  docked: boolean
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** 浮在输入栏上方的内容，例如「回到底部」。 */
  overlay?: ReactNode
}

/** 对话输入栏：随内容增高（最多约 6 行）；非触屏 Enter 发送、Shift+Enter 换行，触屏 Enter 只换行。 */
export function Composer({ value, onValueChange, onSend, busy, onStop, stopping = false, status, docked, textareaRef, overlay }: ComposerProps) {
  const inputId = useId()
  const formRef = useRef<HTMLFormElement | null>(null)
  const [dockHeight, setDockHeight] = useState(0)
  const coarse = useMediaQuery(COARSE_POINTER_QUERY)
  const canSend = !busy && value.trim().length > 0
  // 停止只在「本轮真的在跑」时才有意义；busy 仅来自提交确认时仍旧是禁用的发送按钮。
  const stoppable = busy && Boolean(onStop)

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    if (textarea.scrollHeight > 0) {
      textarea.style.height = `${textarea.scrollHeight + textarea.offsetHeight - textarea.clientHeight}px`
    }
  }, [textareaRef, value])

  useEffect(() => {
    const form = formRef.current
    if (!docked || !form || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => setDockHeight(form.offsetHeight))
    observer.observe(form)
    return () => observer.disconnect()
  }, [docked])

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法组字时的 Enter 用来上屏；Safari 在 compositionend 之后才派发它，只能靠 keyCode 229 认出。
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
    if (isCoarsePointer()) return
    event.preventDefault()
    if (canSend) onSend()
  }

  return (
    <>
      {docked ? <div aria-hidden="true" style={{ height: `calc(${dockHeight}px + var(--keyboard-inset))` }} /> : null}
      <form
        ref={formRef}
        onSubmit={(event) => {
          event.preventDefault()
          if (canSend) onSend()
        }}
        className={docked
          ? 'fixed inset-x-0 z-30 border-t bg-surface/90 px-4 pt-2.5 backdrop-blur-xl supports-[backdrop-filter]:bg-surface/75'
          : 'relative shrink-0 border-t bg-card px-4 pb-4 pt-3'}
        style={docked
          ? { bottom: 'calc(var(--nav-offset) + var(--keyboard-inset))', paddingBottom: 'calc(0.625rem + var(--nav-safe))' }
          : undefined}
      >
        {overlay ? <div className="pointer-events-none absolute inset-x-0 bottom-full flex justify-center pb-3">{overlay}</div> : null}
        <div className="mx-auto w-full max-w-3xl">
          <div role="status" aria-live="polite">
            {status ? <p className="mb-2 flex items-center gap-2 text-footnote text-muted-foreground">{status}</p> : null}
          </div>
          <div className="flex items-end gap-2">
            <label htmlFor={inputId} className="sr-only">发送给 Agent</label>
            <Textarea
              id={inputId}
              ref={textareaRef}
              rows={1}
              maxLength={32_000}
              value={value}
              placeholder="给 Agent 发消息"
              onChange={(event) => onValueChange(event.target.value)}
              onKeyDown={onKeyDown}
              className="max-h-[10.5rem] min-h-11 resize-none rounded-[1.375rem] bg-surface-2/60 px-4 py-2.5 leading-6"
            />
            {stoppable ? (
              <Button
                type="button"
                size="icon"
                variant="destructive-soft"
                aria-label="停止本轮"
                title={stopping ? '正在停止本轮…' : '停止本轮'}
                disabled={stopping}
                className="rounded-full"
                onClick={onStop}
              >
                {stopping
                  ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                  : <CircleStop aria-hidden="true" />}
              </Button>
            ) : (
              <Button type="submit" size="icon" aria-label="发送" disabled={!canSend} className="rounded-full">
                {busy ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <ArrowUp aria-hidden="true" />}
              </Button>
            )}
          </div>
          {coarse ? null : <p className="mt-1.5 hidden text-xs text-muted-foreground lg:block">Enter 发送，Shift+Enter 换行；资源消耗和破坏性操作会逐次请你确认</p>}
        </div>
      </form>
    </>
  )
}
