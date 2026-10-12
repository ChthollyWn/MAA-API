import { useMemo, useState } from 'react'
import { User } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { FieldSource, OperatorRow } from './api'
import {
  RARITY_COLORS,
  UNOWNED_COLORS,
  avatarUrl,
  moduleTypeGlyph,
  skillIconUrl,
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
  text: string
  level: number
}

const SOURCE_TONES: Record<FieldSource, 'neutral' | 'success' | 'primary' | 'warning'> = {
  recognition: 'neutral',
  manual: 'primary',
  agent: 'success',
  skland: 'warning',
}

/** 图片加载失败或未拥有时用的占位：一个中性图标，保证布局不塌。
 *
 * 刻意**不放名字首字**：那会让同一段文字在 DOM 里出现两次（名字 + 占位），
 * 按文本查询的地方会撞车，读屏也会重复念一遍。 */
function NamePlaceholder() {
  return (
    <span
      aria-hidden="true"
      data-testid="operator-avatar-placeholder"
      className="flex size-full items-center justify-center bg-muted text-muted-foreground"
    >
      <User className="size-5" />
    </span>
  )
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
        text: detail.label ?? moduleTypeGlyph(detail.type),
        level: Number(detail.level),
      }))
  }
  // 老数据（只有 `modules`）与手动填写的模组没有明细：退化成「类型字母 + 等级」，
  // 不伪造 `CCR-` 这种分支码。
  return Object.entries(row.modules ?? {})
    .filter(([, level]) => Number(level) > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([type, level]) => ({ key: type, text: moduleTypeGlyph(type), level: Number(level) }))
}

/**
 * 一张干员卡（M43 规格 §4.4）：稀有度配色 + 头像 + 精英化/等级/潜能 + 技能图标与专精 +
 * 模组标签与等级。
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
  // 记哪些图片加载失败：失败就换占位，不留破图（CDN 不可达、离线、id 变更都会走到）。
  const [broken, setBroken] = useState<string[]>([])
  const isBroken = (key: string) => broken.includes(key)
  const markBroken = (key: string) =>
    setBroken((previous) => (previous.includes(key) ? previous : [...previous, key]))

  const colors = row.own
    ? RARITY_COLORS[row.rarity ?? 0] ?? UNOWNED_COLORS
    : UNOWNED_COLORS
  const sources = rowSources(row)
  const slots = useMemo(() => skillSlots(row), [row])
  const chips = useMemo(() => moduleChips(row), [row])
  const avatarKey = `avatar-${row.char_id}`
  const showAvatar = row.own && !isBroken(avatarKey)

  const unknown = (value: unknown) => value === null || value === undefined

  return (
    <li className="list-none">
      <div
        role="button"
        tabIndex={0}
        data-testid={`operator-${row.char_id}`}
        aria-label={`查看 ${row.name} 的练度`}
        onClick={() => onSelect(row)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            onSelect(row)
          }
        }}
        style={{ ['--card-primary' as string]: colors.primary, ['--card-secondary' as string]: colors.secondary }}
        className={`group flex w-full flex-col gap-2 rounded-xl border bg-card p-2.5 text-left shadow-card transition-colors hover:border-[var(--card-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
          row.own ? '' : 'opacity-70 grayscale'
        }`}
      >
        <div className="flex items-start gap-2.5">
          {/* 稀有度色带 + 头像：色带让扫视时先认出星级，头像给身份。 */}
          <span
            aria-hidden="true"
            className="h-12 w-1 shrink-0 rounded-full"
            style={{ background: `linear-gradient(180deg, var(--card-secondary), var(--card-primary))` }}
          />
          <span className="relative size-12 shrink-0 overflow-hidden rounded-lg border bg-muted">
            {showAvatar ? (
              <img
                src={avatarUrl(row.char_id)}
                alt=""
                loading="lazy"
                decoding="async"
                data-testid={`operator-avatar-${row.char_id}`}
                onError={() => markBroken(avatarKey)}
                className="size-full object-cover"
              />
            ) : (
              <NamePlaceholder />
            )}
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5">
              <span className="truncate text-[0.9375rem] font-medium" title={row.name}>
                {row.name}
              </span>
              {row.own ? null : <Badge variant="outline">未拥有</Badge>}
              {/* 显式的编辑入口：整卡可点是主路径，但这颗按钮让「能编辑」这件事看得见，
                  也让键盘/读屏用户不必先理解卡片本身是个按钮。 */}
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
            </span>
            <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-footnote text-muted-foreground">
              <Badge variant="neutral">{rarityLabel(row.rarity)}</Badge>
              <span>{professionLabel(row.profession)}</span>
            </span>
          </span>
        </div>

        {/* 技能：槽位图标 + 专精数字。没有 `skill_ids`（老数据 / 纯识别行）时不画图标，
            只留槽位编号——不拿别的图冒充；**未拥有的干员整块不画**（规格 §3.1.5：没拥有
            就谈不上练度，画一排空槽只是噪声）。 */}
        {row.own && slots.length ? (
          <div className="flex items-center gap-1.5">
            {slots.map((slot) => {
              const iconKey = `skill-${row.char_id}-${slot.slot}`
              const showIcon = row.own && slot.skillId !== null && !isBroken(iconKey)
              return (
                <span
                  key={slot.slot}
                  data-testid={`operator-skill-${row.char_id}-${slot.slot}`}
                  className="flex items-center gap-1 rounded-md border bg-muted/40 px-1 py-0.5"
                >
                  <span className="relative size-5 shrink-0 overflow-hidden rounded bg-muted">
                    {showIcon && slot.skillId ? (
                      <img
                        src={skillIconUrl(slot.skillId)}
                        alt=""
                        loading="lazy"
                        decoding="async"
                        data-testid={`operator-skill-icon-${row.char_id}-${slot.slot}`}
                        onError={() => markBroken(iconKey)}
                        className="size-full object-contain"
                      />
                    ) : null}
                  </span>
                  <span
                    className={`text-footnote tabular-nums ${
                      slot.mastery && slot.mastery > 0 ? 'font-medium text-foreground' : 'text-muted-foreground'
                    }`}
                  >
                    {slot.mastery === null ? '—' : slot.mastery}
                  </span>
                </span>
              )
            })}
          </div>
        ) : null}

        {/* 模组：`CCR-Y` 这类标签 + 等级。图标本轮不做（规格 §3.2）；未拥有同样不画。 */}
        {row.own && chips.length ? (
          <div className="flex flex-wrap items-center gap-1.5">
            {chips.map((chip) => (
              <span
                key={chip.key}
                data-testid={`operator-module-${row.char_id}-${chip.key}`}
                className="flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-footnote tabular-nums"
                style={{ borderColor: 'var(--card-primary)' }}
              >
                <span className="font-medium">{chip.text}</span>
                <span className="text-muted-foreground">{chip.level}</span>
              </span>
            ))}
          </div>
        ) : null}

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
        <dl className="grid grid-cols-3 gap-x-2 gap-y-0.5 text-footnote">
          <dt className="text-muted-foreground">精英化</dt>
          <dd>{eliteLabel(row.elite)}</dd>
          <dt className="text-muted-foreground">等级</dt>
          <dd className="tabular-nums">{trainingValueText(row.level)}</dd>
          <dt className="text-muted-foreground">潜能</dt>
          <dd className="tabular-nums">{trainingValueText(row.potential)}</dd>
          <dt className="sr-only">技能等级</dt>
          <dd className="sr-only">{trainingValueText(row.skill_level)}</dd>
          <dt className="sr-only">专精</dt>
          <dd className="sr-only">{masteryText(row.masteries)}</dd>
          <dt className="sr-only">模组</dt>
          <dd className="sr-only">{moduleText(row.modules)}</dd>
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
