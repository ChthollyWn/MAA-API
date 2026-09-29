import { useState } from 'react'
import { Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { ListGroup } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { Switch } from '@/components/ui/switch'
import { toast } from '@/components/ui/toaster'
import { invalidateKnowledge, knowledgeListQuery, setKnowledgeEnabled, type KnowledgeSummary } from './api'
import {
  FULLTEXT_KINDS,
  MAX_ENABLED_ENTRIES,
  MAX_FULLTEXT_CHARS,
  NEW_KNOWLEDGE_PATH,
  describeKnowledgeError,
  groupByKind,
  knowledgeEntryPath,
  sourceBadge,
  type KnowledgeKind,
} from './format'

const card = 'rounded-xl border bg-card shadow-card'

function EntryRow({
  entry,
  busy,
  disabled,
  onToggle,
}: {
  entry: KnowledgeSummary
  busy: boolean
  disabled: boolean
  onToggle: (enabled: boolean) => void
}) {
  const badge = sourceBadge(entry)
  return (
    <div className="flex items-center gap-1 pr-1.5">
      <Link
        to={knowledgeEntryPath(entry.id)}
        className="flex min-h-14 min-w-0 flex-1 items-center gap-3 py-3 pl-4 pr-1 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[0.9375rem] font-medium leading-5">{entry.title}</span>
          <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-footnote text-muted-foreground">
            <code className="truncate font-mono">{entry.id}</code>
            <Badge variant={badge.tone} title={badge.description}>{badge.label}</Badge>
            {!entry.enabled ? <span>已停用</span> : null}
          </span>
        </span>
      </Link>
      {busy ? <Spinner className="text-muted-foreground" /> : null}
      <Switch
        aria-label={`启用「${entry.title}」`}
        checked={entry.enabled}
        disabled={disabled}
        onCheckedChange={onToggle}
        className="mx-2"
      />
    </div>
  )
}

/**
 * 「设置 › Agent 知识库」的列表：按类型分组，每行一条，开关直接整条回写。
 * 删除与恢复默认在编辑页里做，避免开关旁边挨着破坏性操作。
 */
export function KnowledgeSection() {
  const queryClient = useQueryClient()
  const knowledge = useQuery(knowledgeListQuery)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const entries = knowledge.data ?? []
  const enabledCount = entries.filter((entry) => entry.enabled).length
  const fulltextChars = entries
    .filter((entry) => entry.enabled && FULLTEXT_KINDS.includes(entry.kind as KnowledgeKind))
    .reduce((sum, entry) => sum + entry.body_chars, 0)

  const toggle = async (entry: KnowledgeSummary, enabled: boolean) => {
    setBusyId(entry.id)
    setError(null)
    try {
      await setKnowledgeEnabled(entry.id, enabled)
      toast.success(enabled ? `已启用「${entry.title}」` : `已停用「${entry.title}」`)
    } catch (caught) {
      setError(`无法${enabled ? '启用' : '停用'}「${entry.title}」：${describeKnowledgeError(caught)}`)
    } finally {
      setBusyId(null)
      await invalidateKnowledge(queryClient, entry.id)
    }
  }

  let content
  if (knowledge.isPending) {
    content = <LoadingState label="正在读取知识库…" />
  } else if (knowledge.isError) {
    content = (
      <div className={card}>
        <ErrorState
          title="知识库读取失败"
          message={describeKnowledgeError(knowledge.error)}
          onRetry={() => void knowledge.refetch()}
          retrying={knowledge.isFetching}
        />
      </div>
    )
  } else if (entries.length === 0) {
    content = (
      <div className={card}>
        <EmptyState
          icon={BookOpen}
          title="还没有知识条目"
          description="内置条目随服务发布；也可以自己写一条操作剧本、界面知识或用户档案。"
          action={<Button asChild><Link to={NEW_KNOWLEDGE_PATH}><Plus aria-hidden="true" />新建条目</Link></Button>}
        />
      </div>
    )
  } else {
    content = groupByKind(entries).map((group) => (
      <ListGroup key={group.kind} title={group.label} headingLevel="h3">
        {group.items.map((entry) => (
          <EntryRow
            key={entry.id}
            entry={entry}
            busy={busyId === entry.id}
            disabled={busyId !== null}
            onToggle={(enabled) => void toggle(entry, enabled)}
          />
        ))}
      </ListGroup>
    ))
  }

  return (
    <div className="space-y-4 pb-4">
      {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}
      <Section
        title="知识条目"
        description="Agent 每轮都会带上这份索引，正文由它在需要时用 read_knowledge 读取。"
        actions={entries.length ? (
          <Button asChild variant="soft" size="sm">
            <Link to={NEW_KNOWLEDGE_PATH}><Plus aria-hidden="true" />新建</Link>
          </Button>
        ) : null}
      >
        {content}
      </Section>
      {entries.length > 0 ? (
        <p className="px-1 text-footnote text-muted-foreground">
          已启用 {enabledCount} / {MAX_ENABLED_ENTRIES} 条 · 术语表与用户档案正文合计 {fulltextChars} / {MAX_FULLTEXT_CHARS} 字
        </p>
      ) : null}
    </div>
  )
}
