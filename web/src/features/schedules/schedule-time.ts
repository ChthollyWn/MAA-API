export interface WeeklyScheduleTime {
  weekdays: number[]
  time: string
  timezone: string
}

const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]

function uniqueSortedDays(days: number[]): number[] {
  return [...new Set(days)].filter((day) => Number.isInteger(day) && day >= 0 && day <= 6).sort((left, right) => left - right)
}

function expandCronWeekdays(value: string): number[] | null {
  if (value === '*') return [...WEEKDAYS]
  const days = new Set<number>()

  for (const segment of value.split(',')) {
    const range = /^(\d)-(\d)$/.exec(segment)
    if (range) {
      const start = Number(range[1])
      const end = Number(range[2])
      if (start > end || end > 7) return null
      for (let day = start; day <= end; day += 1) days.add(day === 7 ? 0 : day)
      continue
    }

    if (!/^\d$/.test(segment)) return null
    const day = Number(segment)
    if (day > 7) return null
    days.add(day === 7 ? 0 : day)
  }

  return days.size > 0 ? uniqueSortedDays([...days]) : null
}

/**
 * Convert a visual weekly schedule to a five-field POSIX cron expression.
 * Cron weekdays use 0 for Sunday and 1–6 for Monday–Saturday. The selected
 * clock time is wall time in the separately stored timezone.
 */
export function buildWeeklyCron(weekdays: number[], time: string): string | null {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(time)
  const days = uniqueSortedDays(weekdays)
  if (!match || days.length === 0) return null

  const hour = Number(match[1])
  const minute = Number(match[2])
  const weekdayField = days.length === 7 ? '*' : days.join(',')
  return minute + ' ' + hour + ' * * ' + weekdayField
}

const MONDAY_FIRST = [1, 2, 3, 4, 5, 6, 0]
const WEEKDAY_NAMES = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/** 把星期集合写成人话：每天、工作日、周末、周一至周三、周一、周三、周五。 */
export function describeWeekdays(weekdays: number[]): string {
  const days = uniqueSortedDays(weekdays)
  if (days.length === 7) return '每天'
  const key = days.join(',')
  if (key === '1,2,3,4,5') return '工作日'
  if (key === '0,6') return '周末'
  const ordered = MONDAY_FIRST.filter((day) => days.includes(day))
  const positions = ordered.map((day) => MONDAY_FIRST.indexOf(day))
  const consecutive = positions.every((position, index) => index === 0 || position === positions[index - 1]! + 1)
  if (ordered.length >= 3 && consecutive) {
    return `${WEEKDAY_NAMES[ordered[0]!]}至${WEEKDAY_NAMES[ordered[ordered.length - 1]!]}`
  }
  return ordered.map((day) => WEEKDAY_NAMES[day]).join('、')
}

/** 可视化范围内的 cron 返回「工作日 04:00」，其余返回 null，由调用方显示原始表达式。 */
export function describeSchedule(cron: string): string | null {
  const parsed = parseWeeklyCron(cron)
  return parsed ? `${describeWeekdays(parsed.weekdays)} ${parsed.time}` : null
}

/**
 * Parse the supported weekly cron subset. Expressions outside this subset are
 * intentionally returned as unknown so the UI can preserve and edit the raw
 * cron value without pretending its schedule is understood.
 */
export function parseWeeklyCron(cron: string, timezone = 'UTC'): WeeklyScheduleTime | null {
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return null

  const [minuteText, hourText, dayOfMonth, month, weekdayText] = parts
  if (!/^\d{1,2}$/.test(minuteText) || !/^\d{1,2}$/.test(hourText)) return null
  if (dayOfMonth !== '*' || month !== '*') return null

  const minute = Number(minuteText)
  const hour = Number(hourText)
  if (minute < 0 || minute > 59 || hour < 0 || hour > 23) return null

  const weekdays = expandCronWeekdays(weekdayText)
  if (!weekdays) return null

  return {
    weekdays,
    time: String(hour).padStart(2, '0') + ':' + String(minute).padStart(2, '0'),
    timezone,
  }
}
