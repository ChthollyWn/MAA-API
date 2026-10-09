import { useState } from 'react'
import { Plus, Search, Trash2 } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { InfoHint } from '@/components/ui/info-hint'
import { Input } from '@/components/ui/input'
import { UNSET, getType, isObject, normalizeSchema, objectValue, type JsonSchema } from '../form'
import { NUMBER_INPUT_CLASS, ariaProps, type ControlProps } from './control-props'
import { useItemCatalog, type ItemCatalogEntry } from './resources'

const SEARCH_RESULT_LIMIT = 8

function withoutKey(map: Record<string, unknown>, key: string): Record<string, unknown> {
  const next = { ...map }
  delete next[key]
  return next
}

function ItemIcon({ item, size }: { item: ItemCatalogEntry | undefined; size: 'sm' | 'md' }) {
  if (!item?.icon_url) return null
  return <img src={item.icon_url} alt="" className={size === 'md' ? 'size-8 shrink-0 rounded object-contain' : 'size-6 shrink-0 rounded object-contain'} />
}

/** 掉落物数量（item-count-map）：按中文名搜索物品目录，选中后以行显示「物品名 + 数量 + 删除」，写入物品 id。 */
export function ItemCountMapControl({ id, labelId, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const catalog = useItemCatalog()
  const [query, setQuery] = useState('')
  const [manualId, setManualId] = useState('')
  const map = objectValue(value)
  const entries = Object.entries(map)
  const items = catalog.data ?? []
  const keyword = query.trim().toLocaleLowerCase()
  const matches = keyword
    ? items.filter((item) => !(item.item_id in map) && `${item.name} ${item.item_id}`.toLocaleLowerCase().includes(keyword)).slice(0, SEARCH_RESULT_LIMIT)
    : []
  const add = (itemId: string) => onChange({ ...map, [itemId]: 1 })
  const trimmedId = manualId.trim()

  return (
    <div role="group" aria-labelledby={labelId} {...ariaProps({ invalid, describedBy })} className="space-y-2.5">
      <p className="text-footnote text-muted-foreground">
        <InfoHint label="掉落数量说明">任一物品达到指定数量即停止任务。</InfoHint>
      </p>
      {entries.length > 0 ? (
        <ul className="divide-y overflow-hidden rounded-lg bg-surface-2">
          {entries.map(([itemId, quantity]) => {
            const item = items.find((entry) => entry.item_id === itemId)
            const name = item?.name ?? itemId
            return (
              <li key={itemId} className="flex min-h-14 items-center gap-2 py-1.5 pl-3 pr-1">
                <ItemIcon item={item} size="md" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[0.9375rem]">{item?.name ?? `物品 ${itemId}`}</span>
                  <span className="block font-mono text-footnote text-muted-foreground">{itemId}</span>
                </span>
                <Input
                  type="number"
                  inputMode="numeric"
                  aria-label={`数量：${name}`}
                  className={`w-20 ${NUMBER_INPUT_CLASS}`}
                  min={1}
                  step={1}
                  value={typeof quantity === 'number' ? quantity : 1}
                  disabled={disabled}
                  onChange={(event) => {
                    if (event.target.value === '') return
                    const amount = Number(event.target.value)
                    if (Number.isFinite(amount) && amount >= 0) onChange({ ...map, [itemId]: amount })
                  }}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`移除物品：${name}`}
                  disabled={disabled}
                  onClick={() => {
                    const next = withoutKey(map, itemId)
                    onChange(Object.keys(next).length ? next : UNSET)
                  }}
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="text-footnote text-muted-foreground">{isNull ? '已设为空' : '尚未添加物品数量条件'}</p>
      )}

      {catalog.isPending ? (
        <p role="status" className="text-footnote text-muted-foreground">正在加载物品目录…</p>
      ) : items.length > 0 ? (
        <div className="space-y-1.5">
          <div className="relative">
            <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              id={id}
              type="search"
              aria-label="搜索物品名称或 ID"
              className="pl-9"
              placeholder="搜索物品，例如 固源岩"
              value={query}
              disabled={disabled}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          {keyword ? (
            matches.length > 0 ? (
              <ul aria-label="物品搜索结果" className="divide-y overflow-hidden rounded-lg border">
                {matches.map((item) => (
                  <li key={item.item_id}>
                    <button
                      type="button"
                      aria-label={`添加 ${item.name}`}
                      disabled={disabled}
                      className="flex min-h-12 w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:opacity-45"
                      onClick={() => {
                        add(item.item_id)
                        setQuery('')
                      }}
                    >
                      <ItemIcon item={item} size="sm" />
                      <span className="min-w-0 flex-1 truncate text-[0.9375rem]">{item.name}</span>
                      <span className="font-mono text-footnote text-muted-foreground">{item.item_id}</span>
                      <Plus aria-hidden="true" className="size-4 shrink-0 text-primary-text" />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-footnote text-muted-foreground">还没有匹配的物品</p>
            )
          ) : null}
        </div>
      ) : (
        <div className="flex gap-2">
          <Input
            id={id}
            aria-label="物品 ID"
            className="flex-1 font-mono"
            placeholder="输入物品 ID"
            value={manualId}
            disabled={disabled}
            onChange={(event) => setManualId(event.target.value)}
          />
          <Button
            type="button"
            variant="outline"
            disabled={disabled || !trimmedId || trimmedId in map}
            onClick={() => {
              add(trimmedId)
              setManualId('')
            }}
          >
            <Plus aria-hidden="true" />
            添加 ID
          </Button>
        </div>
      )}
      {catalog.error ? (
        <p role="status" className="text-footnote text-muted-foreground">读取物品目录失败：{toApiError(catalog.error).message}。可以手动输入物品 ID。</p>
      ) : null}
    </div>
  )
}

const RECRUIT_LEVELS = ['3', '4', '5', '6']
const DEFAULT_RECRUIT_MINUTES = 540

/** 公招时长（duration-map）：3–6 星各一行，时、分两个数字框，按分钟写入。 */
export function DurationMapControl({ labelId, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const map = objectValue(value)
  return (
    <div role="group" aria-labelledby={labelId} {...ariaProps({ invalid, describedBy })} className="space-y-2">
      <p className="text-footnote text-muted-foreground">
        {isNull ? '已设为空。' : null}招募等级为 3–6 星 Tag，默认时长 09:00
      </p>
      <ul className="divide-y overflow-hidden rounded-lg bg-surface-2">
        {RECRUIT_LEVELS.map((key) => {
          const minutes = typeof map[key] === 'number' ? map[key] as number : DEFAULT_RECRUIT_MINUTES
          const hours = Math.floor(minutes / 60)
          const remainder = minutes % 60
          return (
            <li key={key} className="flex min-h-14 items-center gap-2 px-3 py-1.5">
              <span className="min-w-0 flex-1 text-[0.9375rem]">{key} 星 Tag</span>
              <Input
                type="number"
                inputMode="numeric"
                aria-label={`时长小时：${key}`}
                className={`w-16 ${NUMBER_INPUT_CLASS}`}
                min={0}
                step={1}
                value={hours}
                disabled={disabled}
                onChange={(event) => {
                  const nextHours = event.target.value === '' ? 0 : Number(event.target.value)
                  if (Number.isFinite(nextHours)) onChange({ ...map, [key]: nextHours * 60 + remainder })
                }}
              />
              <span aria-hidden="true" className="text-footnote text-muted-foreground">时</span>
              <Input
                type="number"
                inputMode="numeric"
                aria-label={`时长分钟：${key}`}
                className={`w-16 ${NUMBER_INPUT_CLASS}`}
                min={0}
                max={59}
                step={1}
                value={remainder}
                disabled={disabled}
                onChange={(event) => {
                  const nextMinutes = event.target.value === '' ? 0 : Number(event.target.value)
                  if (Number.isFinite(nextMinutes)) onChange({ ...map, [key]: hours * 60 + nextMinutes })
                }}
              />
              <span aria-hidden="true" className="text-footnote text-muted-foreground">分</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/** 没有专用控件的 additionalProperties 键值映射：每项一行，可改键、改值、删除，底部新增一项。 */
export function KeyValueMapControl({ labelId, schema, root, value, isNull, disabled, invalid, describedBy, onChange }: ControlProps) {
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const map = objectValue(value)
  const entries = Object.entries(map)
  const additional = isObject(schema.additionalProperties) ? schema.additionalProperties as JsonSchema : { type: 'integer' }
  const childType = getType(normalizeSchema(additional, root).schema)
  const numeric = childType === 'integer' || childType === 'number'
  const parse = (raw: string): unknown => (numeric ? Number(raw) : raw)
  const trimmedKey = newKey.trim()
  const canAdd = Boolean(trimmedKey) && !(trimmedKey in map) && newValue !== '' && !(numeric && !Number.isFinite(Number(newValue)))

  return (
    <div role="group" aria-labelledby={labelId} {...ariaProps({ invalid, describedBy })} className="space-y-2.5">
      {entries.length > 0 ? (
        <ul className="space-y-2">
          {entries.map(([key, entryValue], index) => (
            <li key={index} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_2.75rem] items-center gap-2">
              <Input
                aria-label={`键：${key}`}
                value={key}
                disabled={disabled}
                onChange={(event) => {
                  const nextKey = event.target.value
                  if (nextKey === key || nextKey in map) return
                  onChange(Object.fromEntries(entries.map(([candidate, candidateValue]) => [candidate === key ? nextKey : candidate, candidateValue])))
                }}
              />
              <Input
                aria-label={`值：${key}`}
                type={numeric ? 'number' : 'text'}
                value={typeof entryValue === 'string' || typeof entryValue === 'number' ? entryValue : JSON.stringify(entryValue)}
                disabled={disabled}
                onChange={(event) => {
                  const next = parse(event.target.value)
                  if ((typeof next === 'number' && Number.isFinite(next)) || typeof next === 'string') onChange({ ...map, [key]: next })
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`移除键 ${key}`}
                disabled={disabled}
                onClick={() => {
                  const next = withoutKey(map, key)
                  onChange(Object.keys(next).length ? next : UNSET)
                }}
              >
                <Trash2 aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-footnote text-muted-foreground">{isNull ? '已设为空' : '尚未添加映射项'}</p>
      )}
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] gap-2">
        <Input aria-label="新键" placeholder="键" value={newKey} disabled={disabled} onChange={(event) => setNewKey(event.target.value)} />
        <Input aria-label="新值" placeholder="值" type={numeric ? 'number' : 'text'} value={newValue} disabled={disabled} onChange={(event) => setNewValue(event.target.value)} />
        <Button
          type="button"
          variant="outline"
          aria-label="添加映射项"
          disabled={disabled || !canAdd}
          onClick={() => {
            onChange({ ...map, [trimmedKey]: parse(newValue) })
            setNewKey('')
            setNewValue('')
          }}
        >
          <Plus aria-hidden="true" />
          添加
        </Button>
      </div>
    </div>
  )
}
