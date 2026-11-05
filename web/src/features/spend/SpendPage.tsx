import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { ListGroup } from '@/components/ui/list'
import { Progress } from '@/components/ui/progress'
import { Section } from '@/components/ui/section'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { ErrorState, LoadingState } from '@/components/ui/states'
import { toApiError } from '@/api/errors'
import {
  SPEND_RANGE_DAYS,
  spendBlockText,
  spendQueryOptions,
  spendSinceDate,
  spendUsageText,
  spendUsageTone,
  type SpendRange,
} from './api'

const HINT = '按任务参数里的上限计算，实际可能用得更少。只限制代理与定时任务，手动提交不受限但计入用量。每天 04:00 重新计算。'

const RANGE_OPTIONS = [
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
] as const satisfies readonly { value: SpendRange; label: string }[]

/** 按日表格的列：日期之外三种资源，和后端 `days` 的字段一一对应。 */
const DAY_COLUMNS = [
  { key: 'medicine', label: '理智药' },
  { key: 'stone', label: '源石' },
  { key: 'expedite', label: '加急许可' },
] as const

const card = 'rounded-xl border bg-card shadow-card'

/**
 * 「更多 › 消耗护栏」（M84 §4.6）。
 *
 * 只读页：今日三项用量（有上限时画进度条）、可切近 7 / 30 天的按日表格、
 * 今天被上限拦下的提交（为空时不显示这一节）与去设置页改上限的入口。
 * 消耗日按北京时间 04:00 切分，数据每 60 秒重取一次。
 */
export default function SpendPage() {
  const [range, setRange] = useState<SpendRange>('7d')
  // 近 7 天不传 since，走接口默认值；近 30 天显式给起始消耗日。
  const since = useMemo(
    () => (range === '7d' ? undefined : spendSinceDate(SPEND_RANGE_DAYS[range])),
    [range],
  )
  const spend = useQuery(spendQueryOptions(since))
  const summary = spend.data

  if (spend.isPending) {
    return (
      <Page title="消耗护栏" back="/more">
        <div className={card}><LoadingState label="正在读取消耗…" /></div>
      </Page>
    )
  }
  if (!summary) {
    return (
      <Page title="消耗护栏" back="/more">
        <div className={card}>
          <ErrorState
            title="消耗汇总读取失败"
            message={`读取消耗汇总失败：${toApiError(spend.error).message}`}
            onRetry={() => void spend.refetch()}
            retrying={spend.isFetching}
          />
        </div>
      </Page>
    )
  }

  const blocked = summary.blocked_today
  // 后端按日期升序给出连续的日子；表格倒过来，今天在最上面。
  const days = [...summary.days].reverse()

  return (
    <Page title="消耗护栏" back="/more">
      <div className="space-y-6 pb-4">
        <Section title="今日用量" hint={HINT}>
          <ListGroup>
            {summary.today.map((item) => (
              <div key={item.kind} data-spend-kind={item.kind} className="space-y-1.5 px-4 py-3">
                <p className="text-sm font-medium tabular-nums">{`${item.label} ${spendUsageText(item)}`}</p>
                {/* 不限（limit=-1）不画进度条，避免把「不限」显示成满格。 */}
                {item.limit < 0 ? null : (
                  <Progress
                    value={item.used}
                    max={item.limit > 0 ? item.limit : 1}
                    label={`${item.label}用量`}
                    tone={spendUsageTone(item)}
                  />
                )}
              </div>
            ))}
          </ListGroup>
        </Section>

        <Section
          title="按日用量"
          actions={(
            <SegmentedControl
              ariaLabel="时间范围"
              value={range}
              onValueChange={setRange}
              options={RANGE_OPTIONS}
              size="sm"
            />
          )}
        >
          <div className="overflow-x-auto rounded-xl border bg-card shadow-card">
            <table aria-label="按日用量" className="w-full min-w-[22rem] text-sm">
              <thead className="bg-surface-2/60 text-footnote text-muted-foreground">
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-medium">日期</th>
                  {DAY_COLUMNS.map((column) => (
                    <th key={column.key} scope="col" className="px-3 py-2 text-right font-medium">{column.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border tabular-nums">
                {days.map((day) => (
                  <tr key={day.date} data-spend-day={day.date}>
                    <th scope="row" className="px-3 py-2.5 text-left font-medium">{day.date}</th>
                    {DAY_COLUMNS.map((column) => (
                      <td key={column.key} className="px-3 py-2.5 text-right">{day[column.key]}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        {blocked.length > 0 ? (
          <Section title="今天拦下的提交">
            <ListGroup>
              {blocked.map((block, index) => (
                <p key={`${block.at}-${index}`} className="px-4 py-3 text-sm tabular-nums">
                  {spendBlockText(block)}
                </p>
              ))}
            </ListGroup>
          </Section>
        ) : null}

        <div className="flex justify-center pt-1">
          <Button asChild variant="outline">
            <Link to="/more/settings/agent">调整上限</Link>
          </Button>
        </div>
      </div>
    </Page>
  )
}
