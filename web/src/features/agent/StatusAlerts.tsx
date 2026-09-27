import { Link } from 'react-router'
import { ShieldCheck, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { LLM_SETTINGS_PATH, needsLlmSettings } from './format'
import type { CapabilityResult, ShownError } from './types'

function DismissButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button type="button" variant="ghost" size="icon" aria-label={label} className="-my-2.5 -mr-2" onClick={onClick}>
      <X aria-hidden="true" />
    </Button>
  )
}

function LlmSettingsLink() {
  return (
    <Button asChild variant="link" size="sm" className="min-h-11">
      <Link to={LLM_SETTINGS_PATH}>前往设置检查 LLM 配置</Link>
    </Button>
  )
}

/** 会话授权条：「已授权屏幕操作 · 剩余 12 分钟」与「撤销」。 */
export function GrantBar({ remaining, revoking, onRevoke }: { remaining: string; revoking: boolean; onRevoke: () => void }) {
  return (
    <div className="flex min-h-11 items-center gap-2.5 rounded-xl bg-success-soft py-0.5 pl-3.5 pr-1 text-sm">
      <ShieldCheck aria-hidden="true" className="size-[1.125rem] shrink-0 text-success" />
      <p className="min-w-0 flex-1 truncate">
        <span className="font-medium">已授权屏幕操作</span>
        <span aria-hidden="true" className="px-1.5 text-muted-foreground">·</span>
        <span className="text-muted-foreground tabular-nums">{remaining}</span>
      </p>
      <Button type="button" variant="ghost" size="sm" aria-label="撤销屏幕操作授权" loading={revoking} onClick={onRevoke}>撤销</Button>
    </div>
  )
}

/** 就地错误；LLM 配置、密钥或模型能力相关时附上设置入口。 */
export function ErrorAlert({ error, onDismiss }: { error: ShownError; onDismiss?: () => void }) {
  const settings = needsLlmSettings(error)
  return (
    <InlineAlert
      tone="danger"
      action={settings || onDismiss ? (
        <>
          {settings ? <LlmSettingsLink /> : null}
          {onDismiss ? <DismissButton label="关闭错误提示" onClick={onDismiss} /> : null}
        </>
      ) : undefined}
    >
      {error.message}
    </InlineAlert>
  )
}

/** 模型能力自检结果；消息开始时带回的降级原因也显示在这里。 */
export function CapabilityAlert({ capability, onDismiss }: { capability: CapabilityResult; onDismiss: () => void }) {
  const dismiss = <DismissButton label="关闭模型能力提示" onClick={onDismiss} />
  const reason = capability.degraded_reason
  if (reason) {
    const title = capability.status === 'llm_not_configured'
      ? '尚未配置 LLM'
      : capability.tool_calling === false
        ? '工具调用不可用'
        : capability.vision === false
          ? '视觉能力不可用'
          : '模型能力受限'
    const settings = capability.tool_calling === false || needsLlmSettings({ message: reason })
    return (
      <InlineAlert tone="warning" role="status" title={title} action={<>{settings ? <LlmSettingsLink /> : null}{dismiss}</>}>
        {reason}
      </InlineAlert>
    )
  }
  if (!capability.ready) return null
  return (
    <InlineAlert tone="success" title="模型能力正常" action={dismiss}>
      {capability.model ? `${capability.model} 支持工具调用和视觉输入。` : '当前模型支持工具调用和视觉输入。'}
    </InlineAlert>
  )
}
