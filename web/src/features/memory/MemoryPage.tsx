import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain } from 'lucide-react'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { Textarea } from '@/components/ui/textarea'
import { toast } from '@/components/ui/toaster'
import {
  KIND_LABELS,
  deleteMemory,
  describeMemoryError,
  formatMemoryTime,
  fromDateTimeLocal,
  groupMemories,
  invalidateMemories,
  memoryListQuery,
  reviewMemory,
  statusBadge,
  toDateTimeLocal,
  updateMemory,
  type MemoryItem,
} from './api'

const card = 'rounded-xl border bg-card shadow-card'

interface PendingCardProps {
  memory: MemoryItem
  /** 这条记忆要取代的旧记忆；有值时旧内容加删除线（规格 §4.5）。 */
  old: MemoryItem | undefined
  busy: boolean
  /** 非 null 表示正在「改后批准」，值是编辑中的正文。 */
  draft: string | null
  onDraft: (content: string | null) => void
  onApprove: (content?: string) => void
  onReject: () => void
}

function PendingCard({ memory, old, busy, draft, onDraft, onApprove, onReject }: PendingCardProps) {
  return (
    <li data-memory-id={memory.id} data-memory-status="proposed" className={`${card} space-y-2 p-4`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="warning">{KIND_LABELS[memory.kind]}</Badge>
        {memory.subject ? <Badge variant="outline">{memory.subject}</Badge> : null}
        <span className="text-footnote text-muted-foreground">
          提议于 {formatMemoryTime(memory.created_at)}
        </span>
      </div>
      {draft === null ? (
        <p className="whitespace-pre-wrap break-words text-sm">{memory.content}</p>
      ) : (
        <Field label="改写后批准">
          <Textarea
            aria-label="改写后的记忆内容"
            value={draft}
            maxLength={500}
            onChange={(event) => onDraft(event.target.value)}
          />
        </Field>
      )}
      {old ? (
        <div className="space-y-1 rounded-lg bg-surface-2 px-3 py-2">
          <p className="text-footnote font-medium text-muted-foreground">这条会取代：</p>
          <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground line-through">
            {old.content}
          </p>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {draft === null ? (
          <>
            <Button size="sm" disabled={busy} onClick={() => onApprove()}>
              批准
            </Button>
            <Button size="sm" variant="soft" disabled={busy} onClick={() => onDraft(memory.content)}>
              改后批准
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={onReject}>
              拒绝
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" disabled={busy || draft.trim().length === 0} onClick={() => onApprove(draft.trim())}>
              确认批准
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => onDraft(null)}>
              取消
            </Button>
          </>
        )}
        {busy ? <Spinner className="text-muted-foreground" /> : null}
      </div>
    </li>
  )
}

interface ActiveCardProps {
  memory: MemoryItem
  busy: boolean
  editing: { content: string; expiresAt: string } | null
  onEdit: () => void
  onCancel: () => void
  onEditingChange: (next: { content: string; expiresAt: string }) => void
  onSave: () => void
  onDelete: () => void
}

function ActiveCard({ memory, busy, editing, onEdit, onCancel, onEditingChange, onSave, onDelete }: ActiveCardProps) {
  return (
    <li data-memory-id={memory.id} data-memory-status="active" className={`${card} space-y-2 p-4`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="success">{KIND_LABELS[memory.kind]}</Badge>
        {memory.subject ? <Badge variant="outline">{memory.subject}</Badge> : null}
        <span className="text-footnote text-muted-foreground">
          {memory.expires_at ? `到期 ${formatMemoryTime(memory.expires_at)}` : '长期有效'}
          {memory.use_count > 0 ? ` · 用过 ${memory.use_count} 次` : ''}
        </span>
      </div>
      {editing ? (
        <div className="space-y-3">
          <Field label="内容">
            <Textarea
              aria-label="记忆内容"
              value={editing.content}
              maxLength={500}
              onChange={(event) => onEditingChange({ ...editing, content: event.target.value })}
            />
          </Field>
          <Field label="过期时间" description="留空表示长期有效">
            <Input
              type="datetime-local"
              aria-label="过期时间"
              value={editing.expiresAt}
              onChange={(event) => onEditingChange({ ...editing, expiresAt: event.target.value })}
            />
          </Field>
        </div>
      ) : (
        <p className="whitespace-pre-wrap break-words text-sm">{memory.content}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {editing ? (
          <>
            <Button size="sm" disabled={busy || editing.content.trim().length === 0} onClick={onSave}>
              保存
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={onCancel}>
              取消
            </Button>
          </>
        ) : (
          <Button size="sm" variant="soft" disabled={busy} onClick={onEdit}>
            编辑
          </Button>
        )}
        <Button size="sm" variant="outline" disabled={busy} onClick={onDelete}>
          删除
        </Button>
        {busy ? <Spinner className="text-muted-foreground" /> : null}
      </div>
    </li>
  )
}

function ArchivedCard({ memory, busy, onDelete }: { memory: MemoryItem; busy: boolean; onDelete: () => void }) {
  const badge = statusBadge(memory.status)
  return (
    <li data-memory-id={memory.id} data-memory-status={memory.status} className={`${card} space-y-2 p-4`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={badge.tone}>{badge.label}</Badge>
        <Badge variant="outline">{KIND_LABELS[memory.kind]}</Badge>
        {memory.subject ? <Badge variant="outline">{memory.subject}</Badge> : null}
        <span className="text-footnote text-muted-foreground">
          更新于 {formatMemoryTime(memory.updated_at)}
        </span>
      </div>
      <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{memory.content}</p>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={busy} onClick={onDelete}>
          删除
        </Button>
        {busy ? <Spinner className="text-muted-foreground" /> : null}
      </div>
    </li>
  )
}

/**
 * 「更多 › 记忆」（M79 规格 §4.6）：待审核、生效中、已归档三组。
 *
 * 待审核的操作与待你处理相同（批准 / 改后批准 / 拒绝），都调 `review_memory`；
 * 生效中的行可以改内容与过期时间，也可以删除；已归档只展示与删除。列表一次读
 * 全部状态，客户端分组；`memory_proposed` 会让本页查询失效。
 */
export default function MemoryPage() {
  const queryClient = useQueryClient()
  const memories = useQuery(memoryListQuery)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [draft, setDraft] = useState<{ id: string; content: string } | null>(null)
  const [editing, setEditing] = useState<{ id: string; content: string; expiresAt: string } | null>(null)
  const [confirm, confirmDialog] = useConfirm()

  const items = memories.data ?? []
  const groups = groupMemories(items)
  const byId = new Map(items.map((item) => [item.id, item]))

  const run = async (memory: MemoryItem, action: () => Promise<unknown>, success: string) => {
    setBusyId(memory.id)
    try {
      await action()
      toast.success(success)
    } catch (caught) {
      toast.error(`操作失败：${describeMemoryError(caught)}`)
    } finally {
      setBusyId(null)
      setDraft(null)
      setEditing(null)
      await invalidateMemories(queryClient)
    }
  }

  const approve = (memory: MemoryItem, content?: string) =>
    void run(
      memory,
      () => reviewMemory(memory.id, content ? { approved: true, content } : { approved: true }),
      '已批准这条记忆',
    )

  const reject = (memory: MemoryItem) =>
    void run(memory, () => reviewMemory(memory.id, { approved: false }), '已拒绝这条记忆')

  const save = (memory: MemoryItem) => {
    if (!editing || editing.id !== memory.id) return
    void run(
      memory,
      () =>
        updateMemory(memory.id, {
          content: editing.content.trim(),
          expires_at: fromDateTimeLocal(editing.expiresAt),
        }),
      '已保存记忆',
    )
  }

  const remove = async (memory: MemoryItem) => {
    const ok = await confirm({
      title: '删除这条记忆？',
      description: '删除后不会再注入，也无法恢复。',
      confirmText: '删除',
      tone: 'danger',
    })
    if (!ok) return
    await run(memory, () => deleteMemory(memory.id), '已删除这条记忆')
  }

  let content
  if (memories.isPending) {
    content = <LoadingState label="正在读取记忆…" />
  } else if (memories.isError) {
    content = (
      <div className={card}>
        <ErrorState
          title="记忆读取失败"
          message={describeMemoryError(memories.error)}
          onRetry={() => void memories.refetch()}
          retrying={memories.isFetching}
        />
      </div>
    )
  } else {
    content = (
      <div className="space-y-5 pb-4">
        <Section
          title="待审核"
          description={`${groups.pending.length} 条`}
          hint="Agent 从对话里整理出的候选，批准后才会生效。"
        >
          {groups.pending.length === 0 ? (
            <div className={card}>
              <EmptyState
                icon={Brain}
                title="没有待审核的记忆"
                description="工单结束后，Agent 会把值得记住的事提交到这里"
              />
            </div>
          ) : (
            <ul className="space-y-3">
              {groups.pending.map((memory) => (
                <PendingCard
                  key={memory.id}
                  memory={memory}
                  old={memory.supersedes_id ? byId.get(memory.supersedes_id) : undefined}
                  busy={busyId === memory.id}
                  draft={draft?.id === memory.id ? draft.content : null}
                  onDraft={(next) =>
                    setDraft(next === null ? null : { id: memory.id, content: next })
                  }
                  onApprove={(edited) => approve(memory, edited)}
                  onReject={() => reject(memory)}
                />
              ))}
            </ul>
          )}
        </Section>

        <Section
          title="生效中"
          description={`${groups.active.length} 条`}
          hint="按会话注入总控与 Worker 的 system 提示词。"
        >
          {groups.active.length === 0 ? (
            <div className={card}>
              <EmptyState title="还没有生效的记忆" description="批准之后的记忆会出现在这里" />
            </div>
          ) : (
            <ul className="space-y-3">
              {groups.active.map((memory) => (
                <ActiveCard
                  key={memory.id}
                  memory={memory}
                  busy={busyId === memory.id}
                  editing={editing?.id === memory.id ? editing : null}
                  onEdit={() =>
                    setEditing({
                      id: memory.id,
                      content: memory.content,
                      expiresAt: toDateTimeLocal(memory.expires_at),
                    })
                  }
                  onCancel={() => setEditing(null)}
                  onEditingChange={(next) => setEditing({ id: memory.id, ...next })}
                  onSave={() => save(memory)}
                  onDelete={() => void remove(memory)}
                />
              ))}
            </ul>
          )}
        </Section>

        <Section
          title="已归档"
          description={`${groups.archived.length} 条`}
          hint="被拒绝、取代或过期的记忆；不会注入，可以删除。"
        >
          {groups.archived.length === 0 ? (
            <div className={card}>
              <EmptyState title="没有归档的记忆" description="被拒绝、取代或过期的记忆会留在这里" />
            </div>
          ) : (
            <ul className="space-y-3">
              {groups.archived.map((memory) => (
                <ArchivedCard
                  key={memory.id}
                  memory={memory}
                  busy={busyId === memory.id}
                  onDelete={() => void remove(memory)}
                />
              ))}
            </ul>
          )}
        </Section>
      </div>
    )
  }

  return (
    <Page title="记忆" back="/more">
      {content}
      {confirmDialog}
    </Page>
  )
}
