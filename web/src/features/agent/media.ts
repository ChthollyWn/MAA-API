import type { AuthedImageSource, AuthedImageSize } from '@/components/ui/use-authed-image'
import { isRecord } from './format'
import type { ToolView } from './transcript'

/**
 * 媒体卡：Agent 工具结果里 `result.media` 的渲染契约（规格 §3.1）。
 *
 * 这里只做「解析 + 防御」：字段名与后端一致，地址一律过白名单，未知/残缺/超限的卡
 * 直接丢掉。解析永不抛异常——最差的结果是没有卡片，而不是白屏。
 */

export type MediaImageSource = NonNullable<AuthedImageSource>
export type MediaImageSize = AuthedImageSize

/** 前端再裁一次的上限（规格 §3.1 / §3.5，后端已经先拒过一遍）。 */
export const MEDIA_LIMITS = {
  /** 一次最多渲染的卡片数（规格「一次 ≤ 8 张图」）。 */
  cards: 8,
  /** 物品图标卡的图标数。 */
  icons: 8,
  /** 图表系列数与类目数、单个系列的数值数。 */
  series: 6,
  categories: 200,
  values: 200,
  /** 关卡网格：后台上限 40×40，前端放宽到 64×64 并额外守总格数。 */
  stageSide: 64,
  stageCells: 4_096,
  /** 起终点标记数上限。 */
  markers: 64,
  /** 图例项上限。 */
  legend: 24,
  /** 文本长度（规格 §3.5）。 */
  title: 80,
  label: 40,
  caption: 300,
  source: 200,
  unit: 16,
} as const

export interface ImageCardData {
  type: 'image'
  screenshotId: string | null
  sha256: string | null
  /** 缩略图来源；卡里没有可用缩略图时为 null（用原图当缩略图）。 */
  thumb: MediaImageSource | null
  /** 原图来源；点击放大时用它。 */
  full: MediaImageSource | null
  alt: string
  caption: string
  width: number | null
  height: number | null
}

export interface MediaItemIcon {
  itemId: string
  name: string
  icon: MediaImageSource
}

export interface ItemIconsCardData {
  type: 'item_icons'
  caption: string
  items: MediaItemIcon[]
}

export interface StageMapLegendEntry {
  code: string
  label: string
}

export interface StageMapCardData {
  type: 'stage_map'
  caption: string
  code: string
  name: string
  stageId: string
  width: number
  height: number
  /** 同名关卡的候选数；> 1 时卡片上要给提示。 */
  candidates: number
  /** `tiles[row][col]`，每格 3 字符 `<地形><可部署><高度>`（规格 §3.4）。 */
  tiles: string[][]
  /** 起点坐标 `[x, y]`（x 是列、y 是行）。 */
  start: Array<[number, number]>
  end: Array<[number, number]>
  legend: StageMapLegendEntry[]
}

export type ChartType = 'line' | 'bar' | 'pie'

export interface ChartSeries {
  name: string
  /** `null` 表示断点，折线在这里断开而不是掉到 0。 */
  values: Array<number | null>
}

export interface ChartCardData {
  type: 'chart'
  caption: string
  chartType: ChartType
  title: string
  categories: string[]
  series: ChartSeries[]
  stacked: boolean
  xLabel: string
  yLabel: string
  unit: string
  source: string
}

export type MediaCard = ImageCardData | ItemIconsCardData | StageMapCardData | ChartCardData

// ---------------------------------------------------------------------------
// 白名单：只认本服务的三种图片地址（规格 §3.1；延续 features/logs/format.ts 的做法）。
// 外域、`data:`、相对路径、大小写不符一律不认。
// ---------------------------------------------------------------------------

const IMAGE_URL = /^\/api\/images\/([0-9a-f]{64})\/(thumb|full)$/
const SCREENSHOT_URL = /^\/api\/screenshots\/([0-9A-Za-z_-]{1,64})$/
const ITEM_ICON_URL = /^\/api\/resources\/items\/icon\?item_id=([^&?#]{1,256})$/

/**
 * 把一条图片地址收敛成取图来源；不在白名单里的地址返回 null。
 *
 * `item_id` 允许是百分号编码（后端按 `quote(item_id, safe='')` 拼），解码后再交给
 * `api` 客户端编码成查询参数。
 */
export function mediaImageSource(url: unknown): MediaImageSource | null {
  if (typeof url !== 'string') return null
  const image = IMAGE_URL.exec(url)
  if (image) return { kind: 'image', sha256: image[1]!, size: image[2] as MediaImageSize }
  const screenshot = SCREENSHOT_URL.exec(url)
  if (screenshot) return { kind: 'screenshot', id: screenshot[1]!, size: 'full' }
  const icon = ITEM_ICON_URL.exec(url)
  if (icon) {
    let itemId: string
    try {
      itemId = decodeURIComponent(icon[1]!)
    } catch {
      return null
    }
    if (!itemId || itemId.length > 64 || /[\s/?#]/.test(itemId)) return null
    return { kind: 'resource-icon', itemId }
  }
  return null
}

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

function text(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.slice(0, limit) : ''
}

function trimmed(value: unknown, limit: number): string {
  return text(value, limit).trim()
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function positiveInt(value: unknown): number | null {
  const number = finite(value)
  return number !== null && Number.isInteger(number) && number > 0 ? number : null
}

function nonNegativeInt(value: unknown): number | null {
  const number = finite(value)
  return number !== null && Number.isInteger(number) && number >= 0 ? number : null
}

function sha256Of(value: unknown): string | null {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value) ? value : null
}

// ---------------------------------------------------------------------------
// 四种卡
// ---------------------------------------------------------------------------

/** 地址缺失时用 sha256 / 截图 id 拼规范地址（与 logs/format.ts 的 logImage 同源做法）。 */
function canonicalSource(
  sha256: string | null,
  screenshotId: string | null,
  size: MediaImageSize,
): MediaImageSource | null {
  if (sha256) return { kind: 'image', sha256, size }
  if (screenshotId) return { kind: 'screenshot', id: screenshotId, size }
  return null
}

/**
 * `image` 卡（含 M23 之前 `get_screenshot` 的旧形态：`full_url` + `thumb_url`）。
 *
 * 规则：卡里**给了地址就必须过白名单**，给了外域/`data:` 地址整卡丢弃；地址为空时才
 * 用 sha256 / screenshot_id 拼规范地址。
 */
export function parseImageCard(card: Record<string, unknown>): ImageCardData | null {
  const screenshotId = text(card.screenshot_id, 64) || null
  const sha256 = sha256Of(card.sha256)
  const rawFull = card.url ?? card.full_url
  const rawThumb = card.thumb_url ?? null
  const fullFromUrl = rawFull === undefined || rawFull === null ? null : mediaImageSource(rawFull)
  const thumbFromUrl = rawThumb === null ? null : mediaImageSource(rawThumb)
  // 显式给的地址不合法 → 整卡丢弃，不做「换成 sha256 拼一个」的静默兜底。
  if (rawFull !== undefined && rawFull !== null && !fullFromUrl) return null
  if (rawThumb !== null && !thumbFromUrl) return null
  const recordId = screenshotId ?? (fullFromUrl?.kind === 'screenshot' ? fullFromUrl.id : null)
  const full = fullFromUrl ?? canonicalSource(sha256, recordId, 'full')
  const thumb = thumbFromUrl ?? canonicalSource(sha256, recordId, 'thumb')
  if (!full && !thumb) return null
  return {
    type: 'image',
    screenshotId,
    sha256,
    thumb,
    full,
    alt: trimmed(card.alt, MEDIA_LIMITS.caption),
    caption: trimmed(card.caption, MEDIA_LIMITS.caption),
    width: positiveInt(card.width),
    height: positiveInt(card.height),
  }
}

function parseItemIconsCard(card: Record<string, unknown>): ItemIconsCardData | null {
  const raw = Array.isArray(card.items) ? card.items : null
  if (!raw || raw.length === 0 || raw.length > MEDIA_LIMITS.icons) return null
  const items: MediaItemIcon[] = []
  for (const entry of raw) {
    if (!isRecord(entry)) continue
    const icon = mediaImageSource(entry.icon_url)
    if (!icon || icon.kind !== 'resource-icon') continue
    items.push({ itemId: icon.itemId, name: trimmed(entry.name, MEDIA_LIMITS.label) || icon.itemId, icon })
  }
  if (items.length === 0) return null
  return { type: 'item_icons', caption: trimmed(card.caption, MEDIA_LIMITS.caption), items }
}

function markerPairs(value: unknown, width: number, height: number): Array<[number, number]> {
  if (!Array.isArray(value)) return []
  const pairs: Array<[number, number]> = []
  for (const entry of value.slice(0, MEDIA_LIMITS.markers)) {
    if (!Array.isArray(entry) || entry.length !== 2) continue
    const x = finite(entry[0])
    const y = finite(entry[1])
    if (x === null || y === null || !Number.isInteger(x) || !Number.isInteger(y)) continue
    if (x < 0 || y < 0 || x >= width || y >= height) continue
    pairs.push([x, y])
  }
  return pairs
}

function parseLegend(value: unknown): StageMapLegendEntry[] {
  if (!Array.isArray(value)) return []
  const entries: StageMapLegendEntry[] = []
  for (const item of value.slice(0, MEDIA_LIMITS.legend)) {
    if (!isRecord(item)) continue
    const code = trimmed(item.code, 8)
    if (!code) continue
    entries.push({ code, label: trimmed(item.label, MEDIA_LIMITS.label) || code })
  }
  return entries
}

function parseStageMapCard(card: Record<string, unknown>): StageMapCardData | null {
  const stage = isRecord(card.stage) ? card.stage : null
  if (!stage) return null
  const width = positiveInt(stage.width)
  const height = positiveInt(stage.height)
  if (!width || !height) return null
  if (width > MEDIA_LIMITS.stageSide || height > MEDIA_LIMITS.stageSide || width * height > MEDIA_LIMITS.stageCells) return null
  const rows = Array.isArray(stage.tiles) ? stage.tiles : null
  if (!rows || rows.length !== height) return null
  const tiles: string[][] = []
  for (const row of rows) {
    if (!Array.isArray(row) || row.length !== width) return null
    const cells: string[] = []
    for (const cell of row) {
      if (typeof cell !== 'string' || cell.length !== 3) return null
      cells.push(cell)
    }
    tiles.push(cells)
  }
  const stageId = trimmed(stage.stage_id, 128)
  return {
    type: 'stage_map',
    caption: trimmed(card.caption, MEDIA_LIMITS.caption),
    code: trimmed(stage.code, MEDIA_LIMITS.label),
    name: trimmed(stage.name, MEDIA_LIMITS.label),
    stageId,
    width,
    height,
    candidates: nonNegativeInt(stage.candidates) ?? 1,
    tiles,
    start: markerPairs(stage.start, width, height),
    end: markerPairs(stage.end, width, height),
    legend: parseLegend(stage.legend),
  }
}

function parseSeries(value: unknown): ChartSeries[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > MEDIA_LIMITS.series) return null
  const series: ChartSeries[] = []
  for (const entry of value) {
    if (!isRecord(entry)) return null
    const name = trimmed(entry.name, MEDIA_LIMITS.label)
    if (!name) return null
    const raw = entry.values
    if (!Array.isArray(raw) || raw.length === 0 || raw.length > MEDIA_LIMITS.values) return null
    const values: Array<number | null> = []
    for (const point of raw) {
      // 允许 null（断点），其余必须是有限数；字符串与 NaN / Infinity 一律拒绝整张卡。
      if (point === null) {
        values.push(null)
        continue
      }
      const number = finite(point)
      if (number === null) return null
      values.push(number)
    }
    series.push({ name, values })
  }
  return series
}

function parseChartCard(card: Record<string, unknown>): ChartCardData | null {
  const chart = isRecord(card.chart) ? card.chart : null
  if (!chart) return null
  const chartType = chart.chart_type
  if (chartType !== 'line' && chartType !== 'bar' && chartType !== 'pie') return null
  const title = trimmed(chart.title, MEDIA_LIMITS.title)
  if (!title) return null
  const rawCategories = chart.categories
  if (!Array.isArray(rawCategories) || rawCategories.length === 0 || rawCategories.length > MEDIA_LIMITS.categories) return null
  const categories: string[] = []
  for (const entry of rawCategories) {
    if (typeof entry !== 'string') return null
    categories.push(entry.slice(0, MEDIA_LIMITS.label))
  }
  const series = parseSeries(chart.series)
  if (!series) return null
  return {
    type: 'chart',
    caption: trimmed(card.caption, MEDIA_LIMITS.caption),
    chartType,
    title,
    categories,
    series,
    stacked: chart.stacked === true,
    xLabel: trimmed(chart.x_label, MEDIA_LIMITS.label),
    yLabel: trimmed(chart.y_label, MEDIA_LIMITS.label),
    unit: trimmed(chart.unit, MEDIA_LIMITS.unit),
    source: trimmed(chart.source, MEDIA_LIMITS.source),
  }
}

// ---------------------------------------------------------------------------
// 入口
// ---------------------------------------------------------------------------

function parseCard(value: unknown, toolName?: string | null): MediaCard | null {
  if (!isRecord(value)) return null
  switch (value.type) {
    case 'image':
      return parseImageCard(value)
    case 'item_icons':
      return parseItemIconsCard(value)
    case 'stage_map':
      return parseStageMapCard(value)
    case 'chart':
      return parseChartCard(value)
    default:
      // 旧形态：`get_screenshot` 的结果没有 `type`，靠工具名认。
      return toolName === 'get_screenshot' ? parseImageCard(value) : null
  }
}

/** 工具结果体里的卡片数组：`{result: {...}}` / `{error: {...}}`（实时事件经 liveToolView 后形状一致）。 */
function resultBody(input: unknown): Record<string, unknown> | null {
  if (!isRecord(input)) return null
  if ('result' in input) return isRecord(input.result) ? input.result : null
  return input
}

/**
 * 解析一次工具结果里的媒体卡。
 *
 * 入参是工具结果体（历史消息里的 `{result: {...}}` / `{error: {...}}`，实时事件经
 * `liveToolView` 后形状一致），只读其中的 `media` 键；`get_screenshot` 的旧形态
 * （没有 `media`，只有 `screenshot_id` / `sha256` / `thumb_url` / `full_url`）合成一张
 * image 卡。未知类型、缺字段、类型不符、超上限的卡一律忽略，不抛异常。
 */
export function parseMediaCards(result: unknown, toolName?: string | null): MediaCard[] {
  const body = resultBody(result)
  if (!body) return []
  const raw = body.media
  // 老会话里 `get_screenshot` 的结果没有 media 键（新格式则一定有）；两种都当「缺失」处理，
  // 按 §3.1 的兼容规则合成一张 image 卡。
  if (raw === undefined || raw === null) {
    const legacy = toolName === 'get_screenshot' ? parseImageCard(body) : null
    return legacy ? [legacy] : []
  }
  if (!Array.isArray(raw)) return []
  const cards: MediaCard[] = []
  for (const entry of raw) {
    if (cards.length >= MEDIA_LIMITS.cards) break
    const card = parseCard(entry, toolName)
    if (card) cards.push(card)
  }
  return cards
}

/** `ToolCallBar` 用的便捷入口：从一次工具调用视图里取出可渲染的卡片。 */
export function mediaCardsOf(tool: Pick<ToolView, 'name' | 'result'>): MediaCard[] {
  return parseMediaCards(tool.result, tool.name)
}
