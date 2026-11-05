import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Plus, Target } from 'lucide-react'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ListGroup, ListItem } from '@/components/ui/list'
import { SegmentedControl, type SegmentedOption } from '@/components/ui/segmented-control'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { toApiError } from '@/api/errors'
import type { GoalStatus } from './api'
import {
  deadlineText,
  goalStatusBadge,
  milestoneProgressText,
  nextReviewText,
} from './format'
import { GoalCreateDialog } from './GoalCreateDialog'
import { goalsInfiniteQuery } from './queries'

type GoalsTab = 'active' | 'paused' | 'ended'

const TAB_OPTIONS: readonly SegmentedOption<GoalsTab>[] = [
  { value: 'active', label: '进行中' },
  { value: 'paused', label: '已暂停' },
  { value: 'ended', label: '已结束' },
]

/** 「已结束」= done、failed、cancelled；与 §4.6 的三个分页签一致。 */
const TAB_STATUSES: Record<GoalsTab, readonly GoalStatus[]> = {
  active: ['active'],
  paused: ['paused'],
  ended: ['done', 'failed', 'cancelled'],
}

const card = 'rounded-xl border bg-card shadow-card'

/**
 * 「更多 › 目标」（M82 §4.6）：进行中、已暂停、已结束三个分页签。
 *
 * 每行给标题、状态、截止、里程碑「已完成 n / 共 m」与下次复盘时间；右上角
 * 的「新建目标」与空状态里的按钮打开同一个弹层。
 */
export default function GoalsPage() {
  const [tab, setTab] = useState<GoalsTab>('active')
  const [creating, setCreating] = useState(false)
  const goals = useInfiniteQuery(goalsInfiniteQuery(TAB_STATUSES[tab]))
  const items = goals.data?.pages.flatMap((page) => page.items) ?? []
  const total = goals.data?.pages.at(-1)?.total ?? items.length

  const toolbar = (
    <SegmentedControl
      ariaLabel="目标分页"
      value={tab}
      onValueChange={setTab}
      options={TAB_OPTIONS}
      fullWidth
    />
  )

  const createButton = (
    <Button size="sm" onClick={() => setCreating(true)}>
      <Plus aria-hidden="true" />
      新建目标
    </Button>
  )

  let content
  if (goals.isPending) {
    content = <div className={card}><LoadingState label="正在读取目标…" /></div>
  } else if (goals.error && items.length === 0) {
    content = (
      <div className={card}>
        <ErrorState
          title="目标读取失败"
          message={toApiError(goals.error).message}
          onRetry={() => void goals.refetch()}
          retrying={goals.isFetching}
        />
      </div>
    )
  } else if (items.length === 0) {
    content = (
      <div className={card}>
        <EmptyState
          icon={Target}
          title="还没有目标"
          description="在总控里说一句「月底前把某某练到精二」也能建"
          action={<Button size="sm" onClick={() => setCreating(true)}>新建目标</Button>}
        />
      </div>
    )
  } else {
    content = (
      <>
        <ListGroup>
          {items.map((goal) => {
            const badge = goalStatusBadge(goal.status)
            return (
              <ListItem
                key={goal.id}
                to={`/more/goals/${goal.id}`}
                title={goal.title}
                description={[
                  `截止 ${deadlineText(goal.deadline)}`,
                  `里程碑 ${milestoneProgressText(goal.milestones_done, goal.milestones_total)}`,
                  `下次复盘 ${nextReviewText(goal.next_review_at)}`,
                ].join(' · ')}
                trailing={<Badge variant={badge.tone}>{badge.label}</Badge>}
              />
            )
          })}
        </ListGroup>
        {goals.hasNextPage ? (
          <Button
            variant="outline"
            className="mt-3 w-full"
            loading={goals.isFetchingNextPage}
            onClick={() => void goals.fetchNextPage()}
          >
            {goals.isFetchingNextPage ? '正在加载…' : '加载更多'}
          </Button>
        ) : (
          <p className="mt-3 text-center text-footnote text-muted-foreground">已显示全部 {total} 个目标</p>
        )}
      </>
    )
  }

  return (
    <Page title="目标" back="/more" toolbar={toolbar} actions={createButton}>
      <div className="pb-4">
        {content}
      </div>
      {creating ? <GoalCreateDialog onClose={() => setCreating(false)} /> : null}
    </Page>
  )
}
