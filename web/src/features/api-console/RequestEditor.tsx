import { ArrowLeft, CalendarClock, Copy, Plus, Trash2, WandSparkles } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Disclosure } from '@/components/ui/disclosure'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { Section } from '@/components/ui/section'
import { Switch, SwitchRow } from '@/components/ui/switch'
import { COARSE_POINTER_QUERY, useMediaQuery } from '@/lib/media'
import { cn } from '@/lib/utils'
import { parameterSchema, type OpenApiDocument } from './console-model'
import { LazyJsonEditor } from './LazyJsonEditor'
import { MethodBadge, PathText } from './OperationList'
import { operationParameters, type ConsoleOperation, type QueryDraft } from './utils'

/** 手机分步工作流里随标题栏吸顶：返回接口列表 + 当前接口。 */
export function RequestStepBar({ operation, onBack }: { operation: ConsoleOperation; onBack: () => void }) {
  return (
    <div className="flex min-h-11 items-center gap-2">
      <Button variant="ghost" size="icon" aria-label="返回接口列表" className="-ml-3" onClick={onBack}>
        <ArrowLeft aria-hidden="true" />
      </Button>
      <MethodBadge method={operation.method} />
      <code className="line-clamp-2 min-w-0 flex-1 font-mono text-sm font-semibold [overflow-wrap:anywhere]"><PathText path={operation.path} /></code>
    </div>
  )
}

export function RequestHeading({ operation, showPath, className }: { operation: ConsoleOperation; showPath: boolean; className?: string }) {
  if (!showPath && !operation.summary && !operation.operationId) return null
  return (
    <div className={cn('space-y-1', className)}>
      {showPath ? (
        <div className="flex items-start gap-2">
          <MethodBadge method={operation.method} className="mt-0.5" />
          <code className="min-w-0 flex-1 font-mono text-[0.9375rem] font-semibold leading-6 [overflow-wrap:anywhere]"><PathText path={operation.path} /></code>
        </div>
      ) : null}
      {operation.summary ? <p className="text-sm">{operation.summary}</p> : null}
      {operation.operationId ? <p className="font-mono text-footnote text-muted-foreground [overflow-wrap:anywhere]">{operation.operationId}</p> : null}
    </div>
  )
}

function schemaOptions(schema: Record<string, unknown>): string[] | null {
  if (Array.isArray(schema.enum)) return schema.enum.map(String)
  if (schema.type === 'boolean') return ['true', 'false']
  return null
}

function ParameterControl({ id, schema, value, onChange, emptyLabel }: {
  id: string
  schema: Record<string, unknown>
  value: string
  onChange: (value: string) => void
  emptyLabel: string
}) {
  const options = schemaOptions(schema)
  if (options) {
    return (
      <NativeSelect id={id} value={value} onChange={(event) => onChange(event.target.value)} className="font-mono">
        {options.includes(value) ? null : <option value={value}>{value === '' ? emptyLabel : value}</option>}
        {options.map((option) => <option key={option} value={option}>{option}</option>)}
      </NativeSelect>
    )
  }
  const numeric = schema.type === 'integer' || schema.type === 'number'
  return (
    <Input
      id={id}
      type={numeric ? 'number' : 'text'}
      inputMode={schema.type === 'integer' ? 'numeric' : undefined}
      autoCapitalize="off"
      autoCorrect="off"
      spellCheck={false}
      className="font-mono"
      value={value}
      placeholder={schema.default == null ? '' : String(schema.default)}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

export function ParameterSections({ operation, document, pathParams, onPathParamsChange, query, onQueryChange }: {
  operation: ConsoleOperation
  document?: OpenApiDocument
  pathParams: Record<string, string>
  onPathParamsChange: (value: Record<string, string>) => void
  query: Record<string, QueryDraft>
  onQueryChange: (value: Record<string, QueryDraft>) => void
}) {
  const parameters = operationParameters(operation.operation, document)
  const pathParameters = parameters.filter((parameter) => parameter.in === 'path')
  const queryParameters = parameters.filter((parameter) => parameter.in === 'query')
  return (
    <>
      {pathParameters.length > 0 ? (
        <Section id="console-path-params" title="路径参数" description="全部必填" className="p-4">
          <div className="space-y-4">
            {pathParameters.map((parameter) => {
              const name = String(parameter.name)
              return (
                <Field
                  key={name}
                  label={<span className="font-mono">{name}</span>}
                  htmlFor={`path-${name}`}
                  description={typeof parameter.description === 'string' ? parameter.description : undefined}
                >
                  <ParameterControl
                    id={`path-${name}`}
                    schema={parameterSchema(parameter, document)}
                    value={pathParams[name] ?? ''}
                    emptyLabel="请选择"
                    onChange={(value) => onPathParamsChange({ ...pathParams, [name]: value })}
                  />
                </Field>
              )
            })}
          </div>
        </Section>
      ) : null}
      {queryParameters.length > 0 ? (
        <Section id="console-query-params" title="Query 参数" description="只发送已启用的参数；不启用和发送空值是两种语义" className="p-4">
          <div className="divide-y">
            {queryParameters.map((parameter) => {
              const name = String(parameter.name)
              const required = parameter.required === true
              const item = query[name] ?? { enabled: required, value: '' }
              const description = typeof parameter.description === 'string' ? parameter.description : ''
              return (
                <div key={name} className="space-y-2 py-3 first:pt-1 last:pb-1">
                  <div className="flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                        {item.enabled
                          ? <label htmlFor={`query-${name}`} className="font-mono text-sm font-medium [overflow-wrap:anywhere]">{name}</label>
                          : <span className="font-mono text-sm font-medium text-muted-foreground [overflow-wrap:anywhere]">{name}</span>}
                        {required ? <Badge variant="neutral">必填</Badge> : null}
                      </div>
                      {description ? <p className="mt-0.5 text-footnote text-muted-foreground">{description}</p> : null}
                    </div>
                    <Switch
                      aria-label={`启用 query 参数 ${name}`}
                      checked={item.enabled}
                      disabled={required}
                      onCheckedChange={(enabled) => onQueryChange({ ...query, [name]: { ...item, enabled } })}
                    />
                  </div>
                  {item.enabled ? (
                    <ParameterControl
                      id={`query-${name}`}
                      schema={parameterSchema(parameter, document)}
                      value={item.value}
                      emptyLabel="空值"
                      onChange={(value) => onQueryChange({ ...query, [name]: { ...item, value } })}
                    />
                  ) : null}
                </div>
              )
            })}
          </div>
        </Section>
      ) : null}
    </>
  )
}

const BLOCKED_HEADER = /^(authorization|cookie)$/i

function nextCustomHeaderName(headers: Record<string, string>): string {
  let index = Object.keys(headers).length + 1
  while (`X-Custom-${index}` in headers) index += 1
  return `X-Custom-${index}`
}

export function HeadersSection({ headers, onHeadersChange, onBlockedHeader, token, revealToken, onRevealTokenChange, requestId }: {
  headers: Record<string, string>
  onHeadersChange: (value: Record<string, string>) => void
  onBlockedHeader: () => void
  token: string | null
  revealToken: boolean
  onRevealTokenChange: (value: boolean) => void
  requestId: string | null
}) {
  const entries = Object.entries(headers)
  const rename = (index: number, name: string) => {
    if (BLOCKED_HEADER.test(name.trim())) {
      onBlockedHeader()
      return
    }
    onHeadersChange(Object.fromEntries(entries.map(([key, value], position) => [position === index ? name : key, value])))
  }
  return (
    <div className="px-4 py-1">
      <Disclosure variant="plain" title="请求头" description="Authorization 由当前 token 自动注入" badge={entries.length ? `自定义 ${entries.length}` : undefined}>
        <div className="space-y-3">
          <dl className="divide-y rounded-lg bg-surface-2/70">
            <div className="flex min-h-11 items-center gap-3 px-3 py-1.5">
              <dt className="w-28 shrink-0 font-mono text-footnote text-muted-foreground">Authorization</dt>
              <dd className="min-w-0 flex-1 truncate font-mono text-footnote">{token ? revealToken ? `Bearer ${token}` : 'Bearer ••••••••' : '未设置'}</dd>
              {token ? (
                <Button variant="ghost" size="sm" className="-mr-2 shrink-0 px-2.5 text-primary-text" onClick={() => onRevealTokenChange(!revealToken)}>
                  {revealToken ? '隐藏' : '显示'}
                </Button>
              ) : null}
            </div>
            <div className="flex min-h-11 items-center gap-3 px-3 py-1.5">
              <dt className="w-28 shrink-0 font-mono text-footnote text-muted-foreground">X-Request-Id</dt>
              <dd className="min-w-0 flex-1 truncate font-mono text-footnote">{requestId ?? '发送时生成'}</dd>
            </div>
          </dl>
          <p className="px-1 text-footnote text-muted-foreground">会话凭据只读；临时换 token 请到「请求环境」。</p>
          {entries.map(([key, value], index) => (
            <div key={index} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto] items-center gap-2">
              <Input aria-label={`自定义 header 名称 ${index + 1}`} value={key} placeholder="Header" autoCapitalize="off" autoCorrect="off" spellCheck={false} className="font-mono" onChange={(event) => rename(index, event.target.value)} />
              <Input aria-label={`自定义 header 值 ${index + 1}`} value={value} placeholder="值" autoCapitalize="off" autoCorrect="off" spellCheck={false} className="font-mono" onChange={(event) => onHeadersChange({ ...headers, [key]: event.target.value })} />
              <Button
                variant="ghost"
                size="icon"
                aria-label={`删除 header ${key || index + 1}`}
                onClick={() => onHeadersChange(Object.fromEntries(entries.filter((_, position) => position !== index)))}
              >
                <Trash2 aria-hidden="true" />
              </Button>
            </div>
          ))}
          <Button variant="outline" size="sm" onClick={() => onHeadersChange({ ...headers, [nextCustomHeaderName(headers)]: '' })}>
            <Plus aria-hidden="true" />添加 header
          </Button>
          <p className="px-1 text-footnote text-muted-foreground">可以用自定义 X-Token 测试该渠道；Authorization、X-Token、Cookie 不会写入历史与收藏。</p>
        </div>
      </Disclosure>
    </div>
  )
}

export function BodySection({ body, onBodyChange, schema, openApi, optionalFields, onFormat }: {
  body: string
  onBodyChange: (value: string) => void
  schema: Record<string, unknown>
  openApi?: OpenApiDocument
  optionalFields: Array<{ path: string; description: string }>
  onFormat: () => void
}) {
  const coarse = useMediaQuery(COARSE_POINTER_QUERY)
  return (
    <Section
      id="console-body"
      title="请求体"
      description="JSON；校验不通过仍可发送"
      className="p-4"
      actions={(
        <Button variant="ghost" size="sm" className="-mr-2 text-primary-text" onClick={onFormat}>
          <WandSparkles aria-hidden="true" />格式化
        </Button>
      )}
    >
      <LazyJsonEditor value={body} onChange={onBodyChange} schema={schema} openApi={openApi} ariaLabel="JSON 请求体编辑器" />
      {coarse ? null : <p className="px-1 text-footnote text-muted-foreground">Ctrl/⌘ + Shift + F 也能格式化 · 支持字段补全与折叠</p>}
      {optionalFields.length > 0 ? (
        <Disclosure
          variant="plain"
          title={<>可选字段说明 <span className="font-normal text-muted-foreground tabular-nums">{optionalFields.length}</span></>}
        >
          <dl className="space-y-2.5">
            {optionalFields.map((field) => (
              <div key={field.path}>
                <dt className="font-mono text-footnote font-medium [overflow-wrap:anywhere]">{field.path}</dt>
                <dd className="mt-0.5 text-footnote text-muted-foreground">{field.description}</dd>
              </div>
            ))}
          </dl>
        </Disclosure>
      ) : null}
    </Section>
  )
}

export function FavoriteSection({ name, onNameChange, editing, saving, onSave, canSchedule, onSchedule }: {
  name: string
  onNameChange: (value: string) => void
  editing: boolean
  saving: boolean
  onSave: () => void
  canSchedule: boolean
  onSchedule: () => void
}) {
  return (
    <Section id="console-favorite" title="收藏" description="保存到当前服务；鉴权凭据不会写入收藏" className="p-4">
      <div className="flex gap-2">
        <Input
          aria-label="收藏名称"
          value={name}
          maxLength={64}
          placeholder="例如：查询服务健康"
          enterKeyHint="done"
          onChange={(event) => onNameChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.nativeEvent.isComposing) onSave()
          }}
        />
        <Button className="shrink-0" loading={saving} onClick={onSave}>{editing ? '更新' : '保存'}</Button>
      </div>
      {canSchedule ? (
        <Button variant="outline" className="w-full" onClick={onSchedule}>
          <CalendarClock aria-hidden="true" />转为定时任务
        </Button>
      ) : null}
    </Section>
  )
}

export function CurlSection({ command, includeToken, onIncludeTokenChange, onCopy }: {
  command: string
  includeToken: boolean
  onIncludeTokenChange: (value: boolean) => void
  onCopy: () => void
}) {
  return (
    <div className="px-4 py-1">
      <Disclosure variant="plain" title="cURL 导出" description="默认用 $MAA_TOKEN 占位，不含真实 token">
        <div className="space-y-3">
          <SwitchRow
            label="包含真实 token（仅供自用）"
            description="打开后命令里是明文 token，不要贴给别人。"
            checked={includeToken}
            onCheckedChange={onIncludeTokenChange}
          />
          <pre data-testid="curl-preview" className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-surface-2 p-3 font-mono text-xs leading-5">{command}</pre>
          <Button variant="outline" size="sm" onClick={onCopy}>
            <Copy aria-hidden="true" />复制 cURL
          </Button>
        </div>
      </Disclosure>
    </div>
  )
}
