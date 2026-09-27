import { useRef, type ReactNode, type TouchEvent } from 'react'
import { ChevronDown, ChevronUp, Copy, Maximize2, Minimize2, Send, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { cn } from '@/lib/utils'
import type { ConsoleNotice, DrawerSize, DrawerView, ResponseView } from './console-model'
import { ResponseStatus } from './ResponseViews'

/** 发送栏：上下各 0.75rem 内边距 + 44px 按钮。 */
const SEND_BAR_HEIGHT = '4.25rem'
/** 抽屉收起时只剩抓手与状态行。 */
const DRAWER_PEEK_HEIGHT = '4rem'
const DRAWER_FULL_HEIGHT = `calc(100dvh - var(--nav-offset) - var(--keyboard-inset) - var(--nav-safe) - ${SEND_BAR_HEIGHT} - env(safe-area-inset-top, 0px) - 0.75rem)`
const drawerHeight: Record<DrawerSize, string> = {
  peek: DRAWER_PEEK_HEIGHT,
  half: `min(60dvh, ${DRAWER_FULL_HEIGHT})`,
  full: DRAWER_FULL_HEIGHT,
}
/** 竖向滑动超过这个距离才算手势，避免点按抓手时误触。 */
const SWIPE_THRESHOLD = 40

export function NoticeBanner({ notice, onDismiss, className }: { notice: ConsoleNotice; onDismiss: () => void; className?: string }) {
  return (
    <InlineAlert
      tone={notice.tone}
      className={cn('py-2.5', className)}
      action={(
        <Button variant="ghost" size="icon-sm" aria-label="关闭提示" className="-my-1.5 -mr-1.5 before:absolute before:-inset-1 before:content-['']" onClick={onDismiss}>
          <X aria-hidden="true" />
        </Button>
      )}
    >
      {notice.text}
    </InlineAlert>
  )
}

function SendControls({ sending, onSend, onCopyCurl }: { sending: boolean; onSend: () => void; onCopyCurl: () => void }) {
  return (
    <>
      <Button variant="outline" size="icon" aria-label="复制 cURL" onClick={onCopyCurl}>
        <Copy aria-hidden="true" />
      </Button>
      <Button className="px-5" loading={sending} onClick={onSend}>
        {sending ? null : <Send aria-hidden="true" />}发送请求
      </Button>
    </>
  )
}

export interface SendFooterProps {
  url: string
  requestId: string | null
  sending: boolean
  onSend: () => void
  onCopyCurl: () => void
  notice: ConsoleNotice | null
  onDismissNotice: () => void
}

/** 桌面三栏：请求栏底部的发送区。 */
export function SendFooter({ url, requestId, sending, onSend, onCopyCurl, notice, onDismissNotice }: SendFooterProps) {
  return (
    <div className="shrink-0 space-y-2 border-t px-4 py-3">
      {notice ? <NoticeBanner notice={notice} onDismiss={onDismissNotice} /> : null}
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <code className="block truncate font-mono text-footnote" title={url}>{url}</code>
          <span className="block truncate font-mono text-xs text-muted-foreground">X-Request-Id {requestId ?? '发送时生成'}</span>
        </div>
        <SendControls sending={sending} onSend={onSend} onCopyCurl={onCopyCurl} />
      </div>
    </div>
  )
}

export interface RequestDockProps extends Omit<SendFooterProps, 'requestId'> {
  response: ResponseView | null
  drawerSize: DrawerSize
  onDrawerSizeChange: (size: DrawerSize) => void
  drawerView: DrawerView
  onDrawerViewChange: (view: DrawerView) => void
  logCount: number
  responseContent: ReactNode
  logContent: ReactNode
}

/**
 * 手机分步工作流的底部：发送栏固定在 Tab 栏之上并随虚拟键盘上移，
 * 响应抽屉在它上方（收起 / 半屏 / 全屏，可上下滑动）。键盘弹出时抽屉让位，把空间留给编辑。
 */
export function RequestDock({
  url, sending, onSend, onCopyCurl, notice, onDismissNotice,
  response, drawerSize, onDrawerSizeChange, drawerView, onDrawerViewChange, logCount, responseContent, logContent,
}: RequestDockProps) {
  const touchStart = useRef<number | null>(null)
  const expanded = drawerSize !== 'peek'
  const onTouchEnd = (event: TouchEvent) => {
    const from = touchStart.current
    const to = event.changedTouches[0]?.clientY
    touchStart.current = null
    if (from === null || to === undefined || Math.abs(from - to) < SWIPE_THRESHOLD) return
    if (from > to) onDrawerSizeChange(drawerSize === 'peek' ? 'half' : 'full')
    else onDrawerSizeChange(drawerSize === 'full' ? 'half' : 'peek')
  }
  return (
    <>
      <div aria-hidden="true" style={{ height: `calc(${SEND_BAR_HEIGHT} + var(--nav-safe) + ${response ? DRAWER_PEEK_HEIGHT : '0px'} + 0.75rem)` }} />
      <div className="fixed inset-x-0 z-30 flex flex-col lg:left-60" style={{ bottom: 'calc(var(--nav-offset) + var(--keyboard-inset))' }}>
        {response ? (
          <section
            aria-label="响应抽屉"
            className="mx-auto flex w-full max-w-3xl flex-col overflow-hidden rounded-t-2xl border border-b-0 bg-surface-raised shadow-float transition-[height] duration-200 ease-out motion-reduce:transition-none [:root[data-tabbar=hidden]_&]:hidden"
            style={{ height: drawerHeight[drawerSize] }}
          >
            <div
              data-testid="response-drawer-handle"
              className="shrink-0 touch-none select-none px-4 pb-2 pt-2.5"
              onTouchStart={(event) => { touchStart.current = event.touches[0]?.clientY ?? null }}
              onTouchEnd={onTouchEnd}
            >
              <span aria-hidden="true" className="mx-auto mb-1.5 block h-1 w-9 rounded-full bg-border-strong" />
              <div className="flex min-h-9 items-center gap-1">
                <ResponseStatus response={response} className="flex-1" />
                {expanded ? (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={drawerSize === 'full' ? '退出全屏' : '全屏显示响应'}
                    onClick={() => onDrawerSizeChange(drawerSize === 'full' ? 'half' : 'full')}
                  >
                    {drawerSize === 'full' ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}
                  </Button>
                ) : null}
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={expanded ? '收起响应' : '展开响应'}
                  aria-expanded={expanded}
                  className="-mr-1.5"
                  onClick={() => onDrawerSizeChange(expanded ? 'peek' : 'half')}
                >
                  {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronUp aria-hidden="true" />}
                </Button>
              </div>
              {expanded ? (
                <SegmentedControl
                  ariaLabel="响应面板内容"
                  size="sm"
                  fullWidth
                  className="mt-2"
                  value={drawerView}
                  onValueChange={onDrawerViewChange}
                  options={[
                    { value: 'response', label: '响应' },
                    { value: 'logs', label: logCount ? `日志 ${logCount}` : '日志' },
                  ]}
                />
              ) : null}
            </div>
            {expanded ? (
              <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain border-t px-4 py-4">
                {drawerView === 'response' ? responseContent : logContent}
              </div>
            ) : null}
          </section>
        ) : null}
        <div
          className="border-t bg-surface/90 px-4 pt-3 backdrop-blur-xl supports-[backdrop-filter]:bg-surface/75"
          style={{ paddingBottom: 'calc(0.75rem + var(--nav-safe))' }}
        >
          <div className="mx-auto w-full max-w-3xl space-y-2">
            {notice && !(response && drawerSize === 'full') ? <NoticeBanner notice={notice} onDismiss={onDismissNotice} /> : null}
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate font-mono text-footnote text-muted-foreground">{url}</code>
              <SendControls sending={sending} onSend={onSend} onCopyCurl={onCopyCurl} />
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
