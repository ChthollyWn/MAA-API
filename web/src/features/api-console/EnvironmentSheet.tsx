import { useId } from 'react'
import { ChevronRight, Server } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

export interface ConsoleEnvironment {
  baseUrl: string
  temporaryToken: string
  /** 一旦在临时 token 框里输入就改用它（留空即不带 token），直到「改用登录 token」。 */
  tokenOverride: boolean
  loginToken: string | null
}

export function environmentSummary({ baseUrl, temporaryToken, tokenOverride, loginToken }: ConsoleEnvironment) {
  const target = baseUrl.trim() || '当前服务'
  const credential = tokenOverride
    ? temporaryToken.trim() ? '临时 token' : '不带 token'
    : loginToken ? '登录 token' : '未设置 token'
  return { custom: Boolean(baseUrl.trim()) || tokenOverride, text: `${target} · ${credential}` }
}

/** 请求区顶部的环境入口：非默认值时以「自定义」提示，避免忘了还挂着测试用的地址或 token。 */
export function EnvironmentRow({ environment, onOpen, className }: { environment: ConsoleEnvironment; onOpen: () => void; className?: string }) {
  const descriptionId = useId()
  const summary = environmentSummary(environment)
  return (
    <button
      type="button"
      aria-label="请求环境"
      aria-describedby={descriptionId}
      aria-haspopup="dialog"
      onClick={onOpen}
      className={cn(
        'flex min-h-12 w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      <span className={cn('grid size-8 shrink-0 place-items-center rounded-lg', summary.custom ? 'bg-warning-soft text-warning' : 'bg-surface-2 text-muted-foreground')}>
        <Server aria-hidden="true" className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">请求环境</span>
        <span id={descriptionId} className="block truncate text-footnote text-muted-foreground">
          {summary.custom ? <span className="sr-only">自定义环境：</span> : null}
          {summary.text}
        </span>
      </span>
      {summary.custom ? <Badge variant="warning">自定义</Badge> : null}
      <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground/70" />
    </button>
  )
}

export interface EnvironmentSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  environment: ConsoleEnvironment
  onBaseUrlChange: (value: string) => void
  onTemporaryTokenChange: (value: string) => void
  onUseLoginToken: () => void
  onReset: () => void
}

/** Base URL 与临时 token：修改即时生效，只保存在当前页面的内存里。 */
export function EnvironmentSheet({ open, onOpenChange, environment, onBaseUrlChange, onTemporaryTokenChange, onUseLoginToken, onReset }: EnvironmentSheetProps) {
  const summary = environmentSummary(environment)
  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title="请求环境"
      description="只影响本页发出的调试请求，不会改变登录状态。"
      size="sm"
      footer={(
        <div className="flex gap-2">
          <Button variant="outline" disabled={!summary.custom} onClick={onReset}>恢复默认</Button>
          <Button className="flex-1" onClick={() => onOpenChange(false)}>完成</Button>
        </div>
      )}
    >
      <div className="space-y-5 pb-1 pt-2">
        <Field
          label="Base URL"
          htmlFor="console-base-url"
          description="留空时请求当前服务"
          hint="填写其他实例后，请求、收藏与专用 WebSocket 都会切到那台服务。"
        >
          <Input
            id="console-base-url"
            type="url"
            inputMode="url"
            autoCapitalize="off"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            placeholder="http://192.168.1.20:8002"
            value={environment.baseUrl}
            onChange={(event) => onBaseUrlChange(event.target.value)}
          />
        </Field>
        <Field
          label="临时 token（当前页）"
          htmlFor="console-temporary-token"
          labelAside={environment.tokenOverride ? <Button variant="link" size="sm" className="text-footnote" onClick={onUseLoginToken}>改用登录 token</Button> : null}
          description={environment.tokenOverride && !environment.temporaryToken.trim()
            ? '临时 token 为空：请求将不带 token 发送。'
            : '只保存在当前页面，刷新即失效。'}
        >
          <Input
            id="console-temporary-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder={environment.loginToken ? '默认使用登录 token' : '仅当前页有效'}
            value={environment.temporaryToken}
            onChange={(event) => onTemporaryTokenChange(event.target.value)}
          />
        </Field>
      </div>
    </ResponsiveSheet>
  )
}
