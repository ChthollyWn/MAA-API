import { Badge } from '@/components/ui/badge'
import { ListGroup, ListItem } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { EmptyState } from '@/components/ui/states'
import { formatWhen, runDetail, runStatusLabel, runTone } from './format'
import type { ScheduleRun } from './payload'

const MAX_RUNS = 5

/** 已有定时任务的最近运行：时间、结果、错误；有流水线的行链到它的详情。 */
export function ScheduleRuns({ runs }: { runs: readonly ScheduleRun[] }) {
  return (
    <Section id="schedule-runs" title="最近运行">
      {runs.length === 0 ? (
        <div className="rounded-xl border bg-card shadow-card">
          <EmptyState className="py-7" title="还没有运行记录" description="到点或手动运行后，结果会出现在这里。" />
        </div>
      ) : (
        <ListGroup>
          {runs.slice(0, MAX_RUNS).map((run, index) => {
            const detail = runDetail(run)
            return (
              <ListItem
                key={run.pipeline_id ?? `${run.started_at ?? 'run'}-${index}`}
                to={run.pipeline_id ? `/tasks/history/${encodeURIComponent(run.pipeline_id)}` : undefined}
                title={<span className="tabular-nums">{formatWhen(run.finished_at ?? run.started_at)}</span>}
                description={detail
                  ? <span className={detail.tone === 'danger' ? 'break-words text-danger' : 'break-words'}>{detail.text}</span>
                  : run.pipeline_id ? '查看流水线详情' : undefined}
                trailing={<Badge variant={runTone(run.status)}>{runStatusLabel(run.status)}</Badge>}
              />
            )
          })}
        </ListGroup>
      )}
    </Section>
  )
}
