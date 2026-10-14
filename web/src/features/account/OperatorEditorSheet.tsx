import { useEffect, useId, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { User } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { SwitchRow } from '@/components/ui/switch'
import { toast } from '@/components/ui/toaster'
import { AssetImage } from './AssetImage'
import { accountKeys, patchOperator, type FieldSource, type OperatorPatch, type OperatorRow } from './api'
import { avatarPath, moduleTypeGlyph, RARITY_COLORS, skillIconPath, UNOWNED_COLORS } from './assets'
import {
  MAX_LEVEL,
  MAX_MODULE_LEVEL,
  MAX_POTENTIAL,
  MAX_SKILL_LEVEL,
  MODULE_TYPES,
  SKILL_SLOTS,
  buildPatch,
  canEditMasteries,
  canEditModules,
  draftFromRow,
  maxElite,
  maxEliteLabel,
  validateDraft,
  type OperatorDraft,
} from './constraints'
import { UNKNOWN_TEXT, errorText, fieldSource, professionLabel, rarityLabel, sourceLabel } from './format'

function SourceTag({ source, field }: { source: FieldSource | null; field: string }) {
  const label = sourceLabel(source)
  if (!label || !source) return null
  return <Badge variant="outline" data-testid={`field-source-${field}`}>{label}</Badge>
}

export interface OperatorEditorSheetProps {
  row: OperatorRow
  /** 关闭面板（保存成功后也会调用）。 */
  onClose: () => void
}

/**
 * 干员编辑抽屉（规格 §7.5、§7.4）：精英化上限按稀有度，专精只在精二且技能 7 级，
 * 模组只在精二且类型固定 X/Y/D/A，技能数按稀有度给默认值。保存走
 * `PATCH /api/account/operators/{char_id}`，只提交改动过的字段。
 */
export function OperatorEditorSheet({ row, onClose }: OperatorEditorSheetProps) {
  const baseId = useId()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<OperatorDraft>(() => draftFromRow(row))
  const errors = validateDraft(draft, row.rarity)
  const patch = buildPatch(row, draft)
  const changeCount = Object.keys(patch).length
  const invalid = Object.keys(errors).length > 0
  const eliteCap = maxElite(row.rarity)
  const masteriesEditable = canEditMasteries(draft)
  const modulesEditable = canEditModules(draft)
  const colors = draft.own
    ? RARITY_COLORS[row.rarity ?? 0] ?? UNOWNED_COLORS
    : UNOWNED_COLORS

  const set = <K extends keyof OperatorDraft>(key: K, value: OperatorDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }))
  const setMastery = (slot: string, level: number) =>
    setDraft((current) => ({
      ...current,
      masteries: { ...(current.masteries ?? { '1': 0, '2': 0, '3': 0 }), [slot]: level },
    }))
  const setModule = (type: string, level: number) =>
    setDraft((current) => {
      const modules = { ...(current.modules ?? {}) }
      if (level > 0) modules[type] = level
      else delete modules[type]
      return { ...current, modules }
    })

  const save = useMutation({
    mutationFn: (body: OperatorPatch) => patchOperator(row.char_id, body),
    onSuccess: async () => {
      toast.success(`已保存「${row.name}」的修改`)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: accountKeys.operatorsAll() }),
        queryClient.invalidateQueries({ queryKey: accountKeys.summary() }),
      ])
      onClose()
    },
  })

  useEffect(() => {
    setDraft((current) => {
      const next = { ...current }
      let changed = false
      if (!canEditMasteries(current) && current.masteries !== null && Object.values(current.masteries).some((level) => level > 0)) {
        next.masteries = { '1': 0, '2': 0, '3': 0 }
        changed = true
      }
      if (!canEditModules(current) && current.modules !== null && Object.keys(current.modules).length > 0) {
        next.modules = {}
        changed = true
      }
      return changed ? next : current
    })
  }, [draft.elite, draft.skillLevel])

  return (
    <ResponsiveSheet
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose()
      }}
      desktop="panel"
      title={`编辑 ${row.name}`}
      description={`${rarityLabel(row.rarity)} · ${professionLabel(row.profession)}`}
      footer={(
        <div className="space-y-2">
          {save.error ? <InlineAlert tone="danger">保存失败：{errorText(save.error)}</InlineAlert> : null}
          <div className="grid grid-cols-2 gap-2">
            <Button variant="secondary" disabled={save.isPending} onClick={onClose}>取消</Button>
            <Button
              disabled={invalid || changeCount === 0}
              loading={save.isPending}
              onClick={() => save.mutate(patch)}
            >
              {save.isPending ? '正在保存…' : changeCount > 0 ? `保存 ${changeCount} 项改动` : '保存'}
            </Button>
          </div>
        </div>
      )}
    >
      <div className="space-y-4 pt-1">
        {/* 顶部干员概览与持有开关 */}
        <div
          className="relative overflow-hidden rounded-xl border bg-surface-2/40 p-3.5 pl-4"
          style={{ ['--card-primary' as string]: colors.primary, ['--card-secondary' as string]: colors.secondary }}
        >
          <span
            aria-hidden="true"
            className="absolute inset-y-0 left-0 w-1"
            style={{ background: `linear-gradient(180deg, var(--card-secondary), var(--card-primary))` }}
          />
          <div className="mb-3 flex items-center gap-3 border-b border-border/60 pb-3">
            <span className="relative size-12 shrink-0 overflow-hidden rounded-xl border bg-muted">
              {draft.own ? (
                <AssetImage
                  path={avatarPath(row.char_id)}
                  testId={`editor-avatar-${row.char_id}`}
                  className="size-full object-cover"
                />
              ) : (
                <span className="flex size-full items-center justify-center bg-muted text-muted-foreground">
                  <User className="size-5" />
                </span>
              )}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="truncate text-base font-semibold">{row.name}</span>
                <Badge variant="neutral">{rarityLabel(row.rarity)}</Badge>
              </div>
              <p className="mt-0.5 text-footnote text-muted-foreground">
                {professionLabel(row.profession)}
                {typeof row.favor_percent === 'number' ? ` · 信赖 ${row.favor_percent}%` : ''}
              </p>
            </div>
          </div>
          <SwitchRow
            label="已拥有"
            description="未拥有的干员不参与养成规划"
            checked={draft.own}
            onCheckedChange={(checked) => set('own', checked)}
          />
        </div>

        {/* 基础练度分组 */}
        <div className="space-y-4 rounded-xl border bg-card p-3.5 shadow-card">
          <h3 className="text-sm font-semibold text-foreground">基础练度</h3>
          <Field
            label="精英化"
            htmlFor={`${baseId}-elite`}
            labelAside={<SourceTag source={fieldSource(row, 'elite')} field="elite" />}
            description={`${row.rarity}★ 干员的精英化上限是${maxEliteLabel(row.rarity)}`}
            error={errors.elite}
          >
            <NativeSelect
              id={`${baseId}-elite`}
              value={draft.elite === null ? '' : String(draft.elite)}
              onChange={(event) => set('elite', event.currentTarget.value === '' ? null : Number(event.currentTarget.value))}
            >
              {row.elite == null ? <option value="">{UNKNOWN_TEXT}</option> : null}
              {Array.from({ length: eliteCap + 1 }, (_, elite) => (
                <option key={elite} value={elite}>{['精零', '精一', '精二'][elite]}</option>
              ))}
            </NativeSelect>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field
              label="等级"
              htmlFor={`${baseId}-level`}
              labelAside={<SourceTag source={fieldSource(row, 'level')} field="level" />}
              error={errors.level}
            >
              <Input
                id={`${baseId}-level`}
                type="number"
                inputMode="numeric"
                min={1}
                max={MAX_LEVEL}
                placeholder={UNKNOWN_TEXT}
                value={draft.level === null ? '' : draft.level}
                onChange={(event) => set('level', event.currentTarget.value === '' ? null : Number(event.currentTarget.value))}
              />
            </Field>
            <Field
              label="潜能"
              htmlFor={`${baseId}-potential`}
              labelAside={<SourceTag source={fieldSource(row, 'potential')} field="potential" />}
              error={errors.potential}
            >
              <Input
                id={`${baseId}-potential`}
                type="number"
                inputMode="numeric"
                min={1}
                max={MAX_POTENTIAL}
                placeholder={UNKNOWN_TEXT}
                value={draft.potential === null ? '' : draft.potential}
                onChange={(event) => set('potential', event.currentTarget.value === '' ? null : Number(event.currentTarget.value))}
              />
            </Field>
          </div>
        </div>

        {/* 技能与专精分组 */}
        <div className="space-y-4 rounded-xl border bg-card p-3.5 shadow-card">
          <h3 className="text-sm font-semibold text-foreground">技能与专精</h3>
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="技能等级"
              htmlFor={`${baseId}-skill`}
              labelAside={<SourceTag source={fieldSource(row, 'skill_level')} field="skill_level" />}
              error={errors.skill_level}
            >
              <NativeSelect
                id={`${baseId}-skill`}
                value={draft.skillLevel === null ? '' : String(draft.skillLevel)}
                onChange={(event) => set('skillLevel', event.currentTarget.value === '' ? null : Number(event.currentTarget.value))}
              >
                {row.skill_level == null ? <option value="">{UNKNOWN_TEXT}</option> : null}
                {Array.from({ length: MAX_SKILL_LEVEL }, (_, index) => index + 1).map((level) => (
                  <option key={level} value={level}>{level} 级</option>
                ))}
              </NativeSelect>
            </Field>
            <Field
              label="技能数"
              htmlFor={`${baseId}-skill-count`}
              description="决定下方专精槽位数"
            >
              <NativeSelect
                id={`${baseId}-skill-count`}
                value={String(draft.skillCount)}
                onChange={(event) => set('skillCount', Number(event.currentTarget.value))}
              >
                {SKILL_SLOTS.map((_, index) => index + 1).map((count) => (
                  <option key={count} value={count}>{count} 个</option>
                ))}
              </NativeSelect>
            </Field>
          </div>

          <fieldset className="space-y-2 border-t border-border/60 pt-3">
            <legend className="flex items-center gap-2 text-sm font-medium">
              专精
              <SourceTag source={fieldSource(row, 'masteries')} field="masteries" />
            </legend>
            <p className="text-footnote text-muted-foreground">
              {!masteriesEditable
                ? '专精只在精二且技能等级 7 时可填。'
                : draft.masteries === null
                  ? '未识别：填任意一档算作补录；0 表示未专精，最高 3 级。'
                  : '0 表示未专精，最高 3 级。'}
            </p>
            <div className="grid grid-cols-3 gap-2.5">
              {SKILL_SLOTS.slice(0, draft.skillCount).map((slot, index) => {
                const skillId = row.skill_ids?.[index] ?? null
                return (
                  <label key={slot} className="space-y-1.5 rounded-lg bg-surface-2/50 p-2 text-footnote text-muted-foreground">
                    <span className="flex items-center gap-1.5 font-medium text-foreground">
                      {draft.own && skillId ? (
                        <span className="size-5 shrink-0 overflow-hidden rounded border bg-muted">
                          <AssetImage
                            path={skillIconPath(skillId)}
                            testId={`editor-skill-icon-${row.char_id}-${slot}`}
                            className="size-full object-contain"
                          />
                        </span>
                      ) : null}
                      <span>S{slot}</span>
                    </span>
                    <NativeSelect
                      aria-label={`S${slot} 专精`}
                      disabled={!masteriesEditable}
                      value={draft.masteries === null ? '' : String(draft.masteries[slot] ?? 0)}
                      onChange={(event) => {
                        if (event.currentTarget.value === '') return
                        setMastery(slot, Number(event.currentTarget.value))
                      }}
                    >
                      {draft.masteries === null ? <option value="">{UNKNOWN_TEXT}</option> : null}
                      {[0, 1, 2, 3].map((level) => <option key={level} value={level}>{level}</option>)}
                    </NativeSelect>
                  </label>
                )
              })}
            </div>
            {errors.masteries ? <p className="text-footnote font-medium text-danger">{errors.masteries}</p> : null}
          </fieldset>
        </div>

        {/* 模组分组 */}
        <fieldset className="space-y-2.5 rounded-xl border bg-card p-3.5 shadow-card">
          <legend className="flex items-center gap-2 text-sm font-semibold text-foreground">
            模组
            <SourceTag source={fieldSource(row, 'modules')} field="modules" />
          </legend>
          <p className="text-footnote text-muted-foreground">
            {!modulesEditable
              ? '模组只在精二时可填。'
              : draft.modules === null
                ? '未识别：填任意一个类型算作补录；类型固定为 X / Y / D / A，等级 1–3。'
                : '类型固定为 X / Y / D / A，等级 1–3。'}
          </p>
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            {MODULE_TYPES.map((type) => (
              <label key={type} className="space-y-1.5 rounded-lg bg-surface-2/50 p-2 text-footnote text-muted-foreground">
                <span className="flex items-center justify-between font-medium text-foreground">
                  <span>{type} 模组</span>
                  <span className="font-mono text-xs text-muted-foreground">{moduleTypeGlyph(type)}</span>
                </span>
                <NativeSelect
                  aria-label={`${type} 模组等级`}
                  disabled={!modulesEditable}
                  value={draft.modules === null ? '' : String(draft.modules[type] ?? 0)}
                  onChange={(event) => {
                    if (event.currentTarget.value === '') return
                    setModule(type, Number(event.currentTarget.value))
                  }}
                >
                  {draft.modules === null ? <option value="">{UNKNOWN_TEXT}</option> : null}
                  <option value={0}>未装备</option>
                  {Array.from({ length: MAX_MODULE_LEVEL }, (_, index) => index + 1).map((level) => (
                    <option key={level} value={level}>{level} 级</option>
                  ))}
                </NativeSelect>
              </label>
            ))}
          </div>
          {errors.modules ? <p className="text-footnote font-medium text-danger">{errors.modules}</p> : null}
        </fieldset>
      </div>
    </ResponsiveSheet>
  )
}
