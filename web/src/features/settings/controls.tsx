import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Minus, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { RemovableChip } from '@/components/ui/chip'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { rangeLabel } from './schema'

/** 行内文字按钮；伪元素把触控区域扩到约 44px 高而不放大字号。 */
const INLINE_ACTION_CLASS =
  "relative shrink-0 rounded-sm text-footnote font-medium text-primary-text before:absolute before:-inset-x-2 before:-inset-y-3 before:content-[''] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"

const NOTE_FALLBACK_LENGTH = 26

/** 说明默认一行截断，放不下时显示「展开说明」。 */
export function ExpandableNote({ id, text }: { id: string; text: string }) {
  const ref = useRef<HTMLParagraphElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(() => Array.from(text).length > NOTE_FALLBACK_LENGTH)

  useLayoutEffect(() => {
    const node = ref.current
    if (!node || expanded) return
    const measure = () => {
      if (node.clientWidth > 0) setOverflowing(node.scrollHeight > node.clientHeight + 1)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [text, expanded])

  return (
    <div className="flex items-start gap-2">
      <p id={id} ref={ref} className={cn('min-w-0 flex-1 text-footnote text-muted-foreground', !expanded && 'line-clamp-1')}>{text}</p>
      {overflowing || expanded ? (
        <button type="button" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded((current) => !current)} className={INLINE_ACTION_CLASS}>
          {expanded ? '收起' : '展开说明'}
        </button>
      ) : null}
    </div>
  )
}

export type ChipParseResult = { value: string | number; error?: undefined } | { value?: undefined; error: string }

export interface ChipListInputProps {
  id: string
  label: string
  values: readonly (string | number)[]
  /** 校验并转换一项输入。 */
  parse: (text: string) => ChipParseResult
  onChange: (values: (string | number)[]) => void
  placeholder?: string
  inputMode?: 'text' | 'numeric' | 'email'
  disabled?: boolean
  invalid?: boolean
  describedBy?: string
}

/** 列表值：已有项显示为可移除的芯片，输入框回车或点「添加」追加一项。 */
export function ChipListInput({ id, label, values, parse, onChange, placeholder = '输入后按回车添加', inputMode = 'text', disabled, invalid, describedBy }: ChipListInputProps) {
  const [draft, setDraft] = useState('')
  const [entryError, setEntryError] = useState<string | null>(null)
  const entryErrorId = `${id}-entry-error`

  const commit = () => {
    const text = draft.trim()
    if (!text) return
    const result = parse(text)
    if (result.error !== undefined) {
      setEntryError(result.error)
      return
    }
    if (values.some((item) => item === result.value)) {
      setEntryError('请不要重复添加这个值。')
      return
    }
    onChange([...values, result.value])
    setDraft('')
    setEntryError(null)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    // 输入法组字时的回车只用于上屏。
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
    event.preventDefault()
    commit()
  }

  return (
    <div className="space-y-2">
      {values.length ? (
        <ul className="flex flex-wrap gap-2" aria-label={`${label}：已添加 ${values.length} 项`}>
          {values.map((item, index) => (
            <li key={`${String(item)}-${index}`} className="max-w-full">
              {disabled ? (
                <span className="inline-flex h-8 max-w-full items-center truncate rounded-full bg-surface-2 px-3 text-[0.8125rem] font-medium">{String(item)}</span>
              ) : (
                <RemovableChip removeLabel={`移除 ${String(item)}`} onRemove={() => onChange(values.filter((_, position) => position !== index))}>
                  {String(item)}
                </RemovableChip>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-footnote text-muted-foreground">还没有添加任何值</p>
      )}
      <div className="flex gap-2">
        <Input
          id={id}
          value={draft}
          placeholder={placeholder}
          inputMode={inputMode}
          enterKeyHint="done"
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          disabled={disabled}
          aria-invalid={invalid || entryError ? true : undefined}
          aria-describedby={[entryError ? entryErrorId : '', describedBy ?? ''].filter(Boolean).join(' ') || undefined}
          onChange={(event) => {
            setDraft(event.target.value)
            setEntryError(null)
          }}
          onKeyDown={onKeyDown}
          onBlur={commit}
        />
        <Button type="button" variant="outline" aria-label={`添加${label}`} disabled={disabled || !draft.trim()} onClick={commit}>
          <Plus aria-hidden="true" />
          添加
        </Button>
      </div>
      {entryError ? <p id={entryErrorId} className="text-footnote font-medium text-danger">{entryError}</p> : null}
    </div>
  )
}

export interface NumberInputProps {
  id: string
  label: string
  value: string
  integer: boolean
  minimum?: number
  maximum?: number
  disabled?: boolean
  invalid?: boolean
  describedBy?: string
  onChange: (text: string) => void
}

/** 数字输入：−/+ 步进并夹在允许范围内，旁边显示范围；输入框保留原文，校验在保存时进行。 */
export function NumberInput({ id, label, value, integer, minimum, maximum, disabled, invalid, describedBy, onChange }: NumberInputProps) {
  const step = integer ? 1 : 0.1
  const current = value.trim() === '' ? Number.NaN : Number(value)
  const valid = Number.isFinite(current)
  const stepBy = (direction: 1 | -1) => {
    const base = valid ? current : minimum ?? 0
    const next = integer ? Math.round(base + direction * step) : Math.round((base + direction * step) * 100) / 100
    onChange(String(Math.min(maximum ?? Number.POSITIVE_INFINITY, Math.max(minimum ?? Number.NEGATIVE_INFINITY, next))))
  }
  const range = rangeLabel(minimum, maximum)
  const nonNegative = minimum !== undefined && minimum >= 0
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <div className="flex items-center gap-1.5">
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label={`减少${label}`}
          disabled={disabled || (valid && minimum !== undefined && current <= minimum)}
          onClick={() => stepBy(-1)}
        >
          <Minus aria-hidden="true" />
        </Button>
        <Input
          id={id}
          type="text"
          inputMode={nonNegative ? (integer ? 'numeric' : 'decimal') : 'text'}
          autoComplete="off"
          className="w-28 text-center tabular-nums"
          value={value}
          disabled={disabled}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy || undefined}
          onChange={(event) => onChange(event.target.value)}
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label={`增加${label}`}
          disabled={disabled || (valid && maximum !== undefined && current >= maximum)}
          onClick={() => stepBy(1)}
        >
          <Plus aria-hidden="true" />
        </Button>
      </div>
      {range ? <span className="text-footnote tabular-nums text-muted-foreground">{range}</span> : null}
    </div>
  )
}
