import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { ExternalLink } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ResponsiveSheet } from '@/components/ui/sheet'
import type { AuditRecord } from '@/features/audit/types'
import {
  callerText, formatDateTime, pipelineIdOf, prettyJson, riskLabel, riskTone, statusLabel, statusTone,
} from '@/features/audit/types'
import { cn } from '@/lib/utils'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <dt className="shrink-0 text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-right text-sm [overflow-wrap:anywhere]">{children}</dd>
    </div>
  )
}

function Code({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={cn('font-mono text-[0.8125rem]', className)}>{children}</code>
}

function JsonBlock({ title, children }: { title: string; children: string }) {
  return (
    <section aria-label={title} className="space-y-1.5">
      <h3 className="text-footnote font-medium text-muted-foreground">{title}</h3>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-3 font-mono text-xs leading-5">{children}</pre>
    </section>
  )
}

export interface AuditDetailSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  record: AuditRecord | null
  loading: boolean
  error: string | null
}

/** 一条审计的完整信息：编号、请求 / 会话 / 确认 / 授权、参数与结果摘要，以及关联流水线。 */
export function AuditDetailSheet({ open, onOpenChange, record, loading, error }: AuditDetailSheetProps) {
  const pipelineId = record ? pipelineIdOf(record) : null
  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      desktop="panel"
      title={<span className="font-mono [overflow-wrap:anywhere]">{record?.tool_name ?? '审计详情'}</span>}
      description={record ? `${callerText(record)} · ${formatDateTime(record.created_at)}` : undefined}
      footer={pipelineId ? (
        <Button asChild variant="soft" className="w-full">
          <Link to={`/tasks/history/${encodeURIComponent(pipelineId)}`}>
            <ExternalLink aria-hidden="true" />
            查看流水线与日志
          </Link>
        </Button>
      ) : undefined}
    >
      {record ? (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-1.5">
            <Badge variant={statusTone(record.status)}>{statusLabel(record.status)}</Badge>
            <Badge variant={riskTone(record.risk_level)}>{riskLabel(record.risk_level)}</Badge>
            {record.forced ? <Badge variant="warning">强制执行</Badge> : null}
          </div>
          {loading ? <p role="status" className="text-footnote text-muted-foreground">正在读取完整详情…</p> : null}
          {error ? <InlineAlert tone="warning">详情读取失败，下面显示列表里的信息：{error}</InlineAlert> : null}
          <dl className="divide-y">
            <Row label="审计编号"><Code>{record.audit_id}</Code></Row>
            <Row label="耗时"><span className="tabular-nums">{record.duration_ms} ms</span></Row>
            {record.request_id ? <Row label="请求编号"><Code>{record.request_id}</Code></Row> : null}
            {record.session_id ? <Row label="会话"><Code>{record.session_id}</Code></Row> : null}
            {record.confirmation_id ? <Row label="确认"><Code>{record.confirmation_id}</Code></Row> : null}
            {record.authorized_by_id ? <Row label="授权来源"><Code>{record.authorized_by_id}</Code></Row> : null}
            {record.error_code ? <Row label="错误码"><Code className="text-danger">{record.error_code}</Code></Row> : null}
          </dl>
          <JsonBlock title="参数">{prettyJson(record.arguments)}</JsonBlock>
          {record.result_summary ? <JsonBlock title="结果摘要">{record.result_summary}</JsonBlock> : null}
        </div>
      ) : null}
    </ResponsiveSheet>
  )
}
