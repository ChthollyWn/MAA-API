import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Radar } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Section } from '@/components/ui/section'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import {
  deleteTriggerRule,
  describeTriggerError,
  saveTriggerRule,
  type TriggerMode,
  type TriggerRule,
} from './api'
import {
  formatDateTime,
  formatStats,
  GATE_HINT,
  MODE_OPTIONS,
  originBadge,
  ruleMode,
  ruleWrite,
  sourceLabel,
  suggestionBadge,
  workerTitle,
} from './format'
import { invalidateTriggerRules, triggerRulesQuery, triggerSpecsQuery } from './queries'
import { RuleEditor } from './RuleEditor'

const card = 'rounded-xl border bg-card shadow-card'

interface RuleRowProps {
  rule: TriggerRule
  workerLabel: string
  busy: boolean
  disabled: boolean
  onMode: (mode: TriggerMode) => void
  onEdit: () => void
  onRestore: () => void
  onDelete: () => void
}

function RuleRow({ rule, workerLabel, busy, disabled, onMode, onEdit, onRestore, onDelete }: RuleRowProps) {
  const badge = originBadge(rule)
  const suggestion = suggestionBadge(rule)
  return (
    <li data-rule-id={rule.id} className={`${card} space-y-3 p-4`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="break-words text-[0.9375rem] font-medium leading-5">{rule.title}</p>
          <p className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-footnote text-muted-foreground">
            <code className="truncate font-mono">{rule.id}</code>
            <span aria-hidden="true">·</span>
            <span>{sourceLabel(rule.source)}</span>
            <span aria-hidden="true">·</span>
            <code className="truncate font-mono">{rule.kind_pattern}</code>
          </p>
        </div>
        <Badge variant={badge.tone}>{badge.label}</Badge>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <SegmentedControl
          ariaLabel={`「${rule.title}」的档位`}
          value={ruleMode(rule)}
          onValueChange={onMode}
          options={MODE_OPTIONS}
          size="sm"
          disabled={disabled}
        />
        <span className="text-footnote text-muted-foreground">执行代理 {workerLabel}</span>
        {busy ? <Spinner className="text-muted-foreground" /> : null}
      </div>

      {rule.gated_by ? (
        <p className="text-footnote font-medium text-warning">{GATE_HINT}</p>
      ) : null}

      <p className="text-footnote text-muted-foreground">{formatStats(rule.stats)}</p>

      <div className="flex flex-wrap items-center gap-2">
        {suggestion ? <Badge variant={suggestion.tone}>{suggestion.label}</Badge> : null}
        {rule.cron ? (
          <span className="text-footnote text-muted-foreground">
            cron <code className="font-mono">{rule.cron}</code>
            {rule.next_fire_at ? ` · 下次 ${formatDateTime(rule.next_fire_at)}` : ''}
          </span>
        ) : null}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={onEdit}>
            编辑规则
          </Button>
          {rule.origin === 'override' ? (
            <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={onRestore}>
              恢复默认
            </Button>
          ) : null}
          {rule.origin === 'user' ? (
            <Button type="button" size="sm" variant="ghost" className="text-danger" disabled={disabled} onClick={onDelete}>
              删除规则
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  )
}

/** 「事件规则」分页签：内置规则、覆盖与用户规则，切换档位直接 `PUT` 完整内容。 */
export function RuleList() {
  const queryClient = useQueryClient()
  const rules = useQuery(triggerRulesQuery)
  const specs = useQuery(triggerSpecsQuery)
  const [confirm, confirmElement] = useConfirm()
  const [busyId, setBusyId] = useState<string | null>(null)
  const [editor, setEditor] = useState<{ rule: TriggerRule | null } | null>(null)

  const items = rules.data?.items ?? []
  const specItems = specs.data ?? []

  const changeMode = async (rule: TriggerRule, mode: TriggerMode) => {
    if (mode === ruleMode(rule)) return
    setBusyId(rule.id)
    try {
      await saveTriggerRule(rule.id, ruleWrite(rule, mode))
      toast.success('已保存规则')
    } catch (caught) {
      toast.error(describeTriggerError(caught))
    } finally {
      setBusyId(null)
      await invalidateTriggerRules(queryClient)
    }
  }

  const restore = async (rule: TriggerRule) => {
    setBusyId(rule.id)
    try {
      await deleteTriggerRule(rule.id)
      toast.success('已恢复默认')
    } catch (caught) {
      toast.error(describeTriggerError(caught))
    } finally {
      setBusyId(null)
      await invalidateTriggerRules(queryClient)
    }
  }

  const remove = async (rule: TriggerRule) => {
    const ok = await confirm({
      title: '删除规则？',
      description: '已经建的工单不受影响。',
      confirmText: '删除',
      tone: 'danger',
    })
    if (!ok) return
    setBusyId(rule.id)
    try {
      await deleteTriggerRule(rule.id)
      toast.success('已删除规则')
    } catch (caught) {
      toast.error(describeTriggerError(caught))
    } finally {
      setBusyId(null)
      await invalidateTriggerRules(queryClient)
    }
  }

  let content
  if (rules.isPending) {
    content = <LoadingState label="正在读取事件规则…" />
  } else if (rules.isError) {
    content = (
      <div className={card}>
        <ErrorState
          title="规则读取失败"
          message={toApiError(rules.error).message}
          onRetry={() => void rules.refetch()}
          retrying={rules.isFetching}
        />
      </div>
    )
  } else if (items.length === 0) {
    content = (
      <div className={card}>
        <EmptyState icon={Radar} title="还没有规则" description="新建一条规则，决定事件怎么处理" />
      </div>
    )
  } else {
    content = (
      <ul className="space-y-3">
        {items.map((rule) => (
          <RuleRow
            key={rule.id}
            rule={rule}
            workerLabel={workerTitle(rule.worker, specItems)}
            busy={busyId === rule.id}
            disabled={busyId !== null}
            onMode={(mode) => void changeMode(rule, mode)}
            onEdit={() => setEditor({ rule })}
            onRestore={() => void restore(rule)}
            onDelete={() => void remove(rule)}
          />
        ))}
      </ul>
    )
  }

  return (
    <Section
      title="规则"
      hint="内置规则随服务发布；改过的内置规则可以恢复默认。档位切换会立即保存。"
      actions={(
        <Button type="button" variant="soft" size="sm" onClick={() => setEditor({ rule: null })}>
          <Plus aria-hidden="true" />
          新建规则
        </Button>
      )}
    >
      {content}
      {editor ? (
        <RuleEditor
          key={editor.rule?.id ?? 'new'}
          rule={editor.rule}
          onClose={() => setEditor(null)}
        />
      ) : null}
      {confirmElement}
    </Section>
  )
}
