import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { CircleSlash, Link2, Lock, TriangleAlert, type LucideIcon } from 'lucide-react'
import { ActionMenu } from '@/components/ui/action-sheet'
import { Badge, StatusDot } from '@/components/ui/badge'
import { Label } from '@/components/ui/label'
import {
  UNSET,
  dependencyHint,
  dependencyState,
  fieldLabel,
  fieldState,
  normalizeSchema,
  objectValue,
  requiredFields,
  resetValueFor,
  resolveWidget,
  riskKind,
  riskLabel,
  schemaProperties,
  setObjectValue,
  type FieldState,
  type JsonSchema,
  type Unset,
  type WidgetKind,
} from '../form'
import { ariaProps, type ControlProps } from './control-props'
import { CopilotAssetControl } from './CopilotAssetControl'
import { JsonControl } from './JsonControl'
import { ChoiceOrderedListControl, ChoiceTagsControl, ListControl, TagsControl } from './ListControls'
import { DurationMapControl, ItemCountMapControl, KeyValueMapControl } from './MapControls'
import { NumberControl, SegmentedField, SelectControl, SwitchControl, TextControl } from './ScalarControls'

/** 由一组控件组成、用 aria-labelledby 关联字段标签的控件。 */
const GROUP_WIDGETS: ReadonlySet<WidgetKind> = new Set(['item-count-map', 'duration-map', 'key-value', 'object', 'choice-tags', 'choice-ordered-list', 'segmented'])
const DESCRIPTION_FALLBACK_LENGTH = 26
/** 行内文字按钮；伪元素把触控区域扩到约 44px 高而不放大字号。不经 cn()：tailwind-merge 会把 text-footnote 当成颜色类合并掉。 */
const INLINE_ACTION_CLASS =
  "relative rounded-sm text-footnote font-medium text-primary-text before:absolute before:-inset-x-2 before:-inset-y-3 before:content-[''] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45"

export function fieldDomId(idPrefix: string, path: readonly string[]): string {
  return `${idPrefix}-${path.map((part) => part.replace(/[^a-zA-Z0-9_-]/g, '-')).join('-')}`
}

/**
 * 五档状态标签（docs/09 §7.5）：已修改（带圆点）/ 已设为空 / 必填 / 默认 / 未设置。
 * 「必填」优先于「未设置」——没有默认值的必填字段提示用户必须填，而不是「可以留空」。
 */
function StateTag({ id, state, required }: { id: string; state: FieldState; required: boolean }) {
  if (state === 'modified' || state === 'null') {
    return (
      <span id={id} className="inline-flex items-center gap-1 text-footnote font-medium text-primary-text">
        {state === 'modified' ? <StatusDot tone="primary" /> : null}
        {state === 'modified' ? '已修改' : '已设为空'}
      </span>
    )
  }
  if (required) return <span id={id} className="text-footnote font-medium text-warning">必填</span>
  return <span id={id} className="text-footnote text-muted-foreground">{state === 'default' ? '默认' : '未设置'}</span>
}

function FieldNote({ id, icon: Icon, children }: { id: string; icon: LucideIcon; children: ReactNode }) {
  return (
    <p id={id} className="flex items-start gap-1.5 text-footnote text-muted-foreground">
      <Icon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
      {children}
    </p>
  )
}

/** 说明默认一行截断；放不下时显示「展开说明」。 */
function FieldDescription({ id, text }: { id: string; text: string }) {
  const ref = useRef<HTMLParagraphElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(() => Array.from(text).length > DESCRIPTION_FALLBACK_LENGTH)

  useLayoutEffect(() => {
    const node = ref.current
    if (!node || expanded) return
    // 折叠分组里的说明没有布局（宽度为 0），先沿用按字数的估计，展开后再实测。
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
      <p id={id} ref={ref} className={`min-w-0 flex-1 text-footnote text-muted-foreground${expanded ? '' : ' line-clamp-1'}`}>{text}</p>
      {overflowing || expanded ? (
        <button type="button" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded((current) => !current)} className={`shrink-0 ${INLINE_ACTION_CLASS}`}>
          {expanded ? '收起' : '展开说明'}
        </button>
      ) : null}
    </div>
  )
}

interface NestedContext {
  path: string[]
  idPrefix: string
}

/** 有 properties 的嵌套对象：逐个子字段编辑，子字段全部恢复默认时整个对象也恢复默认。 */
function ObjectControl({ labelId, schema, root, value, disabled, invalid, describedBy, onChange, path, idPrefix }: ControlProps & NestedContext) {
  const map = objectValue(value)
  const properties = schemaProperties(schema)
  const required = requiredFields(schema)
  return (
    <div role="group" aria-labelledby={labelId} {...ariaProps({ invalid, describedBy })} className="space-y-4 border-l-2 pl-3">
      {Object.entries(properties).map(([key, child]) => (
        <FieldEditor
          key={key}
          name={key}
          schema={child}
          root={root}
          path={[...path, key]}
          idPrefix={idPrefix}
          value={map[key]}
          formValues={map}
          siblings={properties}
          disabled={disabled}
          required={required.has(key)}
          onChange={(next) => {
            const changed = setObjectValue(map, [key], next)
            onChange(Object.keys(changed).length === 0 && next === UNSET ? UNSET : changed)
          }}
        />
      ))}
    </div>
  )
}

function renderControl(widget: WidgetKind, props: ControlProps, nested: NestedContext) {
  switch (widget) {
    case 'copilot-asset':
      return <CopilotAssetControl {...props} />
    case 'json':
      return <JsonControl {...props} />
    case 'item-count-map':
      return <ItemCountMapControl {...props} />
    case 'duration-map':
      return <DurationMapControl {...props} />
    case 'key-value':
      return <KeyValueMapControl {...props} />
    case 'object':
      return <ObjectControl {...props} {...nested} />
    case 'choice-tags':
      return <ChoiceTagsControl {...props} />
    case 'choice-ordered-list':
      return <ChoiceOrderedListControl {...props} />
    case 'tags':
      return <TagsControl {...props} />
    case 'ordered-list':
    case 'list':
      return <ListControl {...props} />
    case 'segmented':
      return <SegmentedField {...props} />
    case 'select':
      return <SelectControl {...props} />
    case 'switch':
      return <SwitchControl {...props} />
    case 'number':
      return <NumberControl {...props} />
    case 'textarea':
      return <TextControl {...props} multiline />
    case 'text':
      return <TextControl {...props} />
  }
}

export interface FieldEditorProps {
  name: string
  /** 未经 normalizeSchema 的属性 schema。 */
  schema: JsonSchema
  root: JsonSchema
  /** `[任务类型, 字段名, …]`，用于生成 DOM id 与识别 Copilot 作业字段。 */
  path: string[]
  idPrefix: string
  value: unknown
  /** 同级字段的草稿值，用于判断 x-depends-on。 */
  formValues: Record<string, unknown>
  siblings: Record<string, JsonSchema>
  disabled: boolean
  immutable?: boolean
  required?: boolean
  error?: string
  onChange: (next: unknown | Unset) => void
}

/** 一个字段：标签、状态（默认 / 已修改）、恢复默认、「更多」里的明确留空、唯一的控件、错误与说明。 */
export function FieldEditor({ name, schema: inputSchema, root, path, idPrefix, value, formValues, siblings, disabled, immutable = false, required = false, error, onChange }: FieldEditorProps) {
  const normalized = normalizeSchema(inputSchema, root)
  const schema = normalized.schema
  const label = fieldLabel(schema, name)
  const widget = resolveWidget(inputSchema, root, path)
  const baseId = fieldDomId(idPrefix, path)
  const ids = {
    field: `${baseId}-field`,
    label: `${baseId}-label`,
    state: `${baseId}-state`,
    risk: `${baseId}-risk`,
    note: `${baseId}-note`,
    error: `${baseId}-error`,
    description: `${baseId}-description`,
  }
  const dependency = dependencyState(schema, formValues)
  const dependencyText = dependency.satisfied ? '' : dependencyHint(dependency.unmet, siblings, root)
  const locked = disabled || immutable
  const controlDisabled = locked || !dependency.satisfied
  const state = fieldState(formValues, name, schema)
  const isNull = value === null
  const resettable = state === 'modified' || state === 'null'
  const risk = riskKind(schema)
  const description = typeof schema.description === 'string' ? schema.description : ''
  const hasNote = immutable || Boolean(dependencyText)
  const describedBy = [error ? ids.error : '', ids.state, risk ? ids.risk : '', hasNote ? ids.note : '', description ? ids.description : ''].filter(Boolean).join(' ')
  const control = renderControl(
    widget,
    { id: baseId, labelId: ids.label, label, schema, root, value, present: value !== undefined, isNull, disabled: controlDisabled, invalid: Boolean(error), describedBy, onChange },
    { path, idPrefix },
  )
  const inline = widget === 'switch'

  return (
    <div id={ids.field} className="space-y-2">
      <div className="flex min-h-11 items-center gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
          <Label id={ids.label} htmlFor={GROUP_WIDGETS.has(widget) ? undefined : baseId}>{label}</Label>
          {risk ? (
            <Badge id={ids.risk} variant="warning">
              <TriangleAlert aria-hidden="true" />
              {riskLabel(risk)}
            </Badge>
          ) : null}
          <StateTag id={ids.state} state={state} required={required} />
          {resettable ? (
            <button type="button" className={INLINE_ACTION_CLASS} aria-label={`恢复默认：${label}`} disabled={locked} onClick={() => onChange(resetValueFor(schema))}>
              恢复默认
            </button>
          ) : null}
        </div>
        {normalized.nullable ? (
          <ActionMenu
            label={`${label}的更多操作`}
            title={label}
            className="text-muted-foreground"
            disabled={controlDisabled}
            actions={[{ label: '明确留空', description: '提交空值（null），而不是使用默认。', icon: CircleSlash, disabled: isNull, onSelect: () => onChange(null) }]}
          />
        ) : null}
        {inline ? control : null}
      </div>
      {inline ? null : control}
      {error ? <p id={ids.error} role="alert" className="text-footnote font-medium text-danger">{error}</p> : null}
      {immutable ? <FieldNote id={ids.note} icon={Lock}>运行中不可修改</FieldNote> : dependencyText ? <FieldNote id={ids.note} icon={Link2}>{dependencyText}</FieldNote> : null}
      {description ? <FieldDescription id={ids.description} text={description} /> : null}
    </div>
  )
}
