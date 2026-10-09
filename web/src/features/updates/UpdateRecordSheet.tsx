import type { ReactNode } from 'react'
import { RotateCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ResponsiveSheet } from '@/components/ui/sheet'
import type { UpdateRecord } from '@/features/updates/types'
import {
  asText, errorMessage, formatDate, phaseStepLabel, recordIsFailed, recordLogText, recordStatus, recordVersion, targetLabels,
} from '@/features/updates/types'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <dt className="shrink-0 text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-right text-sm [overflow-wrap:anywhere]">{children}</dd>
    </div>
  )
}

export interface UpdateRecordSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  record: UpdateRecord | null
  loading: boolean
  error: unknown
  retrying: boolean
  retryError: unknown
  onRetry: (id: string) => void
}

/** 一条更新记录的详情：编号、阶段、错误码与消息、结束时间、日志；失败的可以重试。 */
export function UpdateRecordSheet({ open, onOpenChange, record, loading, error, retrying, retryError, onRetry }: UpdateRecordSheetProps) {
  const status = record ? recordStatus(record.status) : null
  const version = record ? recordVersion(record) : null
  const phase = record ? phaseStepLabel(record.phase) : null
  const errorCode = record ? asText(record.error_code) : null
  const errorText = record ? asText(record.error_message) : null
  const log = record ? recordLogText(record.log) : null
  const failed = record ? recordIsFailed(record) : false

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title="更新详情"
      description={record ? targetLabels[record.target] ?? record.target : undefined}
      desktop="panel"
      footer={record && failed ? (
        <Button className="w-full" loading={retrying} onClick={() => onRetry(record.id)}>
          {retrying ? null : <RotateCw aria-hidden="true" />}
          {retrying ? '正在重试…' : '重试更新'}
        </Button>
      ) : undefined}
    >
      {record ? (
        <div className="space-y-4">
          {loading ? <p role="status" className="text-footnote text-muted-foreground">正在读取完整详情…</p> : null}
          {error ? <InlineAlert tone="warning">详情读取失败，下面显示列表里的信息：{errorMessage(error)}</InlineAlert> : null}
          <dl className="divide-y">
            <Row label="记录编号"><code className="font-mono text-[0.8125rem]">{record.id}</code></Row>
            {status ? <Row label="状态"><Badge variant={status.tone}>{status.label}</Badge></Row> : null}
            {version ? <Row label="版本"><span className="font-mono tabular-nums">{version}</span></Row> : null}
            {phase ? <Row label="阶段">{phase}</Row> : null}
            <Row label="开始时间"><span className="tabular-nums">{formatDate(record.started_at ?? record.created_at)}</span></Row>
            {record.finished_at ? <Row label="结束时间"><span className="tabular-nums">{formatDate(record.finished_at)}</span></Row> : null}
            {errorCode ? <Row label="错误码"><code className="font-mono text-[0.8125rem] text-danger">{errorCode}</code></Row> : null}
            {errorText ? <Row label="错误信息"><span className="text-danger">{errorText}</span></Row> : null}
          </dl>
          {log ? (
            <section aria-label="更新日志" className="space-y-1.5">
              <h3 className="text-footnote font-medium text-muted-foreground">日志</h3>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-3 font-mono text-xs leading-5">{log}</pre>
            </section>
          ) : null}
          {retryError ? <InlineAlert tone="danger">重试失败：{errorMessage(retryError)}</InlineAlert> : null}
        </div>
      ) : null}
    </ResponsiveSheet>
  )
}
