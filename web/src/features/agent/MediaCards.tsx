import { useState } from 'react'
import { ChevronDown, ImageOff, ImageUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ImageViewer } from '@/components/ui/image-viewer'
import { Spinner } from '@/components/ui/states'
import { cn } from '@/lib/utils'
import { useAuthedImage } from '@/components/ui/use-authed-image'
import type {
  ChartCardData,
  ImageCardData,
  ItemIconsCardData,
  MediaCard,
  MediaItemIcon,
  StageMapCardData,
} from './media'
import { EChartSurface } from './chart/EChartSurface'

// ---------------------------------------------------------------------------
// 通用：带鉴权 + 懒加载的图片
// ---------------------------------------------------------------------------

function ImageFailure({ label, onRetry }: { label: string; onRetry: () => void }) {
  return (
    <div role="status" className="flex flex-col items-center justify-center gap-2 px-3 py-6 text-center text-footnote text-muted-foreground">
      <ImageOff aria-hidden="true" className="size-5" />
      <span>{label}</span>
      <Button variant="outline" size="sm" onClick={onRetry}>重试</Button>
    </div>
  )
}

/** 两个来源是不是同一个地址（内容寻址才分 thumb/full，截图记录只有一个地址）。 */
function sameSource(a: ImageCardData['thumb'], b: ImageCardData['full']): boolean {
  if (!a || !b) return false
  if (a.kind === 'screenshot' && b.kind === 'screenshot') return a.id === b.id
  if (a.kind === 'image' && b.kind === 'image') return a.sha256 === b.sha256 && a.size === b.size
  return false
}

/** 图片卡：懒加载缩略图，点击看原图（同样走 token 取 blob，不依赖 cookie）。 */
export function ImageCard({ card }: { card: ImageCardData }) {
  const [open, setOpen] = useState(false)
  const thumb = useAuthedImage(card.thumb ?? card.full)
  // 原图只在真的点开后才取；截图记录与缩略图是同一个地址时不必再取一次。
  const needsFull = !sameSource(card.thumb, card.full)
  const full = useAuthedImage(open && needsFull ? card.full : null, { lazy: false })
  const alt = card.alt || card.caption || 'Agent 发来的图片'
  return (
    <figure className="min-w-0">
      {/*
        预览按缩略图的**自然尺寸**显示（长边 320 px，与 util/image.py 的
        `_THUMBNAIL_EDGE` 对应），不再拉满卡片宽度：把 320 px 的图铺到 600+ px
        等于放大四倍，视网膜屏上就是一团糊。想看细节点开，那时取的是原图。
      */}
      <div ref={thumb.ref} className="w-fit max-w-full overflow-hidden rounded-xl border bg-surface-2">
        {thumb.url ? (
          <button
            type="button"
            aria-label={`查看大图：${alt}`}
            onClick={() => setOpen(true)}
            className="group relative block w-fit max-w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          >
            <img
              src={thumb.url}
              alt={alt}
              loading="lazy"
              decoding="async"
              className="block h-auto max-h-80 w-auto max-w-[20rem] object-contain"
            />
            <span aria-hidden="true" className="absolute right-2 top-2 grid size-7 place-items-center rounded-full bg-black/50 text-white opacity-85 transition-opacity group-hover:opacity-100">
              <ImageUp className="size-3.5" />
            </span>
          </button>
        ) : thumb.error ? (
          <ImageFailure label="图片不可用" onRetry={thumb.retry} />
        ) : (
          <div role="status" className="flex min-h-32 w-56 items-center justify-center gap-2 py-6 text-footnote text-muted-foreground">
            <Spinner />
            正在加载图片
          </div>
        )}
      </div>
      {card.caption ? (
        <figcaption className="mt-1.5 text-footnote text-muted-foreground">
          <span className="min-w-0 break-words">{card.caption}</span>
        </figcaption>
      ) : null}
      <ImageViewer
        open={open}
        onOpenChange={setOpen}
        src={full.url ?? thumb.url}
        alt={alt}
        caption={card.caption || undefined}
      />
    </figure>
  )
}

// ---------------------------------------------------------------------------
// 物品图标卡
// ---------------------------------------------------------------------------

function ItemIcon({ item, onOpen }: { item: MediaItemIcon; onOpen: (item: MediaItemIcon, url: string) => void }) {
  const image = useAuthedImage(item.icon)
  return (
    <div ref={image.ref} className="flex min-w-0 flex-col items-center gap-1">
      {image.url ? (
        <button
          type="button"
          aria-label={`查看${item.name}图标`}
          onClick={() => onOpen(item, image.url!)}
          className="grid size-12 place-items-center rounded-lg border bg-surface-2 transition-colors hover:bg-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <img src={image.url} alt={`${item.name}图标`} loading="lazy" decoding="async" className="size-9 object-contain" />
        </button>
      ) : image.error ? (
        <span role="status" title="图标不可用" className="grid size-12 place-items-center rounded-lg border bg-surface-2 text-muted-foreground">
          <ImageOff aria-hidden="true" className="size-4" />
          <span className="sr-only">{item.name}图标不可用</span>
        </span>
      ) : (
        <span className="grid size-12 place-items-center rounded-lg border bg-surface-2 text-muted-foreground">
          <Spinner />
        </span>
      )}
      <span className="w-full truncate text-center text-xs text-muted-foreground" title={item.name}>{item.name}</span>
    </div>
  )
}

/** 物品图标卡：图标 + 名称网格（1–8 项），点击看大图。 */
export function ItemIconsCard({ card }: { card: ItemIconsCardData }) {
  const [viewing, setViewing] = useState<{ name: string; url: string } | null>(null)
  return (
    <div className="min-w-0 rounded-xl border bg-card p-3">
      <ul className="grid grid-cols-4 gap-2 sm:grid-cols-8">
        {card.items.map((item) => (
          <li key={item.itemId} className="min-w-0">
            <ItemIcon item={item} onOpen={(chosen, url) => setViewing({ name: chosen.name, url })} />
          </li>
        ))}
      </ul>
      {card.caption ? <p className="mt-2 break-words text-footnote text-muted-foreground">{card.caption}</p> : null}
      <ImageViewer
        open={viewing !== null}
        onOpenChange={(open) => {
          if (!open) setViewing(null)
        }}
        src={viewing?.url ?? null}
        alt={viewing ? `${viewing.name}图标` : '物品图标'}
        caption={viewing?.name}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// 关卡示意图卡（SVG，不占 canvas）
// ---------------------------------------------------------------------------

const CELL = 18

/**
 * 行序：规格 §7 假设 1 —— 内核 tile 文件的行号从下往上（view 矩阵），所以 `tiles[0]`
 * 画在**最下方**。用一张真实关卡（1-7）的画面确认过以后，若相反只改这一个常量。
 */
const BOTTOM_UP_ROWS = true

function displayRow(row: number, height: number): number {
  return BOTTOM_UP_ROWS ? height - 1 - row : row
}

/** 地形 → 填充色；只用语义 token，暗色主题自动跟随。 */
const TERRAIN_FILL: Record<string, string> = {
  '.': 'var(--chart-3)',
  '#': 'var(--chart-1)',
  '~': 'var(--chart-5)',
  w: 'var(--chart-4)',
  '!': 'var(--chart-2)',
}

const TERRAIN_LABEL: Record<string, string> = {
  '.': '普通地面',
  '#': '阻挡',
  '~': '坑洞',
  w: '水域',
  '!': '特殊机制',
}

/** 可部署类型 → 描边；0 只留网格线，1/2/3 用同一色相的不同线型区分。 */
const DEPLOY_STROKE: Record<string, { stroke: string; width: number; dash?: string }> = {
  '0': { stroke: 'var(--border)', width: 1 },
  '1': { stroke: 'var(--chart-6)', width: 2.5 },
  '2': { stroke: 'var(--chart-6)', width: 2.5, dash: '3 2' },
  '3': { stroke: 'var(--chart-6)', width: 2.5, dash: '1 1.5' },
}

/**
 * 拆一格代码。网格里是 3 字符 `<地形><可部署><高度>`；图例里的代码是**片段**（后端
 * `stage_layout._legend`）：1 字符＝只讲地形，2 字符＝只讲「可部署+高度」。缺的部分留空，
 * 渲染时走中性兜底，这样同一个组件既能画网格也能画图例色块。
 */
function tileParts(code: string): { terrain: string; deploy: string; height: string } {
  if (code.length >= 3) return { terrain: code.slice(0, 1), deploy: code.slice(1, 2), height: code.slice(2, 3) }
  if (code.length === 2) return { terrain: '', deploy: code.slice(0, 1), height: code.slice(1, 2) }
  return { terrain: code.slice(0, 1), deploy: '', height: '' }
}

function tileTitle(code: string, row: number, column: number, height: number): string {
  const { terrain, deploy, height: elevation } = tileParts(code)
  return `画面第 ${displayRow(row, height) + 1} 行第 ${column + 1} 列：${TERRAIN_LABEL[terrain] ?? '未知地形'}，可部署类型 ${deploy}，高度类型 ${elevation}`
}

/** 一格：一个 `<rect>`（颜色/线型来自 3 字符串码）+ 高台的圆点。图例色块与网格共用它。 */
function TileShape({ code, x, y, cell }: { code: string; x: number; y: number; cell: number }) {
  const { terrain, deploy, height } = tileParts(code)
  const stroke = DEPLOY_STROKE[deploy] ?? DEPLOY_STROKE['0']!
  return (
    <>
      <rect
        x={x}
        y={y}
        width={cell}
        height={cell}
        fill={TERRAIN_FILL[terrain] ?? 'var(--surface-2)'}
        stroke={stroke.stroke}
        strokeWidth={stroke.width}
        strokeDasharray={stroke.dash}
        data-terrain={terrain}
        data-deploy={deploy}
        data-height={height}
      />
      {height === '1' ? (
        <circle data-tile-height="1" cx={x + cell / 2} cy={y + cell / 2} r={cell * 0.12} fill="var(--foreground)" opacity={0.5} />
      ) : null}
    </>
  )
}

/** 关卡示意图卡：SVG 网格 + 起点/终点标记 + 图例 + 候选提示。 */
export function StageMapCard({ card }: { card: StageMapCardData }) {
  const width = card.width * CELL
  const height = card.height * CELL
  const title = card.name || card.code || card.stageId || '关卡'
  return (
    <div className="min-w-0 rounded-xl border bg-card p-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <p className="text-sm font-medium">{title}</p>
        {card.code ? <span className="font-mono text-xs text-muted-foreground">{card.code}</span> : null}
        {card.stageId ? <span className="font-mono text-xs text-muted-foreground">{card.stageId}</span> : null}
        <span className="text-xs tabular-nums text-muted-foreground">{card.width}×{card.height}</span>
      </div>
      {card.candidates > 1 ? (
        <p className="mt-0.5 text-xs text-warning">同名关卡 {card.candidates} 个，已取 {card.stageId}</p>
      ) : null}
      <div className="mt-2 overflow-x-auto">
        <svg
          role="img"
          aria-label={`${title} 关卡布局示意图`}
          viewBox={`0 0 ${width} ${height}`}
          className="h-auto w-full max-w-md"
          data-testid="stage-map-grid"
        >
          {card.tiles.map((row, rowIndex) => {
            const y = displayRow(rowIndex, card.height)
            return row.map((code, column) => (
              <g key={`${rowIndex}-${column}`} data-cell={`${rowIndex}-${column}`}>
                <title>{tileTitle(code, rowIndex, column, card.height)}</title>
                <TileShape code={code} x={column * CELL} y={y * CELL} cell={CELL} />
              </g>
            ))
          })}
          {card.start.map(([x, y]) => (
            <circle
              key={`start-${x}-${y}`}
              data-marker="start"
              cx={(x + 0.5) * CELL}
              cy={(displayRow(y, card.height) + 0.5) * CELL}
              r={CELL * 0.3}
              fill="none"
              stroke="var(--success)"
              strokeWidth={2.5}
            />
          ))}
          {card.end.map(([x, y]) => (
            <circle
              key={`end-${x}-${y}`}
              data-marker="end"
              cx={(x + 0.5) * CELL}
              cy={(displayRow(y, card.height) + 0.5) * CELL}
              r={CELL * 0.3}
              fill="none"
              stroke="var(--danger)"
              strokeWidth={2.5}
            />
          ))}
        </svg>
      </div>
      {card.legend.length > 0 ? (
        <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1.5">
          {card.legend.map((entry) => (
            <li key={entry.code} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <svg width={14} height={14} viewBox={`0 0 ${CELL} ${CELL}`} aria-hidden="true" className="shrink-0">
                <TileShape code={entry.code} x={0} y={0} cell={CELL} />
              </svg>
              <span>{entry.label}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <svg width={12} height={12} viewBox="0 0 12 12" aria-hidden="true"><circle cx={6} cy={6} r={4} fill="none" stroke="var(--success)" strokeWidth={2.5} /></svg>
          起点
        </span>
        <span className="flex items-center gap-1.5">
          <svg width={12} height={12} viewBox="0 0 12 12" aria-hidden="true"><circle cx={6} cy={6} r={4} fill="none" stroke="var(--danger)" strokeWidth={2.5} /></svg>
          终点
        </span>
      </p>
      {card.caption ? <p className="mt-2 break-words text-footnote text-muted-foreground">{card.caption}</p> : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 图表卡（ECharts + 数据表兜底）
// ---------------------------------------------------------------------------

function cellText(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : String(value)
}

/** 数据表：列是系列、行是类目；饼图只画第一条系列，表也只列它。 */
function ChartDataTable({ card }: { card: ChartCardData }) {
  const series = card.chartType === 'pie' ? card.series.slice(0, 1) : card.series
  return (
    <table className="w-full border-collapse text-left text-xs">
      <thead>
        <tr className="text-muted-foreground">
          <th scope="col" className="sticky top-0 bg-surface-2 px-2 py-1.5 font-medium">{card.xLabel || '类目'}</th>
          {series.map((item) => (
            <th key={item.name} scope="col" className="sticky top-0 bg-surface-2 px-2 py-1.5 text-right font-medium">{item.name}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {card.categories.map((category, index) => (
          <tr key={`${category}-${index}`} className="border-t">
            <th scope="row" className="max-w-[12rem] truncate px-2 py-1.5 font-normal">{category}</th>
            {series.map((item) => (
              <td key={item.name} className="px-2 py-1.5 text-right tabular-nums">{cellText(item.values[index])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** 图表卡：标题 + ECharts 面 + 数据来源脚注 + 「数据」折叠表（永远可用）。 */
export function ChartCard({ card }: { card: ChartCardData }) {
  const [failed, setFailed] = useState(false)
  return (
    <div className="min-w-0 rounded-xl border bg-card p-3">
      <p className="text-sm font-medium">{card.title}</p>
      {card.caption ? <p className="mt-0.5 break-words text-footnote text-muted-foreground">{card.caption}</p> : null}
      <div className="mt-2">
        <EChartSurface spec={card} onError={() => setFailed(true)} />
      </div>
      {failed ? <p role="status" className="mt-1 text-footnote text-warning">图表加载失败，已改用数据表。</p> : null}
      <details
        open={failed || undefined}
        className="group mt-2 rounded-lg border bg-surface-2"
        data-testid="chart-data"
      >
        <summary className="flex min-h-9 cursor-pointer list-none items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
          <ChevronDown aria-hidden="true" className={cn('size-3.5 transition-transform duration-200', failed && 'rotate-180')} />
          数据
          <span className="text-muted-foreground/80">{card.categories.length} 项 · {card.series.length} 个系列</span>
        </summary>
        <div className="max-h-72 overflow-auto border-t">
          <ChartDataTable card={card} />
        </div>
      </details>
      {card.source ? <p className="mt-1.5 break-words text-xs text-muted-foreground">数据来源：{card.source}</p> : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 入口
// ---------------------------------------------------------------------------

export function MediaCardView({ card }: { card: MediaCard }) {
  switch (card.type) {
    case 'image':
      return <ImageCard card={card} />
    case 'item_icons':
      return <ItemIconsCard card={card} />
    case 'stage_map':
      return <StageMapCard card={card} />
    case 'chart':
      return <ChartCard card={card} />
  }
}

/** 一轮工具结果里的全部媒体卡；没有卡片时什么都不渲染。 */
export function MediaCards({ cards, className }: { cards: readonly MediaCard[]; className?: string }) {
  if (cards.length === 0) return null
  return (
    <div className={cn('space-y-2', className)} data-testid="media-cards">
      {cards.map((card, index) => <MediaCardView key={`${card.type}-${index}`} card={card} />)}
    </div>
  )
}
