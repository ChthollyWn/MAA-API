import { useState } from 'react'
import { Braces } from 'lucide-react'
import { Textarea } from '@/components/ui/textarea'
import { ariaProps, type ControlProps } from './control-props'

function stringify(value: unknown): string {
  if (value === undefined || value === null) return ''
  try {
    return JSON.stringify(value, null, 2) ?? ''
  } catch {
    return ''
  }
}

function sameJson(draft: string, value: unknown): boolean {
  if (value === undefined || value === null) return draft.trim() === ''
  try {
    return JSON.stringify(JSON.parse(draft)) === JSON.stringify(value)
  } catch {
    return false
  }
}

/** 复杂结构的回退：JSON 文本框，只有解析成合法 JSON 才写入草稿。 */
export function JsonControl({ id, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const serialized = stringify(value)
  const [draft, setDraft] = useState(serialized)
  const [synced, setSynced] = useState(serialized)
  const [parseError, setParseError] = useState<string | null>(null)
  if (serialized !== synced) {
    setSynced(serialized)
    if (!sameJson(draft, value)) {
      setDraft(serialized)
      setParseError(null)
    }
  }

  return (
    <div className="space-y-1.5">
      <p className="flex items-start gap-1.5 text-footnote text-muted-foreground">
        <Braces aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
        输入完整的 JSON 后自动应用
      </p>
      <Textarea
        id={id}
        className="min-h-28 font-mono"
        spellCheck={false}
        value={draft}
        placeholder={isNull ? '已设为空' : '未设置'}
        disabled={disabled}
        {...ariaProps({ invalid, describedBy })}
        onChange={(event) => {
          setDraft(event.target.value)
          try {
            const parsed: unknown = JSON.parse(event.target.value)
            onChange(parsed)
            setParseError(null)
          } catch {
            setParseError('JSON 格式尚未完整或无效。')
          }
        }}
      />
      {parseError ? <p role="status" className="text-footnote text-warning">{parseError}</p> : null}
    </div>
  )
}
