import { useState, type KeyboardEvent } from 'react'
import { ArrowDown, ArrowUp, Plus, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Chip, RemovableChip } from '@/components/ui/chip'
import { Input } from '@/components/ui/input'
import { UNSET, arrayItemOptions, getType, isObject, moveArrayItem, normalizeSchema, updateArrayItem, type JsonSchema } from '../form'
import { ariaProps, type ControlProps } from './control-props'

function arrayBounds(schema: JsonSchema) {
  return {
    minItems: typeof schema.minItems === 'number' ? schema.minItems : 0,
    maxItems: typeof schema.maxItems === 'number' ? schema.maxItems : Number.POSITIVE_INFINITY,
  }
}

function itemType(schema: JsonSchema, root: JsonSchema): string | undefined {
  return isObject(schema.items) ? getType(normalizeSchema(schema.items as JsonSchema, root).schema) : undefined
}

/** 列表为空时的提示：明确留空时显示「已设为空」，否则显示 `text`（没有就不显示）。 */
function EmptyHint({ isNull, text }: { isNull: boolean; text?: string }) {
  const content = isNull ? '已设为空' : text
  return content ? <p className="text-footnote text-muted-foreground">{content}</p> : null
}

/** 回车添加；输入法组字时的回车只用于上屏。 */
function useDraftEntry(add: (text: string) => boolean) {
  const [draft, setDraft] = useState('')
  const submit = () => {
    if (add(draft)) setDraft('')
  }
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
    event.preventDefault()
    submit()
  }
  return { draft, setDraft, submit, onKeyDown }
}

function parseEntry(text: string, type: string | undefined): unknown {
  const trimmed = text.trim()
  if (!trimmed) return undefined
  if (type === 'integer' || type === 'number') {
    const parsed = Number(trimmed)
    return Number.isFinite(parsed) ? parsed : undefined
  }
  return trimmed
}

/** 可选值有限的 tags：每个选项一个可切换芯片（例如公招 Tag 等级 1–6）。 */
export function ChoiceTagsControl({ labelId, schema, root, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const values = Array.isArray(value) ? value : []
  const options = arrayItemOptions(schema, root)
  const { maxItems } = arrayBounds(schema)
  return (
    <div className="space-y-1.5">
      <div role="group" aria-labelledby={labelId} {...ariaProps({ invalid, describedBy })} className="flex flex-wrap gap-2">
        {options.map((option) => {
          const active = values.some((entry) => Object.is(entry, option.value))
          return (
            <Chip
              key={option.key}
              selected={active}
              className="h-11 min-w-11"
              disabled={disabled || (!active && values.length >= maxItems)}
              onClick={() => onChange(active ? values.filter((entry) => !Object.is(entry, option.value)) : [...values, option.value])}
            >
              {option.label}
            </Chip>
          )
        })}
      </div>
      <EmptyHint isNull={isNull} />
    </div>
  )
}

/** 可选值有限的有序列表：已选项按顺序成行，可上移、下移、移除；下方点选剩余选项追加到末尾。 */
export function ChoiceOrderedListControl({ labelId, schema, root, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const values = Array.isArray(value) ? value : []
  const options = arrayItemOptions(schema, root)
  const { minItems, maxItems } = arrayBounds(schema)
  const labelOf = (entry: unknown) => options.find((option) => Object.is(option.value, entry))?.label ?? String(entry)
  const remaining = options.filter((option) => !values.some((entry) => Object.is(entry, option.value)))
  return (
    <div role="group" aria-labelledby={labelId} {...ariaProps({ invalid, describedBy })} className="space-y-2.5">
      {values.length > 0 ? (
        <ol className="divide-y overflow-hidden rounded-lg bg-surface-2">
          {values.map((entry, index) => {
            const text = labelOf(entry)
            return (
              <li key={`${String(entry)}-${index}`} className="flex min-h-12 items-center gap-1 py-0.5 pl-3 pr-1">
                <span className="w-5 shrink-0 text-footnote tabular-nums text-muted-foreground">{index + 1}</span>
                <span className="min-w-0 flex-1 truncate text-[0.9375rem]">{text}</span>
                <Button type="button" variant="ghost" size="icon" aria-label={`上移 ${text}`} disabled={disabled || index === 0} onClick={() => onChange(moveArrayItem(values, index, -1))}>
                  <ArrowUp aria-hidden="true" />
                </Button>
                <Button type="button" variant="ghost" size="icon" aria-label={`下移 ${text}`} disabled={disabled || index === values.length - 1} onClick={() => onChange(moveArrayItem(values, index, 1))}>
                  <ArrowDown aria-hidden="true" />
                </Button>
                <Button type="button" variant="ghost" size="icon" aria-label={`移除 ${text}`} disabled={disabled || values.length <= minItems} onClick={() => onChange(updateArrayItem(values, index, UNSET))}>
                  <X aria-hidden="true" />
                </Button>
              </li>
            )
          })}
        </ol>
      ) : (
        <EmptyHint isNull={isNull} text="尚未添加，点下方选项按顺序加入。" />
      )}
      {remaining.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {remaining.map((option) => (
            <Button
              key={option.key}
              type="button"
              variant="outline"
              size="sm"
              className="rounded-full"
              aria-label={`添加 ${option.label}`}
              disabled={disabled || values.length >= maxItems}
              onClick={() => onChange([...values, option.value])}
            >
              <Plus aria-hidden="true" />
              {option.label}
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/** 自由输入列表共用的新增行：输入框回车或点「添加」追加一项，数字类型按数字解析。 */
function EntryRow({ id, label, schema, root, values, disabled, invalid, describedBy, onChange }: ControlProps & { values: unknown[] }) {
  const type = itemType(schema, root)
  const full = values.length >= arrayBounds(schema).maxItems
  const entry = useDraftEntry((text) => {
    const parsed = parseEntry(text, type)
    if (parsed === undefined || full) return false
    onChange([...values, parsed])
    return true
  })
  return (
    <div className="flex gap-2">
      <Input
        id={id}
        value={entry.draft}
        placeholder="输入后按回车添加"
        enterKeyHint="done"
        disabled={disabled || full}
        {...ariaProps({ invalid, describedBy })}
        onChange={(event) => entry.setDraft(event.target.value)}
        onKeyDown={entry.onKeyDown}
      />
      <Button type="button" variant="outline" aria-label={`添加${label}`} disabled={disabled || full || !entry.draft.trim()} onClick={entry.submit}>
        <Plus aria-hidden="true" />
        添加
      </Button>
    </div>
  )
}

/** 自由输入的 tags：芯片显示已有项，输入框回车添加。 */
export function TagsControl(props: ControlProps) {
  const { schema, value, isNull, disabled, onChange } = props
  const values = Array.isArray(value) ? value : []
  const removable = !disabled && values.length > arrayBounds(schema).minItems
  return (
    <div className="space-y-2">
      {values.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {values.map((item, index) =>
            removable ? (
              <RemovableChip key={`${String(item)}-${index}`} removeLabel={`移除 ${String(item)}`} onRemove={() => onChange(updateArrayItem(values, index, UNSET))}>
                {String(item)}
              </RemovableChip>
            ) : (
              <span key={`${String(item)}-${index}`} className="inline-flex h-8 max-w-full items-center truncate rounded-full bg-surface-2 px-3 text-[0.8125rem] font-medium">
                {String(item)}
              </span>
            ),
          )}
        </div>
      ) : (
        <EmptyHint isNull={isNull} />
      )}
      <EntryRow {...props} values={values} />
    </div>
  )
}

/** 自由输入的列表（含无固定选项的 ordered-list）：每项一行，可上移、下移、删除。 */
export function ListControl(props: ControlProps) {
  const { schema, value, isNull, disabled, onChange } = props
  const values = Array.isArray(value) ? value : []
  const { minItems } = arrayBounds(schema)
  return (
    <div className="space-y-2.5">
      {values.length > 0 ? (
        <ol className="divide-y overflow-hidden rounded-lg bg-surface-2">
          {values.map((item, index) => (
            <li key={`${String(item)}-${index}`} className="flex min-h-12 items-center gap-1 py-0.5 pl-3 pr-1">
              <span className="min-w-0 flex-1 break-all text-[0.9375rem]">{String(item)}</span>
              <Button type="button" variant="ghost" size="icon" aria-label={`上移第 ${index + 1} 项`} disabled={disabled || index === 0} onClick={() => onChange(moveArrayItem(values, index, -1))}>
                <ArrowUp aria-hidden="true" />
              </Button>
              <Button type="button" variant="ghost" size="icon" aria-label={`下移第 ${index + 1} 项`} disabled={disabled || index === values.length - 1} onClick={() => onChange(moveArrayItem(values, index, 1))}>
                <ArrowDown aria-hidden="true" />
              </Button>
              <Button type="button" variant="ghost" size="icon" aria-label={`删除第 ${index + 1} 项`} disabled={disabled || values.length <= minItems} onClick={() => onChange(updateArrayItem(values, index, UNSET))}>
                <Trash2 aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ol>
      ) : (
        <EmptyHint isNull={isNull} />
      )}
      <EntryRow {...props} values={values} />
    </div>
  )
}
