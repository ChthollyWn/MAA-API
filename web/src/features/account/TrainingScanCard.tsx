import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Progress } from '@/components/ui/progress'
import { Section } from '@/components/ui/section'
import { Spinner } from '@/components/ui/states'
import {
  accountKeys,
  cancelTrainingScan,
  fetchTrainingScan,
  startTrainingScan,
  type TrainingScanState,
  type TrainingScanStatus,
} from './api'
import { errorText, formatDateTime } from './format'

/**
 * 「练度扫描」卡片（M36 规格 §4.8）：读干员详情页的技能等级、专精与模组，补上内核
 * `OperBox` 给不出的三列。
 *
 * 与 `RecognitionStatus.tsx` 头注释里那条旧裁决（「这一页不提供刷新识别按钮」）的界限：
 * 那条只约束**干员识别**——识别是任务里的一个步骤，账号页不给它单独的入口。练度扫描是
 * **另一种数据**（技能等级 / 专精 / 模组），有自己的提交与取消端点，所以这一页可以给它
 * 一个「开始扫描」按钮；它只读界面，不消耗理智、不点升级入口。`RecognitionStatus.tsx`
 * 本身不改，两处文案也刻意不写「刷新识别」。
 */

/**
 * 设备占用与取消口径的一句提示（§5、§4.9）：扫描期间在设备上手动操作会打断识别，
 * 所以这句话要一直显示，不只在跑的时候。M37 起手会先跑一条 `StartUp`（拉起客户端、
 * 等在主界面），这一句也得说清楚——不然用户会以为设备没被碰过。
 */
const BUSY_HINT =
  '开始前会先把游戏客户端拉起来；扫描是一条普通流水线，会与任务排队，不抢内核；扫描期间别在设备上手动操作。'

/** 状态短标签；`idle` 为 `null`（卡片标题已经说明这是什么，不必再写一行）。 */
const STATUS_LABEL: Record<TrainingScanStatus, string | null> = {
  idle: null,
  running: '进行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const STATUS_TEXT_CLASS: Record<TrainingScanStatus, string> = {
  idle: 'font-medium',
  running: 'font-medium',
  succeeded: 'font-medium',
  failed: 'font-medium text-danger',
  cancelled: 'font-medium',
}

/** 「已读 x / y」的 `y`：`total` 已知就带上，未知只写已读数。 */
export function scannedText(scan: TrainingScanState, total: number | null): string {
  return typeof total === 'number' && total > 0 ? `已读 ${scan.scanned} / ${total}` : `已读 ${scan.scanned}`
}

/**
 * 上次结果摘要：`updated`/`scanned` 与跳过的原因，`skipped`/`unresolved`/`mismatched`
 * 为 0 时不提（§4.8）。
 */
export function trainingScanSummary(scan: TrainingScanState): string[] {
  const lines = [`写入 ${scan.updated} 名 / 共读 ${scan.scanned} 名`]
  const skipped = [
    scan.skipped > 0 ? `跳过 ${scan.skipped} 名` : null,
    scan.unresolved > 0 ? `名单未解析 ${scan.unresolved} 名` : null,
    scan.mismatched > 0 ? `卡名不符 ${scan.mismatched} 名` : null,
  ].filter((item): item is string => item !== null)
  if (skipped.length) lines.push(skipped.join(' · '))
  return lines
}

/**
 * 页面上要显示的两件事：服务器状态 + 本轮目标人数。
 *
 * `total` 不在 `GET`/`DELETE` 的契约里（`POST` 才返回 `total`），所以开始扫描后先用
 * 受理结果里的 `total` 显示「已读 x / y」，轮询回来带 `total` 就用后端的，没带就只写
 * 「已读 x」——不拿别的字段假冒总数。
 */
export function trainingScanView(scan: TrainingScanState | undefined, submittedTotal: number | null) {
  const total = typeof scan?.total === 'number' && scan.total > 0 ? scan.total : submittedTotal
  return { status: scan?.status ?? ('idle' as TrainingScanStatus), total, scan: scan ?? null }
}

/**
 * 账号页的「练度扫描」入口（M36 §4.8）。
 *
 * 三个端点的口径见 `api.ts`：`GET` 读状态、`POST` 提交（非阻塞，409 ＝ 已有扫描在跑）、
 * `DELETE` 取消。跑着的时候每 5 s 轮询一次状态，其余状态不轮询。
 */
export function TrainingScanCard() {
  const queryClient = useQueryClient()
  const scan = useQuery({
    queryKey: accountKeys.trainingScan(),
    queryFn: ({ signal }) => fetchTrainingScan(signal),
    retry: false,
    // 只在跑的时候轮询，其余状态不轮询（照 `web/src/features/tasks/history/PipelineDetail.tsx:95`）。
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 5_000 : false),
  })

  const start = useMutation({
    mutationFn: () => startTrainingScan({}),
    onSuccess: async (submitted) => {
      // 受理结果里带 `total`：先把它放进缓存，第一次轮询回来之前也能显示「已读 0 / y」。
      queryClient.setQueryData<TrainingScanState>(accountKeys.trainingScan(), {
        status: 'running',
        pipeline_id: submitted.pipeline_id,
        started_at: null,
        finished_at: null,
        scanned: 0,
        updated: 0,
        skipped: 0,
        unresolved: 0,
        mismatched: 0,
        message: null,
        total: submitted.total,
      })
      await queryClient.invalidateQueries({ queryKey: accountKeys.trainingScan() })
    },
  })

  const cancel = useMutation({
    mutationFn: () => cancelTrainingScan(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: accountKeys.trainingScan() })
    },
  })

  const data = scan.data
  const view = trainingScanView(data, start.data?.total ?? null)
  const status = view.status
  const running = status === 'running'
  const startedAt = formatDateTime(data?.started_at)
  const finished = Boolean(data?.finished_at)
  // 有结果就往摘要上走：跑着（`scanned` 会变）、有完成时间、或已经读到过东西。
  const showSummary = Boolean(data) && (running || finished || (data?.scanned ?? 0) > 0)
  const actionError = start.error ?? cancel.error ?? scan.error

  return (
    <Section
      title="练度扫描"
      id="training-scan"
      description="读干员详情页的技能等级、专精与模组；只读界面，不消耗资源。"
      actions={(
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant="soft"
            size="sm"
            className="shrink-0"
            loading={start.isPending}
            disabled={running}
            onClick={() => start.mutate()}
          >
            {start.isPending ? '正在提交…' : '开始扫描'}
          </Button>
          {/* 只有跑着的时候才有可取消的东西（`DELETE` 转 `request_cancel`）。 */}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            loading={cancel.isPending}
            disabled={!running}
            onClick={() => cancel.mutate()}
          >
            {cancel.isPending ? '正在取消…' : '取消'}
          </Button>
        </div>
      )}
    >
      <div className="space-y-2 rounded-xl border bg-card p-3.5 shadow-card">
        {!data && scan.isPending ? (
          <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
            <Spinner />
            正在读取扫描状态…
          </p>
        ) : (
          <>
            <p className="flex flex-wrap items-center gap-2 text-sm">
              {STATUS_LABEL[status] ? (
                <span data-testid="training-scan-status" className={STATUS_TEXT_CLASS[status]}>
                  {STATUS_LABEL[status]}
                </span>
              ) : null}
              <span className="tabular-nums text-muted-foreground">
                {data && (running || finished || data.scanned > 0)
                  ? scannedText(data, view.total)
                  : '尚未扫描过'}
              </span>
            </p>

            {running ? (
              <Progress
                value={data?.scanned ?? null}
                // `total` 未知或为 0 时省略 `max`，进度条走不确定态（`Progress` 的兜底）。
                max={typeof view.total === 'number' && view.total > 0 ? view.total : undefined}
                label="练度扫描进度"
              />
            ) : null}

            {showSummary && data
              ? trainingScanSummary(data).map((line) => (
                <p key={line} className="text-footnote tabular-nums text-muted-foreground">
                  {line}
                </p>
              ))
              : null}

            {running && startedAt ? (
              <p className="text-footnote tabular-nums text-muted-foreground">开始于 {startedAt}</p>
            ) : null}

            {status === 'failed' && data?.message ? (
              <InlineAlert tone="danger" title="扫描失败">
                {data.message}
              </InlineAlert>
            ) : null}

            {status === 'cancelled' ? (
              <InlineAlert tone="warning" role="status">
                扫描已取消；已经读到的练度保留在账号数据里，需要时再扫一次补齐剩下的。
              </InlineAlert>
            ) : null}

            {/* 提交/取消的失败就地显示，不吞掉：409 的「已有练度扫描在跑」也走这里。 */}
            {actionError ? (
              <InlineAlert tone="danger" title={start.error ? '开始扫描失败' : '取消扫描失败'}>
                {errorText(actionError)}
              </InlineAlert>
            ) : null}

            <p className="text-footnote text-muted-foreground">{BUSY_HINT}</p>
          </>
        )}
      </div>
    </Section>
  )
}

export default TrainingScanCard
