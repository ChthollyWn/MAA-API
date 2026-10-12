import { useMemo } from 'react'
import { User } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { FieldSource, OperatorRow } from './api'
import {
  RARITY_COLORS,
  UNOWNED_COLORS,
  avatarPath,
  moduleTypeGlyph,
  skillIconPath,
  useAssetUrl,
} from './assets'
import {
  SOURCE_HINTS,
  SOURCE_LABELS,
  eliteLabel,
  masteryText,
  moduleText,
  professionLabel,
  rarityLabel,
  rowSources,
  trainingValueText,
} from './format'

/** 一个技能槽位要画的东西：图标（可能没有）与专精等级（可能未识别）。 */
interface SkillSlot {
  slot: number
  skillId: string | null
  mastery: number | null
}

/** 一个模组 chip：`label` 优先（`CCR-Y`），退化成类型字母（`Y`）。 */
interface ModuleChip {
  key: string
  glyph: string
  text: string
  level: number
}

const SOURCE_TONES: Record<FieldSource, 'neutral' | 'success' | 'primary' | 'warning'> = {
  recognition: 'neutral',
  manual: 'primary',
  agent: 'success',
  skland: 'warning',
}

/**
 * 一张资源图：先带 token 取 blob 再转 object URL（`<img src>` 带不了鉴权头），
 * 取不到（未拥有、没有 id、上游没有这张图）时退回占位，绝不把破图留在页面上。
 */
function AssetImage({
  path,
  testId,
  className,
}: {
  path: string | null
  testId: string
  className: string
}) {
  const url = useAssetUrl(path)
  if (!url) {
    return (
      <span
        aria-hidden="true"
        data-testid={`${testId}-placeholder`}
        className="flex size-full items-center justify-center bg-muted text-muted-foreground"
      >
        <User className="size-4" />
      </span>
    )
  }
  return <img src={url} alt="" data-testid={testId} className={className} decoding="async" />
}

function skillSlots(row: OperatorRow): SkillSlot[] {
  const ids = row.skill_ids ?? []
  const masteries = row.masteries
  const count = Math.min(3, Math.max(ids.length, masteries ? 3 : 0))
  const slots: SkillSlot[] = []
  for (let index = 0; index < count; index += 1) {
    const slot = index + 1
    slots.push({
      slot,
      skillId: ids[index] ?? null,
      // 专精 0 与「未识别」是两件事：`masteries` 为 `null` 时这里是 `null`（未识别），
      // 有映射时缺槽位按 0 算（与 `masteryText` 同口径）。
      mastery: masteries ? Number(masteries[String(slot)] ?? 0) : null,
    })
  }
  return slots
}

function moduleChips(row: OperatorRow): ModuleChip[] {
  const details = row.module_details ?? []
  if (details.length) {
    return details
      .filter((detail) => Number(detail.level) > 0)
      .map((detail) => ({
        key: detail.type,
        glyph: moduleTypeGlyph(detail.type),
        text: detail.label ?? moduleTypeGlyph(detail.type),
        level: Number(detail.level),
      }))
  }
  // 老数据（只有 `modules`）与手动填写的模组没有明细：退化成「类型字母 + 等级」，
  // 不伪造 `CCR-` 这种分支码。
  return Object.entries(row.modules ?? {})
    .filter(([, level]) => Number(level) > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([type, level]) => ({
      key: type,
      glyph: moduleTypeGlyph(type),
      text: moduleTypeGlyph(type),
      level: Number(level),
    }))
}

/** 角标：技能专精与模组等级共用，位置/样式一致，扫视时不会看错列。 */
function CornerBadge({ value, testId }: { value: string; testId: string }) {
  return (
    <span
      data-testid={testId}
      className="absolute -left-1 -top-1 rounded bg-background px-1 text-[0.625rem] font-medium tabular-nums leading-4 shadow-sm"
    >
      {value}
    </span>
  )
}

/**
 * 一张干员卡（M43 规格 §4.4，按用户 2026-10-12 的反馈改成**宽横卡**）。
 *
 * 布局照着参考实现的干员条：左侧 4px 稀有度色带 → 56px 头像 → 名字与精英化/等级/潜能
 * → 三个技能方框（专精角标）→ 模组方框（等级角标 + 下方 `CCR-X` 标签）。行宽由
 * :func:`OperatorGrid` 控制在 360–500px，所以文字不会像窄方块卡那样被挤成竖排。
 *
 * 视觉之上仍保留一份 `dl` 的六列练度原文（与卡片上的图形是同一批事实）：M35 要求
 * 「未识别」在页面上如实可见，用图标或徽章把它藏起来就等于又丢了一次事实。
 * 未拥有（`own=false`）的干员整卡灰显、不加载头像与技能图标。
 */
export function OperatorCard({
  row,
  onSelect,
}: {
  row: OperatorRow
  onSelect: (row: OperatorRow) => void
}) {
  const colors = row.own
    ? RARITY_COLORS[row.rarity ?? 0] ?? UNOWNED_COLORS
    : UNOWNED_COLORS
  const sources = rowSources(row)
  const slots = useMemo(() => skillSlots(row), [row])
  const chips = useMemo(() => moduleChips(row), [row])
  const unknown = (value: unknown) => value === null || value === undefined

  return (
    // testid 分两层：`operator-<id>` 是整行（页面按它读练度原文、找来源徽章），
    // `operator-card-<id>` 是可点的那块（点击/键盘用例点它）。
    <li data-testid={`operator-${row.char_id}`}>
      <div
        role="button"
        tabIndex={0}
        data-testid={`operator-card-${row.char_id}`}
        aria-label={`查看 ${row.name} 的练度`}
        onClick={() => onSelect(row)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            onSelect(row)
          }
        }}
        style={{ ['--card-primary' as string]: colors.primary, ['--card-secondary' as string]: colors.secondary }}
        className={`group relative flex w-full items-center gap-2.5 overflow-hidden rounded-xl border bg-card py-2 pl-3 pr-2 text-left shadow-card transition-colors hover:border-[var(--card-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
          row.own ? '' : 'opacity-70 grayscale'
        }`}
      >
        {/* 左侧稀有度色带（位置与宽度照参考实现：4px 竖条）。 */}
        <span
          aria-hidden="true"
          data-testid={`operator-stripe-${row.char_id}`}
          className="absolute inset-y-0 left-0 w-1"
          style={{ background: `linear-gradient(180deg, var(--card-secondary), var(--card-primary))` }}
        />

        <span className="relative size-14 shrink-0 overflow-hidden rounded-lg border bg-muted">
          {row.own ? (
            <AssetImage
              path={avatarPath(row.char_id)}
              testId={`operator-avatar-${row.char_id}`}
              className="size-full object-cover"
            />
          ) : (
            <span
              aria-hidden="true"
              data-testid="operator-avatar-placeholder"
              className="flex size-full items-center justify-center bg-muted text-muted-foreground"
            >
              <User className="size-5" />
            </span>
          )}
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-[0.9375rem] font-medium" title={row.name}>
              {row.name}
            </span>
            {row.own ? null : <Badge variant="outline">未拥有</Badge>}
          </span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-footnote text-muted-foreground">
            <Badge variant="neutral">{rarityLabel(row.rarity)}</Badge>
            <span>{professionLabel(row.profession)}</span>
            <span className="whitespace-nowrap">{eliteLabel(row.elite)}</span>
            <span className="whitespace-nowrap tabular-nums">
              {unknown(row.level) ? '等级未识别' : `Lv ${row.level}`}
            </span>
            {unknown(row.potential) ? null : (
              <span className="whitespace-nowrap tabular-nums">潜 {row.potential}</span>
            )}
          </span>
        </span>

        {/* 技能：方框 + 左上角专精角标。没有 `skill_ids`（老数据 / 纯识别行）时不画图标，
            只留空框——不拿别的图冒充；未拥有整块不画（规格 §3.1.5）。 */}
        {row.own && slots.length ? (
          <span className="flex shrink-0 items-center gap-2">
            {slots.map((slot) => (
              <span
                key={slot.slot}
                data-testid={`operator-skill-${row.char_id}-${slot.slot}`}
                className="relative size-10 shrink-0 rounded-md border bg-muted/40"
              >
                <AssetImage
                  path={slot.skillId ? skillIconPath(slot.skillId) : null}
                  testId={`operator-skill-icon-${row.char_id}-${slot.slot}`}
                  className="size-full object-contain p-0.5"
                />
                {slot.mastery !== null && slot.mastery > 0 ? (
                  <CornerBadge
                    value={String(slot.mastery)}
                    testId={`operator-skill-mastery-${row.char_id}-${slot.slot}`}
                  />
                ) : null}
              </span>
            ))}
          </span>
        ) : null}

        {/* 模组：方框（类型字形 + 等级角标）+ 下方 `CCR-X` 标签。图标本轮不做（§3.2）。 */}
        {row.own && chips.length ? (
          <span className="flex shrink-0 items-start gap-2">
            {chips.map((chip) => (
              // testid 挂在「方框 + 标签」这一整块上：断言 `CCR-X` 与等级时不关心
              // 它们在 DOM 里是两层还是三层。
              <span
                key={chip.key}
                data-testid={`operator-module-${row.char_id}-${chip.key}`}
                className="flex w-11 flex-col items-center gap-0.5"
              >
                <span
                  className="relative flex size-10 items-center justify-center rounded-md border text-base"
                  style={{ borderColor: 'var(--card-primary)' }}
                >
                  {chip.glyph}
                  <CornerBadge
                    value={String(chip.level)}
                    testId={`operator-module-level-${row.char_id}-${chip.key}`}
                  />
                </span>
                <span className="w-full truncate text-center text-[0.625rem] text-muted-foreground">
                  {chip.text}
                </span>
              </span>
            ))}
          </span>
        ) : null}

        <Button
          variant="ghost"
          size="sm"
          className="ml-auto shrink-0"
          aria-label={`编辑 ${row.name}`}
          onClick={(event) => {
            event.stopPropagation()
            onSelect(row)
          }}
        >
          编辑
        </Button>
      </div>

      <div className="mt-1 space-y-0.5 px-2">
        {/* 未识别提示：M35 要求「不知道」在页面上如实可见——技能与模组没有数据时
            说清楚，别让人以为「没专精 / 没模组」。 */}
        {row.masteries === null || row.masteries === undefined || row.modules === null || row.modules === undefined ? (
          <p className="text-footnote text-muted-foreground">
            {[
              row.masteries === null || row.masteries === undefined ? '专精未识别' : null,
              row.modules === null || row.modules === undefined ? '模组未识别' : null,
            ]
              .filter(Boolean)
              .join(' · ')}
          </p>
        ) : null}

        {/* 六列练度原文：与卡片上的图形是同一批事实，`null` 一律写「未识别」（M35）。 */}
        <dl className="flex flex-wrap gap-x-3 gap-y-0.5 text-footnote text-muted-foreground">
          <span>
            <dt className="inline">精英化</dt> <dd className="inline">{eliteLabel(row.elite)}</dd>
          </span>
          <span>
            <dt className="inline">等级</dt>{' '}
            <dd className="inline tabular-nums">{trainingValueText(row.level)}</dd>
          </span>
          <span>
            <dt className="inline">潜能</dt>{' '}
            <dd className="inline tabular-nums">{trainingValueText(row.potential)}</dd>
          </span>
          <span>
            <dt className="inline">技能等级</dt>{' '}
            <dd className="inline tabular-nums">{trainingValueText(row.skill_level)}</dd>
          </span>
          <span>
            <dt className="inline">专精</dt> <dd className="inline">{masteryText(row.masteries)}</dd>
          </span>
          <span>
            <dt className="inline">模组</dt> <dd className="inline">{moduleText(row.modules)}</dd>
          </span>
        </dl>

        <div className="flex flex-wrap items-center gap-1">
          <span className="sr-only">字段来源</span>
          {sources.length ? (
            sources.map((source) => (
              <Badge
                key={source}
                variant={SOURCE_TONES[source]}
                data-testid={`operator-source-${row.char_id}-${source}`}
              >
                <span title={SOURCE_HINTS[source]}>{SOURCE_LABELS[source]}</span>
              </Badge>
            ))
          ) : (
            <span className="text-footnote text-muted-foreground">未记录来源</span>
          )}
          {unknown(row.recognized_at) ? null : (
            <span className="text-footnote text-muted-foreground">已同步</span>
          )}
        </div>
      </div>
    </li>
  )
}
