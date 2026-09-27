import { useId, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { ResponsiveSheet } from '@/components/ui/sheet'
import type { AuditFilters } from '@/features/audit/filters'
import { CALLER_OPTIONS, RISK_OPTIONS, STATUS_OPTIONS } from '@/features/audit/types'

const ALL = 'all'
const callerOptions = [{ value: ALL, label: '全部' }, ...CALLER_OPTIONS] as const

const dateInputClassName = 'text-left [&::-webkit-date-and-time-value]:text-left'

export interface AuditFilterSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 打开时的筛选；面板里的改动在「应用」后才写入地址。 */
  filters: AuditFilters
  onApply: (filters: AuditFilters) => void
  onClear: () => void
}

/** 审计筛选面板：调用方、工具名、状态、风险与开始日期（UTC）。 */
export function AuditFilterSheet({ open, onOpenChange, filters, onApply, onClear }: AuditFilterSheetProps) {
  const baseId = useId()
  const [draft, setDraft] = useState<AuditFilters>(filters)
  const set = <K extends keyof AuditFilters>(key: K, value: AuditFilters[K]) => setDraft((current) => ({ ...current, [key]: value }))

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title="筛选审计记录"
      description="筛选条件会写入页面地址，可以直接分享当前视图。"
      footer={(
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" onClick={onClear}>清除</Button>
          <Button onClick={() => onApply(draft)}>应用</Button>
        </div>
      )}
    >
      <div className="space-y-5 pt-1">
        <Field label="调用方">
          <SegmentedControl
            ariaLabel="调用方"
            fullWidth
            value={draft.caller || ALL}
            onValueChange={(value) => set('caller', value === ALL ? '' : value)}
            options={callerOptions}
          />
        </Field>
        <Field label="工具名" htmlFor={`${baseId}-tool`}>
          <Input
            id={`${baseId}-tool`}
            className="font-mono"
            placeholder="例如 submit_pipeline"
            autoCapitalize="none"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            enterKeyHint="search"
            value={draft.tool_name}
            onChange={(event) => set('tool_name', event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key !== 'Enter') return
              event.preventDefault()
              onApply(draft)
            }}
          />
        </Field>
        <div className="grid gap-5 sm:grid-cols-2 sm:gap-3">
          <Field label="状态" htmlFor={`${baseId}-status`}>
            <NativeSelect id={`${baseId}-status`} value={draft.status} onChange={(event) => set('status', event.currentTarget.value)}>
              <option value="">全部</option>
              {STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </NativeSelect>
          </Field>
          <Field label="风险" htmlFor={`${baseId}-risk`}>
            <NativeSelect id={`${baseId}-risk`} value={draft.risk_level} onChange={(event) => set('risk_level', event.currentTarget.value)}>
              <option value="">全部</option>
              {RISK_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </NativeSelect>
          </Field>
        </div>
        <Field label="开始日期" htmlFor={`${baseId}-since`} description="按 UTC 零点计算，只看这一天及之后的记录。">
          <Input
            id={`${baseId}-since`}
            type="date"
            className={dateInputClassName}
            value={draft.since}
            onChange={(event) => set('since', event.currentTarget.value)}
          />
        </Field>
      </div>
    </ResponsiveSheet>
  )
}
