import { Button } from '@/components/ui/button'
import { Chip } from '@/components/ui/chip'
import { Disclosure } from '@/components/ui/disclosure'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { DEFAULT_TIMEZONE, type ScheduleFieldErrors, type ScheduleForm } from './payload'
import { MONDAY_FIRST, sameWeekdays, WEEKDAY_NAMES, WEEKDAY_PRESETS, WEEKDAY_SHORT_NAMES } from './schedule-time'

export interface ScheduleTimeFieldsProps {
  form: ScheduleForm
  errors: ScheduleFieldErrors
  disabled: boolean
  /** 只增不减：时区出错时展开「高级」，之后不因错误消失而收起。 */
  advancedOpen: boolean
  onChange: (patch: Partial<ScheduleForm>) => void
}

function errorProps(error: string | undefined, id: string) {
  return error ? { 'aria-invalid': true as const, 'aria-describedby': id } : {}
}

/** 星期芯片（周一在前）与快捷选择、时间；已有 cron 超出这个范围时换成原始表达式输入。「高级」里是时区。 */
export function ScheduleTimeFields({ form, errors, disabled, advancedOpen, onChange }: ScheduleTimeFieldsProps) {
  const toggleDay = (day: number) => {
    onChange({ weekdays: form.weekdays.includes(day) ? form.weekdays.filter((selected) => selected !== day) : [...form.weekdays, day] })
  }

  return (
    <div className="space-y-5">
      {form.mode === 'weekly' ? (
        <>
          <div className="space-y-2">
            <div className="flex min-h-9 items-center justify-between gap-3">
              <span id="schedule-weekdays-label" className="text-sm font-medium">星期</span>
              <div role="group" aria-label="快捷选择" className="flex gap-1.5">
                {WEEKDAY_PRESETS.map((preset) => (
                  <Chip
                    key={preset.label}
                    selected={sameWeekdays(form.weekdays, preset.weekdays)}
                    disabled={disabled}
                    onClick={() => onChange({ weekdays: [...preset.weekdays] })}
                  >
                    {preset.label}
                  </Chip>
                ))}
              </div>
            </div>
            <div
              id="schedule-weekdays"
              role="group"
              aria-labelledby="schedule-weekdays-label"
              aria-describedby={errors.weekdays ? 'schedule-weekdays-error' : undefined}
              className="grid grid-cols-7 gap-1.5"
            >
              {MONDAY_FIRST.map((day) => (
                <Chip
                  key={day}
                  aria-label={WEEKDAY_NAMES[day]}
                  selected={form.weekdays.includes(day)}
                  disabled={disabled}
                  className="h-11 px-0 text-[0.9375rem]"
                  onClick={() => toggleDay(day)}
                >
                  {WEEKDAY_SHORT_NAMES[day]}
                </Chip>
              ))}
            </div>
            {errors.weekdays ? <p id="schedule-weekdays-error" className="text-footnote font-medium text-danger">{errors.weekdays}</p> : null}
          </div>
          <Field label="时间" htmlFor="schedule-time" error={errors.time} errorId="schedule-time-error">
            <Input
              id="schedule-time"
              type="time"
              required
              value={form.time}
              disabled={disabled}
              className="sm:max-w-44"
              {...errorProps(errors.time, 'schedule-time-error')}
              onChange={(event) => onChange({ time: event.target.value })}
            />
          </Field>
        </>
      ) : (
        <div className="space-y-3">
          <InlineAlert tone="warning" role="none" title="按原始 cron 运行">这个表达式超出按星期设置的范围，将原样保存。</InlineAlert>
          <Field
            label="cron 表达式"
            htmlFor="schedule-cron"
            description="五段标准 cron：分 时 日 月 星期。"
            error={errors.cron}
            errorId="schedule-cron-error"
          >
            <Input
              id="schedule-cron"
              value={form.cron}
              maxLength={64}
              disabled={disabled}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              className="font-mono"
              {...errorProps(errors.cron, 'schedule-cron-error')}
              onChange={(event) => onChange({ cron: event.target.value })}
            />
          </Field>
          <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => onChange({ mode: 'weekly', cron: '' })}>
            改为按星期设置
          </Button>
        </div>
      )}
      <Disclosure title="高级" description={`时区 ${form.timezone.trim() || DEFAULT_TIMEZONE}`} defaultOpen={advancedOpen}>
        <Field
          label="时区"
          htmlFor="schedule-timezone"
          hint="IANA 时区名，例如 Asia/Shanghai、UTC；星期与时间按这个时区计算。"
          error={errors.timezone}
          errorId="schedule-timezone-error"
        >
          <Input
            id="schedule-timezone"
            value={form.timezone}
            maxLength={48}
            placeholder={DEFAULT_TIMEZONE}
            disabled={disabled}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            {...errorProps(errors.timezone, 'schedule-timezone-error')}
            onChange={(event) => onChange({ timezone: event.target.value })}
          />
        </Field>
      </Disclosure>
    </div>
  )
}
