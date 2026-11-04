import { Link } from 'react-router'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { LLM_SETTINGS_PATH, needsLlmSettings } from './format'
import type { ShownError } from './types'

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
