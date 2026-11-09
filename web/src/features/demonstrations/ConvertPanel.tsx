/** 转换预览与注册（M89 §4.6）：按节点展开编译结果，有错误时禁用注册。 */

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { DemonstrationConvertOut, DemonstrationOut } from './useDemonstrations'

type CompileError = Record<string, string>

function errorText(item: CompileError): string {
  const task = item.task ?? '任务链'
  const field = item.field ?? ''
  const reason = item.reason ?? '校验没有通过'
  return field ? `${task} · ${field}：${reason}` : `${task}：${reason}`
}

export interface ConvertPanelProps {
  demonstration: DemonstrationOut
  preview: DemonstrationConvertOut | null
  previewing: boolean
  registering: boolean
  onPreview: () => void
  onRegister: () => void
}

/**
 * 预览只调 `convert(register=false)`，不改示教状态；注册由页面先弹确认。
 * 「会写入 N 个任务节点」的 N 与预览里的节点数一致。
 */
export function ConvertPanel({
  demonstration,
  preview,
  previewing,
  registering,
  onPreview,
  onRegister,
}: ConvertPanelProps) {
  const compile = preview?.compile ?? null
  const errors = compile?.errors ?? []
  const chainErrors = compile?.chain_errors ?? []
  const hasErrors = Boolean(compile && (!compile.valid || errors.length > 0 || chainErrors.length > 0))
  const nodes = compile ? Object.entries(compile.tasks) : []
  const canRegister = Boolean(compile) && !hasErrors && demonstration.status === 'recorded'
  const convertedEntry =
    demonstration.status === 'converted' && demonstration.converted_to
      ? String(demonstration.converted_to.entry ?? '')
      : ''

  return (
    <section className="space-y-3" aria-label="转换预览">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto px-1 text-footnote font-medium text-muted-foreground">转换预览</h3>
        <Button disabled={previewing || registering} onClick={onPreview} size="sm" variant="outline">
          预览任务
        </Button>
        {demonstration.status === 'recorded' ? (
          <Button disabled={!canRegister || previewing || registering} onClick={onRegister} size="sm">
            注册任务
          </Button>
        ) : null}
      </div>

      {convertedEntry ? (
        <p className="px-1 text-sm text-muted-foreground">
          已注册为 <code className="break-all font-mono text-foreground">{convertedEntry}</code>
        </p>
      ) : null}

      {compile ? (
        <div className="space-y-3 rounded-xl border bg-card p-3 shadow-card">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={hasErrors ? 'danger' : 'success'}>{hasErrors ? '校验未通过' : '校验通过'}</Badge>
            <span className="text-footnote text-muted-foreground">
              {nodes.length} 个任务节点
            </span>
          </div>

          {errors.length > 0 ? (
            <div className="space-y-1">
              <p className="text-sm font-medium text-danger">错误</p>
              <ul className="list-inside list-disc space-y-0.5 text-footnote text-danger">
                {errors.map((item, index) => <li key={`error-${index}`}>{errorText(item)}</li>)}
              </ul>
            </div>
          ) : null}

          {chainErrors.length > 0 ? (
            <div className="space-y-1">
              <p className="text-sm font-medium text-danger">任务链错误</p>
              <ul className="list-inside list-disc space-y-0.5 text-footnote text-danger">
                {chainErrors.map((item, index) => <li key={`chain-error-${index}`}>{errorText(item)}</li>)}
              </ul>
            </div>
          ) : null}

          <div className="space-y-1">
            <p className="text-sm font-medium">任务链</p>
            <ol className="list-inside list-decimal space-y-0.5 text-footnote text-muted-foreground">
              {compile.chain.map((name) => (
                <li key={name} className="break-all font-mono">{name}</li>
              ))}
            </ol>
          </div>

          <div className="space-y-1">
            <p className="text-sm font-medium">任务节点</p>
            <div className="space-y-1">
              {nodes.map(([name, node]) => (
                <details key={name} className="rounded-lg border bg-background px-2 py-1.5">
                  <summary className="cursor-pointer break-all font-mono text-footnote">{name}</summary>
                  <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-all text-[0.6875rem] leading-4 text-muted-foreground">
                    {JSON.stringify(node, null, 2)}
                  </pre>
                </details>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <p className="px-1 text-footnote text-muted-foreground">
          {demonstration.status === 'converted' ? '这条示教已经转换过' : '先预览任务，确认编译结果后再注册'}
        </p>
      )}
    </section>
  )
}

export default ConvertPanel
