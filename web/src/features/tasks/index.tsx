import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowDown, ArrowUp, Check, ChevronDown, ChevronUp, CircleHelp, LoaderCircle, Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { api } from '@/api/client'
import { ApiError, toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export type JsonSchema = Record<string, unknown>

export interface TaskTypeSchema {
  name: string
  label: string
  description: string
  schema: JsonSchema
  groups: string[]
  runtime_immutable: string[]
}

export interface DynamicTaskFormProps {
  taskType: TaskTypeSchema
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
  disabled?: boolean
  errors?: Readonly<Record<string, string>>
  runtimeEditing?: boolean
}

type PipelineRow = Record<string, unknown> & {
  id?: string
  pipeline_id?: string
  title?: string | null
  status?: string
  source?: string
  priority?: number
  task_count?: number
  created_at?: string | null
  error?: { message?: string | null; code?: string | null } | null
  tasks?: Array<Record<string, unknown>>
}

type PageEnvelope<T> = { items: T[]; total: number; page: number; size: number }
type TaskTypesEnvelope = PageEnvelope<TaskTypeSchema>
type ItemCatalogEntry = { item_id: string; name: string; icon_url: string | null }
type QueueSnapshot = {
  running?: PipelineRow | null
  pending?: PipelineRow[]
  paused?: boolean
  counts?: { pending?: number; running?: number }
}
type PipelineLogRecord = {
  id: number
  source: string
  level: string
  content: string
  ts: number
  pipeline_id: string
  task_id: string | null
  logger: string | null
  attachment: unknown
}
type PipelineScreenshotRecord = {
  id: string
  pipeline_id: string
  task_id: string | null
  trigger: string
  backend: string
  path: string
  format: string
  width: number
  height: number
  size_bytes: number
  deleted_at: string | null
  created_at: string | null
}

const taskTypesKey = [...keys.tasks.all(), 'catalog'] as const
const itemCatalogKey = [...keys.all, 'resources', 'items'] as const
const historyPageSize = 20
const UNSET = Symbol('unset')
type Unset = typeof UNSET

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function objectValue(value: unknown): Record<string, unknown> {
  return isObject(value) ? value : {}
}

function textValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function schemaProperties(schema: JsonSchema): Record<string, JsonSchema> {
  const properties = schema.properties
  if (!isObject(properties)) return {}
  return Object.fromEntries(Object.entries(properties).filter(([, child]) => isObject(child))) as Record<string, JsonSchema>
}

function resolveReference(schema: JsonSchema, root: JsonSchema): JsonSchema {
  if (typeof schema.$ref !== 'string' || !schema.$ref.startsWith('#/')) return schema
  let current: unknown = root
  for (const segment of schema.$ref.slice(2).split('/').map((part) => part.replaceAll('~1', '/').replaceAll('~0', '~'))) {
    current = isObject(current) ? current[segment] : undefined
  }
  if (!isObject(current)) return schema
  return { ...current, ...schema, $ref: undefined }
}

interface NormalizedSchema {
  schema: JsonSchema
  nullable: boolean
  complex: boolean
}

function normalizeSchema(input: JsonSchema, root: JsonSchema, depth = 0): NormalizedSchema {
  if (depth > 12) return { schema: input, nullable: false, complex: true }
  const resolved = resolveReference(input, root)
  const schema = { ...resolved }
  let nullable = schema.nullable === true
  delete schema.nullable

  const rawType = schema.type
  if (Array.isArray(rawType)) {
    const types = rawType.filter((entry) => entry !== 'null')
    nullable ||= types.length !== rawType.length
    if (types.length === 1) schema.type = types[0]
    else if (types.length > 1) return { schema, nullable, complex: true }
    else return { schema, nullable: true, complex: true }
  }

  const unionKey = Array.isArray(schema.anyOf) ? 'anyOf' : Array.isArray(schema.oneOf) ? 'oneOf' : null
  if (unionKey) {
    const branches = schema[unionKey] as unknown[]
    const nonNull: JsonSchema[] = []
    for (const branch of branches) {
      if (!isObject(branch)) return { schema, nullable, complex: true }
      const resolvedBranch = resolveReference(branch, root)
      if (resolvedBranch.type === 'null' || resolvedBranch.const === null || (Array.isArray(resolvedBranch.enum) && resolvedBranch.enum.length === 1 && resolvedBranch.enum[0] === null)) {
        nullable = true
      } else nonNull.push(resolvedBranch)
    }
    delete schema[unionKey]
    if (nonNull.length !== 1) return { schema, nullable, complex: true }
    const branch = normalizeSchema(nonNull[0], root, depth + 1)
    return { schema: { ...schema, ...branch.schema }, nullable: nullable || branch.nullable, complex: branch.complex }
  }

  return { schema, nullable, complex: false }
}

function getType(schema: JsonSchema): string | undefined {
  if (typeof schema.type === 'string') return schema.type
  if (schema.properties || schema.additionalProperties) return 'object'
  if (schema.items) return 'array'
  return undefined
}

function fieldLabel(schema: JsonSchema, key: string): string {
  return textValue(schema['x-label'], textValue(schema.title, key))
}

function scalarEnumValue(serialized: string): unknown {
  try {
    return JSON.parse(serialized) as unknown
  } catch {
    return serialized
  }
}

function enumLabels(schema: JsonSchema): Record<string, string> {
  return isObject(schema['x-enum-labels']) ? schema['x-enum-labels'] as Record<string, string> : {}
}

function setObjectValue(value: Record<string, unknown>, path: string[], next: unknown | Unset): Record<string, unknown> {
  const [head, ...rest] = path
  if (!head) return value
  const result = { ...value }
  if (rest.length === 0) {
    if (next === UNSET) delete result[head]
    else result[head] = next
    return result
  }
  const child = objectValue(result[head])
  const changed = setObjectValue(child, rest, next)
  if (Object.keys(changed).length === 0 && next === UNSET) delete result[head]
  else result[head] = changed
  return result
}

function updateArrayItem(values: unknown[], index: number, next: unknown | Unset): unknown[] {
  const result = [...values]
  if (next === UNSET) result.splice(index, 1)
  else result[index] = next
  return result
}

interface DynamicDurationMapProps {
  id: string
  value: Record<string, unknown>
  disabled: boolean
  onChange: (next: unknown | Unset) => void
  error?: string
}

function DynamicDurationMap({ id, value, disabled, onChange, error }: DynamicDurationMapProps) {
  const fixedLevels = ['3', '4', '5', '6']
  return <div className="space-y-3">
    <p className="text-xs text-muted-foreground">招募等级固定为 3–6 星 Tag；默认时长为 09:00，编辑后按分钟提交。</p>
    <ul className="space-y-2">{fixedLevels.map((key) => {
      const minutes = typeof value[key] === 'number' ? value[key] as number : 540
      const hours = Math.floor(minutes / 60)
      const remainder = minutes % 60
      return <li key={key} className="grid grid-cols-[minmax(0,1fr)_5rem_5rem_2.75rem] items-end gap-2 rounded-lg border p-2">
        <span className="pb-3 text-sm">{key} 星 Tag</span>
        <div><Label className="sr-only" htmlFor={`${id}-${key}-hours`}>时长小时：{key}</Label><Input id={`${id}-${key}-hours`} type="number" min={0} step={1} aria-label={`时长小时：${key}`} value={hours} disabled={disabled} onChange={(event) => {
          const nextHours = event.target.value === '' ? 0 : Number(event.target.value)
          if (Number.isFinite(nextHours)) onChange({ ...value, [key]: nextHours * 60 + remainder })
        }} /></div>
        <div><Label className="sr-only" htmlFor={`${id}-${key}-minutes`}>时长分钟：{key}</Label><Input id={`${id}-${key}-minutes`} type="number" min={0} max={59} step={1} aria-label={`时长分钟：${key}`} value={remainder} disabled={disabled} onChange={(event) => {
          const nextMinutes = event.target.value === '' ? 0 : Number(event.target.value)
          if (Number.isFinite(nextMinutes)) onChange({ ...value, [key]: hours * 60 + nextMinutes })
        }} /></div>
        <Button type="button" size="sm" variant="ghost" className="min-h-11" aria-label={`恢复${key}星 Tag 默认时长`} disabled={disabled || (value[key] === undefined && minutes === 540)} onClick={() => onChange({ ...value, [key]: 540 })}>恢复默认</Button>
      </li>
    })}</ul>
    {error && <p id={`${id}-error`} className="text-sm text-destructive" role="alert">{error}</p>}
  </div>
}

function errorId(path: string[]): string {
  return `task-field-${path.map((part) => part.replace(/[^a-zA-Z0-9_-]/g, '-')).join('-')}`
}

interface ValueEditorProps {
  schema: JsonSchema
  root: JsonSchema
  value: unknown
  nullable?: boolean
  disabled?: boolean
  path: string[]
  onChange: (next: unknown | Unset) => void
  items?: ItemCatalogEntry[]
  itemCatalogLoading?: boolean
  itemCatalogError?: string | null
  error?: string
}

function ValueEditor({
  schema: inputSchema,
  root,
  value,
  nullable = false,
  disabled = false,
  path,
  onChange,
  items = [],
  itemCatalogLoading = false,
  itemCatalogError = null,
  error,
}: ValueEditorProps) {
  const normalized = normalizeSchema(inputSchema, root)
  const schema = normalized.schema
  const initialJson = (() => {
    try {
      return value === undefined ? '' : JSON.stringify(value, null, 2)
    } catch {
      return ''
    }
  })()
  const [jsonDraft, setJsonDraft] = useState(initialJson)
  const [parseError, setParseError] = useState<string | null>(null)
  const [itemSearch, setItemSearch] = useState('')
  const [fallbackItemId, setFallbackItemId] = useState('')
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [arrayDraft, setArrayDraft] = useState('')
  const isNullable = nullable || normalized.nullable
  const widget = textValue(schema['x-widget'])
  const type = getType(schema)
  const options = Array.isArray(schema.enum) ? schema.enum : []
  const id = errorId(path)
  const ariaError = error ? { 'aria-invalid': true as const, 'aria-describedby': `${id}-error` } : {}

  const stateSelector = isNullable ? (
    <div className="mb-2 flex flex-wrap gap-2">
      <Button type="button" size="sm" variant={value === undefined ? 'secondary' : 'outline'} className="min-h-11" disabled={disabled} onClick={() => onChange(UNSET)}>
        使用内核默认
      </Button>
      <Button type="button" size="sm" variant={value === null ? 'secondary' : 'outline'} className="min-h-11" disabled={disabled} onClick={() => onChange(null)}>
        明确留空
      </Button>
    </div>
  ) : null

  if (normalized.complex || !type) {
    return (
      <div className="space-y-2">
        {stateSelector}
        <p className="flex items-start gap-2 text-xs text-muted-foreground"><CircleHelp className="mt-0.5 size-4 shrink-0" aria-hidden="true" />此参数使用 JSON 编辑器；输入完整 JSON 值后自动应用。</p>
        <textarea
          id={id}
          className="min-h-28 w-full rounded-lg border border-input bg-background px-3 py-2 font-mono text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          aria-label={`${fieldLabel(schema, path.at(-1) ?? '参数')} JSON 值`}
          disabled={disabled}
          value={jsonDraft}
          {...ariaError}
          onChange={(event) => {
            setJsonDraft(event.target.value)
            try {
              const parsed: unknown = JSON.parse(event.target.value)
              onChange(parsed)
              setParseError(null)
            } catch {
              setParseError('JSON 格式尚未完整或无效')
            }
          }}
        />
        {parseError && <p className="text-xs text-destructive" role="status">{parseError}</p>}
        {error && <p id={`${id}-error`} className="text-sm text-destructive" role="alert">{error}</p>}
      </div>
    )
  }

  if (widget === 'item-count-map') {
    const map = objectValue(value)
    const selectedItems = Object.entries(map)
    return (
      <div className="space-y-3">
        {stateSelector}
        <p className="text-xs text-muted-foreground">任一物品达到指定数量即停止任务。</p>
        {selectedItems.length > 0 ? <ul className="space-y-2">
          {selectedItems.map(([itemId, quantity]) => {
            const item = items.find((entry) => entry.item_id === itemId)
            return <li key={itemId} className="flex flex-wrap items-center gap-2 rounded-lg border p-2">
              {item?.icon_url && <img src={item.icon_url} alt="" className="size-8 rounded object-contain" />}
              <span className="min-w-24 flex-1 text-sm">{item?.name ?? `物品 ${itemId}`} <span className="text-xs text-muted-foreground">({itemId})</span></span>
              <Label className="sr-only" htmlFor={`${id}-${itemId}-count`}>数量：{item?.name ?? itemId}</Label>
              <Input id={`${id}-${itemId}-count`} className="w-24" type="number" min={1} step={1} value={typeof quantity === 'number' ? quantity : 1} disabled={disabled} onChange={(event) => {
                if (event.target.value === '') return
                const amount = Number(event.target.value)
                if (Number.isFinite(amount) && amount >= 0) onChange({ ...map, [itemId]: amount })
              }} />
              <Button type="button" size="icon" variant="ghost" aria-label={`删除物品：${item?.name ?? itemId}`} disabled={disabled} onClick={() => {
                const changed = { ...map }
                delete changed[itemId]
                onChange(Object.keys(changed).length ? changed : UNSET)
              }}><Trash2 aria-hidden="true" /></Button>
            </li>
          })}
        </ul> : <p className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">尚未添加物品数量条件。</p>}
        {items.length > 0 ? <div className="space-y-2">
          <Label className="sr-only" htmlFor={`${id}-item-search`}>搜索物品名称或 ID</Label>
          <Input id={`${id}-item-search`} value={itemSearch} placeholder={itemCatalogLoading ? '正在加载物品…' : '搜索物品名称或 ID'} disabled={disabled || itemCatalogLoading} onChange={(event) => setItemSearch(event.target.value)} />
          <ul className="max-h-56 space-y-1 overflow-y-auto rounded-lg border p-2" aria-label="物品搜索结果">
            {items.filter((item) => !(item.item_id in map) && (!itemSearch.trim() || `${item.name} ${item.item_id}`.toLocaleLowerCase().includes(itemSearch.trim().toLocaleLowerCase()))).slice(0, 30).map((item) => <li key={item.item_id}>
              <Button type="button" variant="ghost" className="w-full justify-start" disabled={disabled} onClick={() => {
                onChange({ ...map, [item.item_id]: 1 })
                setItemSearch('')
              }}>
                {item.icon_url && <img src={item.icon_url} alt="" className="size-6 rounded object-contain" />}
                <span className="truncate">添加 {item.name} <span className="text-xs text-muted-foreground">({item.item_id})</span></span>
              </Button>
            </li>)}
          </ul>
        </div> : <div className="flex flex-col gap-2 sm:flex-row">
          <Label className="sr-only" htmlFor={`${id}-item-id`}>物品 ID</Label>
          <Input id={`${id}-item-id`} className="flex-1" placeholder={itemCatalogLoading ? '物品目录加载中' : '输入物品 ID'} value={fallbackItemId} disabled={disabled} onChange={(event) => setFallbackItemId(event.target.value)} />
          <Button type="button" variant="outline" disabled={disabled || !fallbackItemId.trim() || fallbackItemId in map} onClick={() => {
            const nextId = fallbackItemId.trim()
            if (!nextId) return
            onChange({ ...map, [nextId]: 1 })
            setFallbackItemId('')
          }}><Plus aria-hidden="true" />添加 ID</Button>
        </div>}
        {itemCatalogError && <p className="text-xs text-muted-foreground" role="status">物品目录不可用，可手动输入物品 ID。{itemCatalogError}</p>}
        {error && <p id={`${id}-error`} className="text-sm text-destructive" role="alert">{error}</p>}
      </div>
    )
  }

  if (widget === 'duration-map') {
    const map = objectValue(value)
    return <div className="space-y-3">{stateSelector}<DynamicDurationMap id={id} value={map} disabled={disabled} onChange={onChange} error={error} /></div>
  }

  if (widget === 'item-count-map' || (type === 'object' && !schema.properties && widget !== 'duration-map')) {
    const map = objectValue(value)
    const additionalSchema = isObject(schema.additionalProperties) ? schema.additionalProperties as JsonSchema : { type: 'integer' }
    const entries = Object.entries(map)
    return <div className="space-y-3">
      {stateSelector}
      {entries.length ? <ul className="space-y-2">{entries.map(([key, entryValue]) => <li key={key} className="grid grid-cols-[minmax(0,1fr)_minmax(5rem,8rem)_2.75rem] gap-2">
        <Label className="sr-only" htmlFor={`${id}-${key}-key`}>键</Label>
        <Input id={`${id}-${key}-key`} value={key} disabled={disabled} onChange={(event) => {
          const nextKey = event.target.value
          if (nextKey === key || nextKey in map) return
          const next = { ...map }
          delete next[key]
          next[nextKey] = entryValue
          onChange(next)
        }} />
        <Label className="sr-only" htmlFor={`${id}-${key}-value`}>值：{key}</Label>
        <Input id={`${id}-${key}-value`} type={getType(normalizeSchema(additionalSchema, root).schema) === 'number' || getType(normalizeSchema(additionalSchema, root).schema) === 'integer' ? 'number' : 'text'} value={typeof entryValue === 'string' || typeof entryValue === 'number' ? entryValue : JSON.stringify(entryValue)} disabled={disabled} onChange={(event) => {
          const childType = getType(normalizeSchema(additionalSchema, root).schema)
          const next: unknown = childType === 'integer' || childType === 'number' ? Number(event.target.value) : event.target.value
          if ((typeof next === 'number' && Number.isFinite(next)) || typeof next === 'string') onChange({ ...map, [key]: next })
        }} />
        <Button type="button" size="icon" variant="ghost" aria-label={`删除键 ${key}`} disabled={disabled} onClick={() => {
          const next = { ...map }
          delete next[key]
          onChange(Object.keys(next).length ? next : UNSET)
        }}><Trash2 aria-hidden="true" /></Button>
      </li>)}</ul> : <p className="text-sm text-muted-foreground">尚未添加映射项。</p>}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Label className="sr-only" htmlFor={`${id}-new-key`}>新键</Label>
        <Input id={`${id}-new-key`} placeholder="键" value={newKey} disabled={disabled} onChange={(event) => setNewKey(event.target.value)} />
        <Label className="sr-only" htmlFor={`${id}-new-value`}>新值</Label>
        <Input id={`${id}-new-value`} placeholder="值" type={getType(normalizeSchema(additionalSchema, root).schema) === 'integer' ? 'number' : 'text'} value={newValue} disabled={disabled} onChange={(event) => setNewValue(event.target.value)} />
        <Button type="button" variant="outline" disabled={disabled || !newKey.trim() || newKey in map || newValue === ''} onClick={() => {
          if (!newKey.trim() || newKey in map || newValue === '') return
          const childType = getType(normalizeSchema(additionalSchema, root).schema)
          const parsed: unknown = childType === 'integer' || childType === 'number' ? Number(newValue) : newValue
          if (typeof parsed === 'number' && !Number.isFinite(parsed)) return
          onChange({ ...map, [newKey.trim()]: parsed })
          setNewKey('')
          setNewValue('')
        }}><Plus aria-hidden="true" />添加</Button>
      </div>
      {error && <p id={`${id}-error`} className="text-sm text-destructive" role="alert">{error}</p>}
    </div>
  }

  if (type === 'object') {
    const map = objectValue(value)
    const properties = schemaProperties(schema)
    return <div className="space-y-3 rounded-lg border p-3">
      {stateSelector}
      {Object.entries(properties).map(([key, child]) => <FieldEditor key={key} name={key} schema={child} root={root} value={map[key]} path={[...path, key]} formValues={map} disabled={disabled} onChange={(next) => onChange(setObjectValue(map, [key], next))} items={items} />)}
      {error && <p id={`${id}-error`} className="text-sm text-destructive" role="alert">{error}</p>}
    </div>
  }

  if (type === 'array') {
    const values = Array.isArray(value) ? value : []
    const itemSchema = isObject(schema.items) ? schema.items as JsonSchema : {}
    const normalizedItem = normalizeSchema(itemSchema, root)
    const itemType = getType(normalizedItem.schema)
    const itemOptions = Array.isArray(normalizedItem.schema.enum) ? normalizedItem.schema.enum : []
    const minItems = typeof schema.minItems === 'number' ? schema.minItems : 0
    const maxItems = typeof schema.maxItems === 'number' ? schema.maxItems : Number.POSITIVE_INFINITY
    if ((widget === 'tags' || widget === 'ordered-list') && itemOptions.length > 0) {
      const addOption = (candidate: unknown) => {
        if (values.length < maxItems && !values.some((entry) => Object.is(entry, candidate))) onChange([...values, candidate])
      }
      return <div className="space-y-3">
        {stateSelector}
        {widget === 'tags' ? <div className="flex flex-wrap gap-2">
          {itemOptions.map((option) => {
            const active = values.some((entry) => Object.is(entry, option))
            return <Button key={JSON.stringify(option)} type="button" size="sm" variant={active ? 'secondary' : 'outline'} className="min-h-11" disabled={disabled || (!active && values.length >= maxItems)} onClick={() => onChange(active ? values.filter((entry) => !Object.is(entry, option)) : [...values, option])} aria-pressed={active}>
              {active ? <Check aria-hidden="true" /> : <Plus aria-hidden="true" />}{enumLabels(normalizedItem.schema)[String(option)] ?? String(option)}
            </Button>
          })}
        </div> : <>
          <ol className="space-y-2">{values.map((entry, index) => <li key={`${String(entry)}-${index}`} className="flex items-center gap-2 rounded-lg border p-2">
            <span className="min-w-0 flex-1 text-sm">{enumLabels(normalizedItem.schema)[String(entry)] ?? String(entry)}</span>
            <span className="text-xs text-muted-foreground">{index + 1}</span>
            <Button type="button" size="icon" variant="outline" aria-label={`上移${String(entry)}`} disabled={disabled || index === 0} onClick={() => {
              const next = [...values]
              ;[next[index - 1], next[index]] = [next[index], next[index - 1]]
              onChange(next)
            }}><ArrowUp aria-hidden="true" /></Button>
            <Button type="button" size="icon" variant="outline" aria-label={`下移${String(entry)}`} disabled={disabled || index === values.length - 1} onClick={() => {
              const next = [...values]
              ;[next[index + 1], next[index]] = [next[index], next[index + 1]]
              onChange(next)
            }}><ArrowDown aria-hidden="true" /></Button>
            <Button type="button" size="icon" variant="ghost" aria-label={`移除${String(entry)}`} disabled={disabled || values.length <= minItems} onClick={() => onChange(updateArrayItem(values, index, UNSET))}><X aria-hidden="true" /></Button>
          </li>)}</ol>
          <div className="flex flex-wrap gap-2">{itemOptions.filter((option) => !values.some((entry) => Object.is(entry, option))).map((option) => <Button key={JSON.stringify(option)} type="button" size="sm" variant="outline" className="min-h-11" disabled={disabled || values.length >= maxItems} onClick={() => addOption(option)}><Plus aria-hidden="true" />{enumLabels(normalizedItem.schema)[String(option)] ?? String(option)}</Button>)}</div>
        </>}
        {error && <p id={`${id}-error`} className="text-sm text-destructive" role="alert">{error}</p>}
      </div>
    }

    if (widget === 'tags' || widget === 'ordered-list') {
      const appendDraft = () => {
        if (arrayDraft.trim() && values.length < maxItems) {
          const parsed: unknown = itemType === 'integer' || itemType === 'number' ? Number(arrayDraft) : arrayDraft.trim()
          if (typeof parsed !== 'number' || Number.isFinite(parsed)) onChange([...values, parsed])
          setArrayDraft('')
        }
      }
      return <div className="space-y-3">
        {stateSelector}
        <ol className="space-y-2">{values.map((entry, index) => <li key={`${String(entry)}-${index}`} className="flex items-center gap-2 rounded-lg border p-2">
          <span className="min-w-0 flex-1 break-all text-sm">{String(entry)}</span>
          {widget === 'ordered-list' && <>
            <Button type="button" size="icon" variant="outline" aria-label={`上移第 ${index + 1} 项`} disabled={disabled || index === 0} onClick={() => { const next = [...values]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; onChange(next) }}><ArrowUp aria-hidden="true" /></Button>
            <Button type="button" size="icon" variant="outline" aria-label={`下移第 ${index + 1} 项`} disabled={disabled || index === values.length - 1} onClick={() => { const next = [...values]; [next[index + 1], next[index]] = [next[index], next[index + 1]]; onChange(next) }}><ArrowDown aria-hidden="true" /></Button>
          </>}
          <Button type="button" size="icon" variant="ghost" aria-label={`删除第 ${index + 1} 项`} disabled={disabled || values.length <= minItems} onClick={() => onChange(updateArrayItem(values, index, UNSET))}><Trash2 aria-hidden="true" /></Button>
        </li>)}</ol>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Label className="sr-only" htmlFor={`${id}-new-tag`}>输入一个值</Label>
          <Input id={`${id}-new-tag`} value={arrayDraft} disabled={disabled || values.length >= maxItems} onChange={(event) => setArrayDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); appendDraft() } }} />
          <Button type="button" variant="outline" disabled={disabled || !arrayDraft.trim() || values.length >= maxItems} onClick={appendDraft}><Plus aria-hidden="true" />添加</Button>
        </div>
        {error && <p id={`${id}-error`} className="text-sm text-destructive" role="alert">{error}</p>}
      </div>
    }

    const appendValue = () => {
      if (arrayDraft === '' || values.length >= maxItems) return
      const parsed: unknown = itemType === 'integer' || itemType === 'number' ? Number(arrayDraft) : arrayDraft
      if (typeof parsed === 'number' && !Number.isFinite(parsed)) return
      onChange([...values, parsed])
      setArrayDraft('')
    }
    return <div className="space-y-3">
      {stateSelector}
      <ul className="space-y-2">{values.map((entry, index) => <li key={`${String(entry)}-${index}`} className="flex items-center gap-2 rounded-lg border p-2">
        <span className="min-w-0 flex-1 break-all text-sm">{String(entry)}</span>
        <Button type="button" size="icon" variant="outline" aria-label={`上移第 ${index + 1} 项`} disabled={disabled || index === 0} onClick={() => { const next = [...values]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; onChange(next) }}><ArrowUp aria-hidden="true" /></Button>
        <Button type="button" size="icon" variant="outline" aria-label={`下移第 ${index + 1} 项`} disabled={disabled || index === values.length - 1} onClick={() => { const next = [...values]; [next[index + 1], next[index]] = [next[index], next[index + 1]]; onChange(next) }}><ArrowDown aria-hidden="true" /></Button>
        <Button type="button" size="icon" variant="ghost" aria-label={`删除第 ${index + 1} 项`} disabled={disabled || values.length <= minItems} onClick={() => onChange(updateArrayItem(values, index, UNSET))}><Trash2 aria-hidden="true" /></Button>
      </li>)}</ul>
      <div className="flex flex-col gap-2 sm:flex-row"><Label className="sr-only" htmlFor={`${id}-array-value`}>添加列表项</Label><Input id={`${id}-array-value`} value={arrayDraft} disabled={disabled || values.length >= maxItems} onChange={(event) => setArrayDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); appendValue() } }} /><Button type="button" variant="outline" disabled={disabled || !arrayDraft || values.length >= maxItems} onClick={appendValue}><Plus aria-hidden="true" />添加</Button></div>
      {error && <p id={`${id}-error`} className="text-sm text-destructive" role="alert">{error}</p>}
    </div>
  }

  if (options.length > 0 || widget === 'select') {
    const labels = enumLabels(schema)
    const selected = value === undefined ? '' : JSON.stringify(value)
    return <div>{stateSelector}<Label className="sr-only" htmlFor={id}>{fieldLabel(schema, path.at(-1) ?? '参数')}</Label><select id={id} className="min-h-11 w-full rounded-lg border border-input bg-background px-3 text-sm disabled:opacity-50" value={selected} disabled={disabled} {...ariaError} onChange={(event) => onChange(event.target.value === '' ? UNSET : scalarEnumValue(event.target.value))}>
      <option value="">选择一个值</option>
      {options.map((option) => <option key={JSON.stringify(option)} value={JSON.stringify(option)}>{labels[String(option)] ?? String(option)}</option>)}
    </select>{error && <p id={`${id}-error`} className="mt-1 text-sm text-destructive" role="alert">{error}</p>}</div>
  }

  if (type === 'boolean' || widget === 'switch') {
    const selected = value === undefined ? '' : value === null ? 'null' : value ? 'true' : 'false'
    return <div>{stateSelector}<Label className="sr-only" htmlFor={id}>{fieldLabel(schema, path.at(-1) ?? '参数')}</Label><select id={id} className="min-h-11 w-full rounded-lg border border-input bg-background px-3 text-sm disabled:opacity-50" value={selected} disabled={disabled} {...ariaError} onChange={(event) => {
      if (event.target.value === '') onChange(UNSET)
      else if (event.target.value === 'null') onChange(null)
      else onChange(event.target.value === 'true')
    }}>
      <option value="">使用内核默认</option>{isNullable && <option value="null">明确留空</option>}<option value="true">开启</option><option value="false">关闭</option>
    </select>{error && <p id={`${id}-error`} className="mt-1 text-sm text-destructive" role="alert">{error}</p>}</div>
  }

  if (type === 'integer' || type === 'number') {
    const min = typeof schema.minimum === 'number' ? schema.minimum : undefined
    const max = typeof schema.maximum === 'number' ? schema.maximum : undefined
    const smallIntegerRange = type === 'integer' && min !== undefined && max !== undefined && max - min <= 12
    return <div className="space-y-2">{stateSelector}<div className="flex items-center gap-3">
      {smallIntegerRange && <input aria-label={`${fieldLabel(schema, path.at(-1) ?? '参数')}滑块`} className="min-h-11 flex-1 accent-primary" type="range" min={min} max={max} step={1} value={typeof value === 'number' ? value : min} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))} />}
      {widget === 'stepper' && <Button type="button" size="icon" variant="outline" aria-label={`减少${fieldLabel(schema, path.at(-1) ?? '参数')}`} disabled={disabled || (typeof value === 'number' && min !== undefined && value <= min)} onClick={() => onChange(Math.max(min ?? Number.NEGATIVE_INFINITY, (typeof value === 'number' ? value : 0) - (type === 'integer' ? 1 : 0.05)))}><span aria-hidden="true">−</span></Button>}
      <Label className="sr-only" htmlFor={id}>{fieldLabel(schema, path.at(-1) ?? '参数')}</Label><Input id={id} className="w-32" type="number" min={min} max={max} step={type === 'integer' ? 1 : 0.05} value={typeof value === 'number' ? value : ''} disabled={disabled} {...ariaError} onChange={(event) => {
        if (event.target.value === '') onChange(UNSET)
        else {
          const next = Number(event.target.value)
          if (Number.isFinite(next)) onChange(next)
        }
      }} />
      {widget === 'stepper' && <Button type="button" size="icon" variant="outline" aria-label={`增加${fieldLabel(schema, path.at(-1) ?? '参数')}`} disabled={disabled || (typeof value === 'number' && max !== undefined && value >= max)} onClick={() => onChange(Math.min(max ?? Number.POSITIVE_INFINITY, (typeof value === 'number' ? value : 0) + (type === 'integer' ? 1 : 0.05)))}><Plus aria-hidden="true" /></Button>}
      {smallIntegerRange && <span className="min-w-8 text-sm tabular-nums">{typeof value === 'number' ? value : min}</span>}
    </div>{error && <p id={`${id}-error`} className="text-sm text-destructive" role="alert">{error}</p>}</div>
  }

  if (type === 'string') {
    const multiline = widget === 'textarea' || (typeof schema.maxLength === 'number' && schema.maxLength > 120)
    const Element = multiline ? 'textarea' : Input
    return <div>{stateSelector}<Label className="sr-only" htmlFor={id}>{fieldLabel(schema, path.at(-1) ?? '参数')}</Label><Element id={id} className={multiline ? 'min-h-24 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50' : undefined} value={typeof value === 'string' ? value : ''} disabled={disabled} placeholder={textValue(schema.examples && Array.isArray(schema.examples) ? schema.examples[0] : undefined)} {...ariaError} onChange={(event) => onChange(event.target.value === '' ? UNSET : event.target.value)} />{error && <p id={`${id}-error`} className="mt-1 text-sm text-destructive" role="alert">{error}</p>}</div>
  }

  return <div>{stateSelector}<Label className="sr-only" htmlFor={id}>{fieldLabel(schema, path.at(-1) ?? '参数')}</Label><Input id={id} value={value === undefined ? '' : String(value)} disabled={disabled} {...ariaError} onChange={(event) => onChange(event.target.value)} />{error && <p id={`${id}-error`} className="mt-1 text-sm text-destructive" role="alert">{error}</p>}</div>
}

interface FieldEditorProps {
  name: string
  schema: JsonSchema
  root: JsonSchema
  value: unknown
  path: string[]
  formValues: Record<string, unknown>
  disabled: boolean
  immutable?: boolean
  onChange: (next: unknown | Unset) => void
  items?: ItemCatalogEntry[]
  itemCatalogLoading?: boolean
  itemCatalogError?: string | null
  error?: string
}

function FieldEditor({ name, schema: inputSchema, root, value, path, formValues, disabled, immutable = false, onChange, items, itemCatalogLoading, itemCatalogError, error }: FieldEditorProps) {
  const normalized = normalizeSchema(inputSchema, root)
  const schema = normalized.schema
  const label = fieldLabel(schema, name)
  const id = errorId(path)
  const dependsOn = isObject(schema['x-depends-on']) ? schema['x-depends-on'] as Record<string, unknown> : null
  const dependencyFailed = dependsOn !== null && Object.entries(dependsOn).some(([dependency, expected]) => !Object.is(formValues[dependency], expected))
  return <fieldset className="min-w-0 space-y-2" disabled={disabled || immutable || dependencyFailed}>
    <div className="flex items-start justify-between gap-2">
      <Label htmlFor={id} className="leading-5">{label}{schema['x-risk'] !== undefined && <span className="ml-2 inline-flex items-center gap-1 text-warning"><AlertTriangle className="size-4" aria-hidden="true" /><span className="text-xs">会消耗资源</span></span>}</Label>
      {dependencyFailed && <Badge variant="secondary" className="max-w-48 whitespace-normal text-left text-xs">先满足：{Object.entries(dependsOn ?? {}).map(([key, expected]) => `${key} = ${String(expected)}`).join(' 且 ')}</Badge>}
    </div>
    {immutable && <p className="text-xs text-muted-foreground">此参数在运行中的流水线里不可修改。</p>}
    {dependencyFailed && <p className="text-xs text-muted-foreground">该参数已禁用；满足依赖条件后即可编辑。</p>}
    {typeof schema.description === 'string' && <p className="text-xs leading-5 text-muted-foreground">{schema.description}</p>}
    <ValueEditor schema={schema} root={root} value={value} nullable={normalized.nullable} disabled={disabled || immutable || dependencyFailed} path={path} onChange={onChange} items={items} itemCatalogLoading={itemCatalogLoading} itemCatalogError={itemCatalogError} error={error} />
  </fieldset>
}

export function DynamicTaskForm({ taskType, value, onChange, disabled = false, errors = {}, runtimeEditing = false }: DynamicTaskFormProps) {
  const itemCatalog = useQuery({
    queryKey: itemCatalogKey,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/resources/items')
      if (error) throw error
      return (data ?? []) as ItemCatalogEntry[]
    },
    enabled: Object.values(schemaProperties(taskType.schema)).some((field) => field['x-widget'] === 'item-count-map'),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const properties = schemaProperties(taskType.schema)
  const fields = Object.entries(properties).filter(([name]) => name !== 'name')
  const groups = taskType.groups.length > 0 ? taskType.groups : ['基础']
  const ungrouped = fields.filter(([, schema]) => typeof schema['x-group'] !== 'string')
  const finalGroups = ungrouped.length > 0 && !groups.includes('基础') ? [...groups, '基础'] : groups
  const errorText = itemCatalog.error instanceof Error ? itemCatalog.error.message : itemCatalog.error ? '目录请求失败' : null

  return <div className="space-y-4">
    {taskType.description && <p className="text-sm text-muted-foreground">{taskType.description}</p>}
    {finalGroups.map((group) => {
      const groupFields = fields.filter(([, schema]) => textValue(schema['x-group'], '基础') === group)
      if (groupFields.length === 0) return null
      return <details key={group} open className="group rounded-xl border bg-card">
        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-4 py-3 font-medium [&::-webkit-details-marker]:hidden">
          {group}<ChevronDown className="size-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
        </summary>
        <div className="grid gap-4 border-t p-4 sm:grid-cols-2">{groupFields.map(([name, schema]) => <FieldEditor key={name} name={name} schema={schema} root={taskType.schema} value={value[name]} path={[taskType.name, name]} formValues={value} disabled={disabled} immutable={runtimeEditing && taskType.runtime_immutable.includes(name)} onChange={(next) => onChange(setObjectValue(value, [name], next))} items={itemCatalog.data} itemCatalogLoading={itemCatalog.isLoading} itemCatalogError={errorText} error={errors[name]} />)}</div>
      </details>
    })}
  </div>
}

interface TaskDraft {
  id: string
  name: string
  params: Record<string, unknown>
}

function randomId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}

function serializeTask(task: TaskDraft): Record<string, unknown> {
  return { name: task.name, ...task.params }
}

function unwrapHttpError(value: unknown): Record<string, unknown> {
  if (isObject(value) && isObject(value.error)) return value.error
  if (isObject(value)) return value
  return { message: typeof value === 'string' ? value : '请求失败，请稍后重试' }
}

function errorMessage(value: unknown): string {
  const apiError = toApiError(value)
  if (apiError.status === 403) return '请求被策略拒绝（403）。若操作本应允许，请检查代理是否剥离了 X-Token 鉴权头，并重新登录。'
  const source = unwrapHttpError(value)
  const nested = isObject(source.error) ? source.error : source
  const message = nested.message ?? source.message
  return typeof message === 'string' ? message : '请求失败，请稍后重试'
}

interface FieldIssue {
  taskIndex?: number
  field: string
  message: string
}

function collectFieldIssues(body: unknown, taskCount: number): { issues: FieldIssue[]; message: string } {
  const issues: FieldIssue[] = []
  const payload = body instanceof ApiError ? body.body : body
  const error = unwrapHttpError(payload)
  const details = isObject(error.details) ? error.details : isObject(error.context) ? error.context : error
  const visit = (candidate: unknown, location: unknown[] = []) => {
    if (Array.isArray(candidate)) {
      for (const entry of candidate) visit(entry, location)
      return
    }
    if (!isObject(candidate)) return
    if (Array.isArray(candidate.loc)) {
      const loc = candidate.loc
      const tasksAt = loc.indexOf('tasks')
      const possibleIndex = tasksAt >= 0 ? loc[tasksAt + 1] : undefined
      const field = [...loc].reverse().find((part) => typeof part === 'string' && !['body', 'tasks', 'task'].includes(part))
      if (typeof field === 'string') issues.push({ taskIndex: typeof possibleIndex === 'number' ? possibleIndex : undefined, field, message: textValue(candidate.msg, errorMessage(body)) })
    }
    if (typeof candidate.field === 'string') {
      let taskIndex: number | undefined
      for (let index = 0; index < taskCount; index += 1) {
        if (candidate.task_index === index || candidate.taskIndex === index || candidate.index === index) taskIndex = index
      }
      issues.push({ taskIndex, field: candidate.field, message: textValue(candidate.message, errorMessage(body)) })
    }
    for (const key of ['errors', 'fields', 'detail', 'details', 'context']) {
      if (candidate[key] !== undefined && candidate[key] !== candidate) visit(candidate[key], [...location, key])
    }
  }
  visit(details)
  if (details !== error) visit(error.detail)
  const unique = issues.filter((issue, index) => issues.findIndex((candidate) => candidate.field === issue.field && candidate.taskIndex === issue.taskIndex && candidate.message === issue.message) === index)
  return { issues: unique, message: errorMessage(body) }
}

function mapFieldIssues(issues: FieldIssue[], tasks: TaskDraft[], taskTypes: TaskTypeSchema[]): Record<string, Record<string, string>> {
  const result: Record<string, Record<string, string>> = {}
  for (const issue of issues) {
    const candidates = issue.taskIndex === undefined ? tasks : tasks.slice(issue.taskIndex, issue.taskIndex + 1)
    const matched = candidates.find((task) => Object.keys(schemaProperties(taskTypes.find((type) => type.name === task.name)?.schema ?? {})).some((field) => field === issue.field))
    if (!matched) continue
    result[matched.id] ??= {}
    result[matched.id][issue.field] = issue.message
  }
  return result
}

function nonZeroRisk(value: unknown): boolean {
  if (value === undefined || value === null || value === false || value === 0 || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  if (isObject(value)) return Object.keys(value).length > 0
  return true
}

function riskSummary(tasks: TaskDraft[], types: TaskTypeSchema[]): string[] {
  const lines: string[] = []
  for (const task of tasks) {
    const type = types.find((candidate) => candidate.name === task.name)
    if (!type) continue
    for (const [name, schema] of Object.entries(schemaProperties(type.schema))) {
      if (schema['x-risk'] !== undefined && nonZeroRisk(task.params[name])) lines.push(`${type.label} · ${fieldLabel(schema, name)}：${JSON.stringify(task.params[name])}`)
    }
  }
  return lines
}

function sourceLabel(source: unknown): string {
  if (source === 'manual') return '手动'
  if (source === 'agent') return 'Agent'
  if (source === 'scheduled') return '定时'
  return typeof source === 'string' ? source : '未知来源'
}

function priorityLabel(priority: unknown): string {
  if (priority === 0) return '0 · 最高'
  if (priority === 1) return '1 · 普通'
  if (priority === 2) return '2 · 定时'
  return `优先级 ${String(priority ?? '未知')}`
}

function pipelineId(row: PipelineRow): string {
  return textValue(row.id, textValue(row.pipeline_id))
}

function pipelineTitle(row: PipelineRow): string {
  return textValue(row.title, textValue(row.name, '')) || pipelineId(row) || '未命名流水线'
}

function timeLabel(value: unknown): string {
  if (typeof value !== 'string') return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function screenshotImageSource(screenshot: PipelineScreenshotRecord): { kind: 'thumb'; sha256: string } | { kind: 'record'; id: string } {
  const basename = screenshot.path.split('/').at(-1) ?? ''
  const match = /^([0-9a-f]{64})(?:\.[a-zA-Z0-9.]+)?$/.exec(basename)
  if (match) return { kind: 'thumb', sha256: match[1] }
  return { kind: 'record', id: screenshot.id }
}

function ScreenshotThumbnail({ screenshot, index }: { screenshot: PipelineScreenshotRecord; index: number }) {
  const source = screenshotImageSource(screenshot)
  const figureRef = useRef<HTMLElement | null>(null)
  const [nearViewport, setNearViewport] = useState(typeof IntersectionObserver === 'undefined')
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  useEffect(() => {
    const node = figureRef.current
    if (!node || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setNearViewport(true)
        observer.disconnect()
      }
    }, { rootMargin: '180px' })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  const imageQuery = useQuery({
    queryKey: [...keys.pipelines.detail(screenshot.pipeline_id), 'screenshot-image', screenshot.id],
    queryFn: async () => {
      const result = source.kind === 'thumb'
        ? await api.GET('/api/images/{sha256}/thumb', { params: { path: { sha256: source.sha256 } }, parseAs: 'blob' })
        : await api.GET('/api/screenshots/{id}', { params: { path: { id: source.id } }, parseAs: 'blob' })
      if (result.error) throw result.error
      return result.data as unknown as Blob
    },
    enabled: screenshot.deleted_at === null && nearViewport,
    staleTime: Infinity,
    retry: false,
  })

  useEffect(() => {
    if (!imageQuery.data) return
    const url = URL.createObjectURL(imageQuery.data)
    setObjectUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [imageQuery.data])

  const alt = `流水线截图 ${index + 1}，${screenshot.trigger}，${screenshot.width}×${screenshot.height}`
  return <figure ref={figureRef} className="overflow-hidden rounded-lg border bg-muted/30">
    {screenshot.deleted_at !== null ? <div className="grid aspect-video place-items-center px-3 text-center text-sm text-muted-foreground">截图已过期清理</div>
      : imageQuery.isLoading ? <div className="grid aspect-video place-items-center text-sm text-muted-foreground" role="status">正在加载截图…</div>
        : imageQuery.error ? <div className="grid aspect-video place-items-center px-3 text-center text-sm text-destructive" role="alert">截图读取失败：{errorMessage(imageQuery.error)}</div>
          : objectUrl ? <img src={objectUrl} alt={alt} loading="lazy" width={screenshot.width || 320} height={screenshot.height || 180} className="max-h-64 w-full object-contain" />
            : <div className="grid aspect-video place-items-center text-sm text-muted-foreground">截图预览暂不可用</div>}
    <figcaption className="space-y-1 border-t p-2 text-xs text-muted-foreground"><span className="block">{screenshot.trigger} · {screenshot.format.toUpperCase()} · {screenshot.width}×{screenshot.height}</span><span className="block">{timeLabel(screenshot.created_at)} · {Math.max(1, Math.round(screenshot.size_bytes / 1024))} KB</span></figcaption>
  </figure>
}

function statusLabel(value: unknown): string {
  const statuses: Record<string, string> = {
    PENDING: '等待中', RUNNING: '运行中', COMPLETED: '已完成', FAILED: '失败', CANCELLED: '已取消', SKIPPED: '已跳过',
    pending: '等待中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', skipped: '已跳过',
  }
  return typeof value === 'string' ? statuses[value] ?? value : '状态未知'
}

export type TasksView = 'create' | 'queue' | 'history'
export interface TasksFeatureProps {
  view?: TasksView
  routePipelineId?: string | null
  onViewChange?: (view: TasksView) => void
  onPipelineChange?: (pipelineId: string | null) => void
}

export default function TasksFeature({ view, routePipelineId, onViewChange, onPipelineChange }: TasksFeatureProps = {}) {
  const queryClient = useQueryClient()
  const [localView, setLocalView] = useState<TasksView>('create')
  const activeView = view ?? localView
  const changeView = (next: TasksView) => {
    if (onViewChange) onViewChange(next)
    else setLocalView(next)
  }
  const [drafts, setDrafts] = useState<TaskDraft[]>([])
  const [newType, setNewType] = useState('')
  const [title, setTitle] = useState('')
  const [formErrors, setFormErrors] = useState<Record<string, Record<string, string>>>({})
  const [submitStatus, setSubmitStatus] = useState('')
  const [queueStatus, setQueueStatus] = useState('')
  const [historyStatus, setHistoryStatus] = useState('')
  const [historyPage, setHistoryPage] = useState(1)
  const [localPipelineId, setLocalPipelineId] = useState<string | null>(null)
  const selectedPipelineId = routePipelineId === undefined ? localPipelineId : routePipelineId
  const changePipeline = (next: string | null) => {
    if (onPipelineChange) onPipelineChange(next)
    else setLocalPipelineId(next)
  }
  const [submissionIdempotencyKey, setSubmissionIdempotencyKey] = useState<string | null>(null)

  const typeQuery = useQuery({
    queryKey: taskTypesKey,
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/tasks/types')
      if (error) throw toApiError(error, response)
      return data as TaskTypesEnvelope
    },
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
  const taskTypes = typeQuery.data?.items ?? []
  const selectedType = taskTypes.find((type) => type.name === newType) ?? taskTypes[0]

  const queueQuery = useQuery({
    queryKey: keys.queue.all(),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/queue')
      if (error) throw toApiError(error, response)
      return data as QueueSnapshot
    },
    refetchInterval: activeView === 'queue' ? 5000 : false,
    retry: false,
  })

  const historyQuery = useQuery({
    queryKey: keys.pipelines.list({ page: historyPage, size: historyPageSize }),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/pipelines', { params: { query: { page: historyPage, size: historyPageSize } } })
      if (error) throw toApiError(error, response)
      return data as PageEnvelope<PipelineRow>
    },
    enabled: activeView === 'history',
    retry: false,
  })

  const selectedPipelineQuery = useQuery({
    queryKey: keys.pipelines.detail(selectedPipelineId ?? ''),
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/pipelines/{pipeline_id}', { params: { path: { pipeline_id: selectedPipelineId ?? '' } } })
      if (error) throw toApiError(error, response)
      return data as PipelineRow
    },
    enabled: selectedPipelineId !== null,
    retry: false,
  })
  const selectedPipelineLogsQuery = useQuery({
    queryKey: [...keys.pipelines.detail(selectedPipelineId ?? ''), 'logs'],
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/pipelines/{pipeline_id}/logs', {
        params: { path: { pipeline_id: selectedPipelineId ?? '' }, query: { order: 'asc', size: 100 } },
      })
      if (error) throw toApiError(error, response)
      return data as PageEnvelope<PipelineLogRecord>
    },
    enabled: selectedPipelineId !== null,
    retry: false,
  })
  const selectedPipelineScreenshotsQuery = useQuery({
    queryKey: [...keys.pipelines.detail(selectedPipelineId ?? ''), 'screenshots'],
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/pipelines/{pipeline_id}/screenshots', {
        params: { path: { pipeline_id: selectedPipelineId ?? '' } },
      })
      if (error) throw toApiError(error, response)
      return data as { items: PipelineScreenshotRecord[]; total: number }
    },
    enabled: selectedPipelineId !== null,
    retry: false,
  })

  const queueMutation = useMutation({
    mutationFn: async (action: { kind: 'pause' | 'resume' | 'clear' | 'cancel'; pipelineId?: string }) => {
      if (action.kind === 'pause') {
        const result = await api.POST('/api/queue/pause')
        if (result.error) throw toApiError(result.error, result.response)
      } else if (action.kind === 'resume') {
        const result = await api.POST('/api/queue/resume')
        if (result.error) throw toApiError(result.error, result.response)
      } else if (action.kind === 'clear') {
        const result = await api.DELETE('/api/queue')
        if (result.error) throw toApiError(result.error, result.response)
      } else if (action.pipelineId) {
        const result = await api.DELETE('/api/pipelines/{pipeline_id}', { params: { path: { pipeline_id: action.pipelineId } } })
        if (result.error) throw toApiError(result.error, result.response)
      }
    },
    onSuccess: async (_data, action) => {
      const messages = { pause: '队列已暂停；当前流水线会继续运行。', resume: '队列已恢复。', clear: '已取消所有待执行条目。', cancel: '已发送取消请求。' }
      setQueueStatus(messages[action.kind])
      await Promise.all([queryClient.invalidateQueries({ queryKey: keys.queue.all() }), queryClient.invalidateQueries({ queryKey: keys.pipelines.lists() })])
    },
    onError: (error) => setQueueStatus(`操作失败：${errorMessage(error)}`),
  })

  const historyAction = useMutation({
    mutationFn: async (action: { kind: 'cancel' | 'retry'; pipelineId: string }) => {
      if (action.kind === 'cancel') {
        const result = await api.DELETE('/api/pipelines/{pipeline_id}', { params: { path: { pipeline_id: action.pipelineId } } })
        if (result.error) throw toApiError(result.error, result.response)
        return result.data
      }
      const result = await api.POST('/api/pipelines/{pipeline_id}/retry', { params: { path: { pipeline_id: action.pipelineId } }, body: {} })
        if (result.error) throw toApiError(result.error, result.response)
      return result.data
    },
    onSuccess: async (_data, action) => {
      setHistoryStatus(action.kind === 'retry' ? '已提交重试流水线。' : '已发送取消请求。')
      await Promise.all([queryClient.invalidateQueries({ queryKey: keys.pipelines.lists() }), queryClient.invalidateQueries({ queryKey: keys.queue.all() }), queryClient.invalidateQueries({ queryKey: keys.pipelines.detail(action.pipelineId) })])
    },
    onError: (error) => setHistoryStatus(`操作失败：${errorMessage(error)}`),
  })

  const stopAndPromote = async (target: PipelineRow) => {
    const running = queueQuery.data?.running
    const runningId = running ? pipelineId(running) : ''
    const targetId = pipelineId(target)
    if (!runningId || !targetId) {
      setQueueStatus('无法识别当前或目标流水线 ID，未执行操作。')
      return
    }
    const confirmed = window.confirm(`即将取消当前流水线「${pipelineTitle(running!)}」，并将「${pipelineTitle(target)}」调至手动优先级 0。队列之后仍按已显示的 priority、创建时间顺序调度；更早的 priority=0 条目仍可能先运行，因此不能保证目标紧接当前项执行。是否继续？`)
    if (!confirmed) return
    setQueueStatus('正在提升目标并请求取消当前流水线…')
    try {
      const promoted = await api.PATCH('/api/queue/{pipeline_id}', { params: { path: { pipeline_id: targetId } }, body: { priority: 0 } })
      if (promoted.error) throw toApiError(promoted.error, promoted.response)
      const cancelled = await api.DELETE('/api/pipelines/{pipeline_id}', { params: { path: { pipeline_id: runningId } } })
      if (cancelled.error) throw toApiError(cancelled.error, cancelled.response)
      setQueueStatus('目标已调至优先级 0，当前流水线已请求取消；队列按显示顺序继续调度。')
      await Promise.all([queryClient.invalidateQueries({ queryKey: keys.queue.all() }), queryClient.invalidateQueries({ queryKey: keys.pipelines.lists() })])
    } catch (error) {
      setQueueStatus(`操作未完整完成：${errorMessage(error)}。若优先级调整已成功，目标仍会留在队列中。`)
      await queryClient.invalidateQueries({ queryKey: keys.queue.all() })
    }
  }

  const submitMutation = useMutation({
    mutationFn: async (idempotencyKey: string) => {
      const serialized = drafts.map(serializeTask)
      const validation = await api.POST('/api/tasks/validate', { body: { tasks: serialized } as never })
      if (validation.error) throw toApiError(validation.error, validation.response)
      const submitted = await api.POST('/api/pipelines', {
        body: { tasks: serialized, ...(title.trim() ? { title: title.trim() } : {}) } as never,
        headers: { 'Idempotency-Key': idempotencyKey },
      })
      if (submitted.error) throw toApiError(submitted.error, submitted.response)
      return submitted.data
    },
    onSuccess: async () => {
      setFormErrors({})
      setSubmitStatus('流水线已加入队列。')
      setSubmissionIdempotencyKey(null)
      setDrafts([])
      setTitle('')
      await Promise.all([queryClient.invalidateQueries({ queryKey: keys.queue.all() }), queryClient.invalidateQueries({ queryKey: keys.pipelines.lists() })])
    },
    onError: async (error) => {
      const { issues, message } = collectFieldIssues(error, drafts.length)
      const mapped = mapFieldIssues(issues, drafts, taskTypes)
      setFormErrors(mapped)
      if (error instanceof ApiError && error.status === 504) {
        setSubmitStatus('服务端未在预期时间内响应（504），这条流水线可能已经入队。正在确认状态；再次点击会使用同一请求编号。')
        try {
          const { data, error: currentError, response } = await api.GET('/api/pipelines/current')
          if (!currentError) queryClient.setQueryData(keys.pipelines.current(), data)
          else setSubmitStatus(`服务端未在预期时间内响应（504），且当前流水线查询失败：${toApiError(currentError, response).message}。再次点击会使用同一请求编号。`)
        } catch (statusError) {
          setSubmitStatus(`服务端未在预期时间内响应（504），当前状态查询失败：${errorMessage(statusError)}。再次点击会使用同一请求编号。`)
        }
        await queryClient.invalidateQueries({ queryKey: keys.queue.all() })
      } else {
        setSubmitStatus(issues.length > 0 ? `校验未通过：${message}。错误已标到对应字段。` : `提交失败：${message}`)
      }
    },
  })

  const addTask = () => {
    if (!selectedType) return
    setSubmissionIdempotencyKey(null)
    setDrafts((current) => [...current, { id: randomId(), name: selectedType.name, params: {} }])
    setNewType(selectedType.name)
    setSubmitStatus('')
  }

  const moveTask = (index: number, offset: -1 | 1) => {
    setSubmissionIdempotencyKey(null)
    setDrafts((current) => {
      const target = index + offset
      if (target < 0 || target >= current.length) return current
      const next = [...current]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  const submitPipeline = () => {
    if (drafts.length === 0) {
      setSubmitStatus('请先添加至少一个任务。')
      return
    }
    const risks = riskSummary(drafts, taskTypes)
    if (risks.length > 0 && !window.confirm(`本流水线包含可能消耗资源的参数：\n\n${risks.join('\n')}\n\n确认后先由服务端校验，再提交入队。继续吗？`)) return
    setFormErrors({})
    setSubmitStatus('正在校验任务参数…')
    const idempotencyKey = submissionIdempotencyKey ?? randomId()
    setSubmissionIdempotencyKey(idempotencyKey)
    submitMutation.mutate(idempotencyKey)
  }

  const queue = queueQuery.data
  const pending = Array.isArray(queue?.pending) ? queue.pending : []
  const history = historyQuery.data
  const historyNextAvailable = history ? history.page * history.size < history.total : false
  const detail = selectedPipelineQuery.data
  const historyItems = history?.items ?? []
  const deepLinkedPipelineIsOutsidePage = Boolean(
    selectedPipelineId && detail && pipelineId(detail) === selectedPipelineId
    && !historyItems.some((row) => pipelineId(row) === selectedPipelineId),
  )
  const visibleHistoryItems = deepLinkedPipelineIsOutsidePage && detail
    ? [detail, ...historyItems]
    : historyItems

  return <main className="mx-auto w-full max-w-5xl space-y-5 px-4 pb-24 pt-5 sm:px-6">
    <header className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">任务中心</p>
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">任务与流水线</h1>
      <p className="max-w-2xl text-sm text-muted-foreground">按服务端任务 schema 配置参数、查看实时队列和执行历史。</p>
    </header>

    <div className="grid grid-cols-3 gap-2 rounded-xl border bg-muted/40 p-1" role="tablist" aria-label="任务视图">
      {([['create', '创建'], ['queue', '队列'], ['history', '历史']] as const).map(([nextView, label]) => <Button key={nextView} type="button" variant={activeView === nextView ? 'default' : 'ghost'} role="tab" aria-selected={activeView === nextView} onClick={() => changeView(nextView)}>{label}</Button>)}
    </div>

    {activeView === 'create' && <section role="tabpanel" aria-label="创建任务" className="space-y-4">
      <Card>
        <CardHeader><CardTitle>创建流水线</CardTitle><CardDescription>按顺序执行任务。只会提交你实际修改过的参数。</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          {typeQuery.isLoading && <p role="status" className="text-sm text-muted-foreground">正在读取服务端任务 schema…</p>}
          {typeQuery.error && <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive" role="alert">无法读取任务类型：{errorMessage(typeQuery.error)} <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={() => void typeQuery.refetch()}>重试</Button></div>}
          {!typeQuery.isLoading && !typeQuery.error && taskTypes.length === 0 && <p role="status" className="text-sm text-muted-foreground">服务端暂未提供任务类型。</p>}
          {taskTypes.length > 0 && <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="flex-1 space-y-2"><Label htmlFor="new-task-type">任务类型</Label><select id="new-task-type" className="min-h-11 w-full rounded-lg border border-input bg-background px-3 text-sm" value={selectedType?.name ?? ''} onChange={(event) => setNewType(event.target.value)}>{taskTypes.map((type) => <option key={type.name} value={type.name}>{type.label} · {type.name}</option>)}</select></div>
            <Button type="button" variant="outline" onClick={addTask} disabled={!selectedType}><Plus aria-hidden="true" />添加任务</Button>
          </div>}
          <div className="space-y-2"><Label htmlFor="pipeline-title">流水线名称（可选）</Label><Input id="pipeline-title" maxLength={64} value={title} onChange={(event) => { setSubmissionIdempotencyKey(null); setTitle(event.target.value) }} placeholder="例如：每日基建与刷图" /></div>
          {drafts.length === 0 ? <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">尚未添加任务。选择服务端提供的类型后添加。</div> : <ol className="space-y-3">
            {drafts.map((draft, index) => {
              const taskType = taskTypes.find((type) => type.name === draft.name)
              if (!taskType) return null
              return <li key={draft.id} className="rounded-xl border bg-card">
                <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2 sm:px-4">
                  <span className="grid size-8 place-items-center rounded-full bg-muted text-sm font-semibold" aria-label={`第 ${index + 1} 个任务`}>{index + 1}</span>
                  <div className="min-w-0 flex-1"><p className="font-medium">{taskType.label}</p><p className="text-xs text-muted-foreground">{taskType.name}</p></div>
                  <Button type="button" size="icon" variant="outline" aria-label={`上移${taskType.label}`} disabled={index === 0 || submitMutation.isPending} onClick={() => moveTask(index, -1)}><ArrowUp aria-hidden="true" /></Button>
                  <Button type="button" size="icon" variant="outline" aria-label={`下移${taskType.label}`} disabled={index === drafts.length - 1 || submitMutation.isPending} onClick={() => moveTask(index, 1)}><ArrowDown aria-hidden="true" /></Button>
                  <Button type="button" size="icon" variant="ghost" aria-label={`删除${taskType.label}`} disabled={submitMutation.isPending} onClick={() => { setSubmissionIdempotencyKey(null); setDrafts((current) => current.filter((item) => item.id !== draft.id)) }}><Trash2 aria-hidden="true" /></Button>
                </div>
                <div className="p-3 sm:p-4"><DynamicTaskForm taskType={taskType} value={draft.params} errors={formErrors[draft.id]} disabled={submitMutation.isPending} onChange={(params) => {
                  setSubmissionIdempotencyKey(null)
                  setDrafts((current) => current.map((item) => item.id === draft.id ? { ...item, params } : item))
                  setFormErrors((current) => { const next = { ...current }; delete next[draft.id]; return next })
                }} /></div>
              </li>
            })}
          </ol>}
          <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted-foreground">任务数 {drafts.length} / 32</p>
            <Button type="button" onClick={submitPipeline} disabled={drafts.length === 0 || drafts.length > 32 || submitMutation.isPending || typeQuery.isLoading}>
          {submitMutation.isPending ? <><LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" />正在校验并提交…</> : '校验并加入队列'}
            </Button>
          </div>
          {submitStatus && <p className={`text-sm ${submitMutation.isError ? 'text-destructive' : 'text-muted-foreground'}`} role={submitMutation.isError ? 'alert' : 'status'}>{submitStatus}</p>}
        </CardContent>
      </Card>
    </section>}

    {activeView === 'queue' && <section role="tabpanel" aria-label="执行队列" className="space-y-4">
      <Card>
        <CardHeader className="flex-row items-start justify-between"><div><CardTitle>执行队列</CardTitle><CardDescription>待执行顺序来自服务端；按优先级和创建时间调度。</CardDescription></div><Button type="button" size="icon" variant="outline" aria-label="刷新队列" disabled={queueQuery.isFetching} onClick={() => void queueQuery.refetch()}><RefreshCw className={queueQuery.isFetching ? 'animate-spin motion-reduce:animate-none' : ''} aria-hidden="true" /></Button></CardHeader>
        <CardContent className="space-y-4">
          {queueQuery.error && <p className="text-sm text-destructive" role="alert">无法读取队列：{errorMessage(queueQuery.error)}</p>}
          <div className="flex flex-wrap gap-2">
            <Badge variant={queue?.paused ? 'warning' : 'positive'}>{queue?.paused ? '已暂停' : '运行中'}</Badge>
            <span className="self-center text-sm text-muted-foreground">当前 {queue?.running ? pipelineTitle(queue.running) : '空闲'} · 待执行 {pending.length} 条</span>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" disabled={queueMutation.isPending} onClick={() => queueMutation.mutate({ kind: queue?.paused ? 'resume' : 'pause' })}>{queue?.paused ? '恢复队列' : '暂停队列'}</Button>
            <Button type="button" variant="destructive" disabled={pending.length === 0 || queueMutation.isPending} onClick={() => {
              if (window.confirm(`将取消全部 ${pending.length} 条待执行流水线。当前运行中的流水线不会受影响。继续吗？`)) queueMutation.mutate({ kind: 'clear' })
            }}>清空待执行条目</Button>
          </div>
          {queueStatus && <p role={queueMutation.isError ? 'alert' : 'status'} className={`text-sm ${queueMutation.isError ? 'text-destructive' : 'text-muted-foreground'}`}>{queueStatus}</p>}
          {queueQuery.isLoading ? <p role="status" className="text-sm text-muted-foreground">正在读取队列…</p> : pending.length === 0 ? <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">当前没有待执行条目。</div> : <ol className="space-y-3" aria-label="服务端待执行顺序">
            {pending.map((row, index) => <li key={pipelineId(row) || index} className="rounded-xl border p-4">
              <div className="flex flex-wrap items-start gap-3">
                <span className="grid size-8 shrink-0 place-items-center rounded-full bg-muted text-sm font-semibold">{index + 1}</span>
                <div className="min-w-0 flex-1"><p className="break-words font-medium">{pipelineTitle(row)}</p><p className="mt-1 break-all text-xs text-muted-foreground">{pipelineId(row)}</p><div className="mt-2 flex flex-wrap gap-2"><Badge variant="outline">来源：{sourceLabel(row.source)}</Badge><Badge variant="secondary">优先级：{priorityLabel(row.priority)}</Badge><Badge variant="outline">{statusLabel(row.status ?? 'PENDING')}</Badge></div></div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 border-t pt-3">
                <Button type="button" size="sm" variant="outline" className="min-h-11" disabled={queueMutation.isPending || !pipelineId(row)} onClick={() => {
                  if (window.confirm(`取消待执行流水线「${pipelineTitle(row)}」？`)) queueMutation.mutate({ kind: 'cancel', pipelineId: pipelineId(row) })
                }}>取消此条</Button>
                <Button type="button" size="sm" variant="destructive" className="min-h-11" disabled={queueMutation.isPending || !queue?.running || !pipelineId(row)} onClick={() => void stopAndPromote(row)}>停止当前并执行此条</Button>
              </div>
            </li>)}
          </ol>}
        </CardContent>
      </Card>
      {queue?.running && <Card><CardHeader><CardTitle>当前流水线</CardTitle><CardDescription>停止操作会取消当前运行中的流水线。</CardDescription></CardHeader><CardContent><p className="font-medium">{pipelineTitle(queue.running)}</p><p className="mt-1 text-xs text-muted-foreground">{pipelineId(queue.running)} · {statusLabel(queue.running.status ?? 'RUNNING')}</p><div className="mt-3 flex flex-wrap gap-2"><Badge variant="outline">来源：{sourceLabel(queue.running.source)}</Badge><Badge variant="secondary">优先级：{priorityLabel(queue.running.priority)}</Badge></div></CardContent></Card>}
    </section>}

    {activeView === 'history' && <section role="tabpanel" aria-label="执行历史" className="space-y-4">
      <Card>
        <CardHeader className="flex-row items-start justify-between"><div><CardTitle>执行历史</CardTitle><CardDescription>按服务端 page、size、total 分页。</CardDescription></div><Button type="button" size="icon" variant="outline" aria-label="刷新历史" disabled={historyQuery.isFetching} onClick={() => void historyQuery.refetch()}><RefreshCw className={historyQuery.isFetching ? 'animate-spin motion-reduce:animate-none' : ''} aria-hidden="true" /></Button></CardHeader>
        <CardContent className="space-y-4">
          {historyQuery.error && <p className="text-sm text-destructive" role="alert">无法读取执行历史：{errorMessage(historyQuery.error)}</p>}
          {deepLinkedPipelineIsOutsidePage && <p role="status" className="text-sm text-muted-foreground">此深链目标不在当前分页；已将它固定显示在列表顶部。</p>}
          {historyStatus && <p role={historyAction.isError ? 'alert' : 'status'} className={`text-sm ${historyAction.isError ? 'text-destructive' : 'text-muted-foreground'}`}>{historyStatus}</p>}
          {historyQuery.isLoading ? <p role="status" className="text-sm text-muted-foreground">正在读取执行历史…</p> : visibleHistoryItems.length === 0 ? <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">暂无流水线历史。</div> : <ol className="space-y-3">
            {visibleHistoryItems.map((row) => {
              const id = pipelineId(row)
              const selected = selectedPipelineId === id
              return <li key={id} className="rounded-xl border">
                <button type="button" className="flex min-h-16 w-full flex-wrap items-center gap-3 p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-expanded={selected} onClick={() => {
                  changePipeline(selected ? null : id)
                  setHistoryStatus('')
                }}>
                  <span className="min-w-0 flex-1"><span className="block break-words font-medium">{pipelineTitle(row)}</span><span className="mt-1 block text-xs text-muted-foreground">{timeLabel(row.created_at)} · {row.task_count ?? 0} 个任务 · {sourceLabel(row.source)}</span></span>
                  <Badge variant={row.status === 'FAILED' || row.status === 'failed' ? 'destructive' : 'secondary'}>{statusLabel(row.status)}</Badge>
                  {selected ? <ChevronUp aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
                </button>
                {selected && <div className="space-y-4 border-t p-4">
                  {selectedPipelineQuery.isLoading && <p role="status" className="text-sm text-muted-foreground">正在读取流水线详情…</p>}
                  {selectedPipelineQuery.error && <p role="alert" className="text-sm text-destructive">详情读取失败：{errorMessage(selectedPipelineQuery.error)}</p>}
                  {detail && pipelineId(detail) === id && <>
                    <div className="flex flex-wrap gap-2"><Badge variant="outline">状态：{statusLabel(detail.status)}</Badge><Badge variant="outline">优先级：{priorityLabel(detail.priority)}</Badge><Badge variant="outline">来源：{sourceLabel(detail.source)}</Badge></div>
                    {detail.error?.message && <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{detail.error.message}</p>}
                    <div className="space-y-2">
                      <h3 className="text-sm font-semibold">任务结果</h3>
                      {(detail.tasks ?? []).length === 0 ? <p className="text-sm text-muted-foreground">服务端没有返回任务明细。</p> : <ol className="space-y-2">{detail.tasks?.map((task, taskIndex) => <li key={String(task.id ?? taskIndex)} className="rounded-lg border p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">{String(task.task_name ?? task.type_name ?? `任务 ${taskIndex + 1}`)}</span><Badge variant={task.status === 'FAILED' || task.status === 'failed' ? 'destructive' : 'secondary'}>{statusLabel(task.status)}</Badge></div>
                        <p className="mt-1 text-xs text-muted-foreground">耗时 {typeof task.duration_seconds === 'number' ? `${task.duration_seconds.toFixed(1)} 秒` : '未开始'}</p>
                        {isObject(task.error) && typeof task.error.message === 'string' && <p className="mt-2 text-sm text-destructive">{task.error.message}</p>}
                      </li>)}</ol>}
                    </div>
                    <div className="space-y-2 border-t pt-4">
                      <h3 className="text-sm font-semibold">关联流水线日志</h3>
                      {selectedPipelineLogsQuery.isLoading && <p role="status" className="text-sm text-muted-foreground">正在读取日志…</p>}
                      {selectedPipelineLogsQuery.error && <p role="alert" className="text-sm text-destructive">日志读取失败：{errorMessage(selectedPipelineLogsQuery.error)}</p>}
                      {selectedPipelineLogsQuery.data && selectedPipelineLogsQuery.data.items.length === 0 && <p className="text-sm text-muted-foreground">该流水线暂无日志。</p>}
                      {selectedPipelineLogsQuery.data && selectedPipelineLogsQuery.data.items.length > 0 && <ol className="max-h-80 space-y-2 overflow-y-auto rounded-lg border p-3">
                        {selectedPipelineLogsQuery.data.items.map((log) => <li key={log.id} className="space-y-1 border-b pb-2 last:border-b-0 last:pb-0">
                          <div className="flex flex-wrap items-center gap-2"><Badge variant={log.level === 'ERROR' || log.level === 'CRITICAL' ? 'destructive' : 'secondary'}>{log.level}</Badge><span className="text-xs text-muted-foreground">{sourceLabel(log.source)} · {timeLabel(new Date(log.ts * 1000).toISOString())}{log.logger ? ` · ${log.logger}` : ''}</span></div>
                          <p className="break-words whitespace-pre-wrap text-sm">{log.content}</p>
                        </li>)}
                      </ol>}
                      {selectedPipelineLogsQuery.data && selectedPipelineLogsQuery.data.total > selectedPipelineLogsQuery.data.size && <p className="text-xs text-muted-foreground">显示前 {selectedPipelineLogsQuery.data.size} 条，共 {selectedPipelineLogsQuery.data.total} 条日志。</p>}
                    </div>
                    <div className="space-y-2 border-t pt-4">
                      <h3 className="text-sm font-semibold">关联截图</h3>
                      {selectedPipelineScreenshotsQuery.isLoading && <p role="status" className="text-sm text-muted-foreground">正在读取截图…</p>}
                      {selectedPipelineScreenshotsQuery.error && <p role="alert" className="text-sm text-destructive">截图列表读取失败：{errorMessage(selectedPipelineScreenshotsQuery.error)}</p>}
                      {selectedPipelineScreenshotsQuery.data && selectedPipelineScreenshotsQuery.data.items.length === 0 && <p className="text-sm text-muted-foreground">该流水线暂无截图。</p>}
                      {selectedPipelineScreenshotsQuery.data && selectedPipelineScreenshotsQuery.data.items.length > 0 && <div className="grid gap-3 sm:grid-cols-2">
                        {selectedPipelineScreenshotsQuery.data.items.map((screenshot, screenshotIndex) => <ScreenshotThumbnail key={screenshot.id} screenshot={screenshot} index={screenshotIndex} />)}
                      </div>}
                    </div>
                    <div className="flex flex-wrap gap-2 border-t pt-3">
                      {(detail.status === 'PENDING' || detail.status === 'RUNNING' || detail.status === 'pending' || detail.status === 'running') && <Button type="button" size="sm" variant="destructive" className="min-h-11" disabled={historyAction.isPending} onClick={() => {
                        if (window.confirm(`请求取消流水线「${pipelineTitle(detail)}」？`)) historyAction.mutate({ kind: 'cancel', pipelineId: id })
                      }}>取消流水线</Button>}
                      {(detail.status === 'FAILED' || detail.status === 'CANCELLED' || detail.status === 'failed' || detail.status === 'cancelled') && <Button type="button" size="sm" variant="outline" className="min-h-11" disabled={historyAction.isPending} onClick={() => {
                        if (window.confirm(`使用这条流水线的原始任务参数创建一条新流水线？`)) historyAction.mutate({ kind: 'retry', pipelineId: id })
                      }}>重试</Button>}
                    </div>
                  </>}
                </div>}
              </li>
            })}
          </ol>}
          {history && <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3"><p className="text-sm text-muted-foreground">第 {history.page} 页 · 每页 {history.size} 条 · 共 {history.total} 条</p><div className="flex gap-2"><Button type="button" variant="outline" disabled={historyPage <= 1 || historyQuery.isFetching} onClick={() => setHistoryPage((page) => Math.max(1, page - 1))}>上一页</Button><Button type="button" variant="outline" disabled={!historyNextAvailable || historyQuery.isFetching} onClick={() => setHistoryPage((page) => page + 1)}>下一页</Button></div></div>}
        </CardContent>
      </Card>
    </section>}
  </main>
}
