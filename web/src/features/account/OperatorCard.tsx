import { useMemo } from 'react'
import { User } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { AssetImage } from './AssetImage'
import type { OperatorRow } from './api'
import {
  RARITY_COLORS,
  UNOWNED_COLORS,
  avatarPath,
  moduleTypeGlyph,
  skillIconPath,
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

/** 来源徽章的配色。M47 起实际只会用到 `skland`（森空岛），其余三个取值留给老响应。 */
const SOURCE_TONES: Record<string, 'neutral' | 'success' | 'primary' | 'warning'> = {
  recognition: 'neutral',
  manual: 'primary',
  agent: 'success',
  skland: 'warning',
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

/** 角标：技能专精与模组等级共用，位置与样式一致。 */
function CornerBadge({ value, testId }: { value: string; testId: string }) {
  return (
    <span
      data-testid={testId}
      className="absolute -right-1 -top-1 grid min-w-4 place-items-center rounded bg-primary px-1 text-[0.625rem] font-semibold tabular-nums leading-4 text-primary-foreground shadow-sm"
    >
      {value}
    </span>
  )
}

/**
 * 一张干员卡（M43 规格 §4.4、M47 只读版）：
 *
 * 采用固定两段式卡片结构：
 * 1. 顶部身份栏：左侧头像 + 姓名/星级/职业/基础练度，右侧来源徽章（M47 起干员数据只有
 *    森空岛一个来源，所以是一枚固定的「森空岛」，也没有编辑入口）；
 * 2. 底部装备与状态栏：等高对齐的技能图标（带专精角标）与水平模组芯片（带等级角标与分支码），以及未识别提示。
 */
export function OperatorCard({ row }: { row: OperatorRow }) {
  const colors = row.own
    ? RARITY_COLORS[row.rarity ?? 0] ?? UNOWNED_COLORS
    : UNOWNED_COLORS
  const sources = rowSources(row)
  const slots = useMemo(() => skillSlots(row), [row])
  const chips = useMemo(() => moduleChips(row), [row])
  const unknown = (value: unknown) => value === null || value === undefined
  const hasVisualEquipment = row.own && (slots.length > 0 || chips.length > 0)
  const unrecognizedHint = [
    row.masteries === null || row.masteries === undefined ? '专精未识别' : null,
    row.modules === null || row.modules === undefined ? '模组未识别' : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <li data-testid={`operator-${row.char_id}`} className="flex">
      <div
        data-testid={`operator-card-${row.char_id}`}
        style={{ ['--card-primary' as string]: colors.primary, ['--card-secondary' as string]: colors.secondary }}
        className={cn(
          'relative flex w-full flex-col justify-between gap-2.5 overflow-hidden rounded-xl border bg-card p-3 pl-3.5 text-left shadow-card',
          row.own ? '' : 'opacity-70 grayscale',
        )}
      >
        {/* 左侧稀有度色带 */}
        <span
          aria-hidden="true"
          data-testid={`operator-stripe-${row.char_id}`}
          className="absolute inset-y-0 left-0 w-1"
          style={{ background: `linear-gradient(180deg, var(--card-secondary), var(--card-primary))` }}
        />

        {/* 第一行：头像 + 姓名与核心练度 + 右侧来源徽章与编辑入口 */}
        <div className="flex items-start justify-between gap-2.5">
          <div className="flex min-w-0 flex-1 items-center gap-2.5">
            <span className="relative size-12 shrink-0 overflow-hidden rounded-xl border bg-muted">
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

            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="truncate text-[0.9375rem] font-semibold leading-5 text-foreground" title={row.name}>
                  {row.name}
                </span>
                <Badge variant="neutral" className="px-1.5 py-0 text-[0.6875rem] tabular-nums">
                  {rarityLabel(row.rarity)}
                </Badge>
                <span className="text-xs text-muted-foreground">{professionLabel(row.profession)}</span>
                {row.own ? null : <Badge variant="outline">未拥有</Badge>}
              </div>

              {/* 核心练度单行胶囊流（视觉呈现，不重复堆叠 6 个小盒子） */}
              <div aria-hidden="true" className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs tabular-nums text-muted-foreground">
                <span className={cn('font-medium', !unknown(row.elite) && 'text-foreground')}>
                  {unknown(row.elite) ? '精英未识别' : eliteLabel(row.elite)}
                </span>
                <span className="text-border-strong">·</span>
                <span className={cn('font-medium', !unknown(row.level) && 'text-foreground')}>
                  {unknown(row.level) ? '等级未识别' : `Lv.${row.level}`}
                </span>
                <span className="text-border-strong">·</span>
                <span>{unknown(row.potential) ? '潜能未识别' : `潜能 ${row.potential}`}</span>
                <span className="text-border-strong">·</span>
                <span>{unknown(row.skill_level) ? '技能未识别' : `技能 Lv.${row.skill_level}`}</span>
                {typeof row.favor_percent === 'number' ? (
                  <>
                    <span className="text-border-strong">·</span>
                    <span>信赖 {row.favor_percent}%</span>
                  </>
                ) : null}
              </div>
            </div>
          </div>

        </div>

        {/* 第二行：技能图标槽位 + 等高水平模组芯片 + 来源徽章 */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2">
          <div className="flex flex-wrap items-center gap-2">
            {hasVisualEquipment ? (
              <>
                {slots.length ? (
                  <span className="flex items-center gap-1.5">
                    {slots.map((slot) => (
                      <span
                        key={slot.slot}
                        data-testid={`operator-skill-${row.char_id}-${slot.slot}`}
                        className="relative size-8 shrink-0 rounded-lg border bg-surface-2/70"
                      >
                        <AssetImage
                          path={slot.skillId ? skillIconPath(slot.skillId) : null}
                          testId={`operator-skill-icon-${row.char_id}-${slot.slot}`}
                          className="size-full rounded-lg object-contain p-0.5"
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

                {chips.length ? (
                  <span className="flex flex-wrap items-center gap-1.5">
                    {chips.map((chip) => (
                      <span
                        key={chip.key}
                        data-testid={`operator-module-${row.char_id}-${chip.key}`}
                        className="relative inline-flex h-8 items-center gap-1 rounded-lg border bg-surface-2/50 px-2 text-xs"
                        style={{ borderColor: 'var(--card-primary)' }}
                      >
                        <span className="font-semibold text-foreground">{chip.glyph}</span>
                        {chip.text !== chip.glyph ? (
                          <span className="font-mono text-[0.6875rem] text-muted-foreground">{chip.text}</span>
                        ) : null}
                        <CornerBadge
                          value={String(chip.level)}
                          testId={`operator-module-level-${row.char_id}-${chip.key}`}
                        />
                      </span>
                    ))}
                  </span>
                ) : null}
              </>
            ) : null}

            {unrecognizedHint ? (
              <span className="text-footnote text-muted-foreground">{unrecognizedHint}</span>
            ) : null}
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-1">
            <span className="sr-only">字段来源</span>
            {sources.length ? (
              sources.map((source) => (
                <Badge
                  key={source}
                  variant={SOURCE_TONES[source] ?? 'skland'}
                  data-testid={`operator-source-${row.char_id}-${source}`}
                >
                  <span title={SOURCE_HINTS[source]}>{SOURCE_LABELS[source] ?? source}</span>
                </Badge>
              ))
            ) : (
              <span className="text-footnote text-muted-foreground">未记录来源</span>
            )}
          </div>
        </div>

        {/* 六列练度结构化定义列表（供无障碍与 M35 契约精确读取，`null` 写「未识别」） */}
        <dl className="sr-only">
          <div>
            <dt>精英化</dt>
            <dd>{eliteLabel(row.elite)}</dd>
          </div>
          <div>
            <dt>等级</dt>
            <dd>{trainingValueText(row.level)}</dd>
          </div>
          <div>
            <dt>潜能</dt>
            <dd>{trainingValueText(row.potential)}</dd>
          </div>
          <div>
            <dt>技能等级</dt>
            <dd>{trainingValueText(row.skill_level)}</dd>
          </div>
          <div>
            <dt>专精</dt>
            <dd>{masteryText(row.masteries)}</dd>
          </div>
          <div>
            <dt>模组</dt>
            <dd>{moduleText(row.modules)}</dd>
          </div>
        </dl>
      </div>
    </li>
  )
}
