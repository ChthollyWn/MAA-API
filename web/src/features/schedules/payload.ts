import { isObject } from '@/features/tasks/form'
import type { components } from '@/types/api'
import { buildWeeklyCron, parseWeeklyCron } from './schedule-time'

export type ScheduleView = components['schemas']['ScheduleView']
export type ScheduleRun = components['schemas']['ScheduleRunView']
export type ScheduleWrite = components['schemas']['ScheduleWrite']
type ScheduleTemplate = ScheduleWrite['template']
type ScheduleTemplateTask = ScheduleTemplate[number]
/** 扁平任务模板 `{ name, ...改过的参数 }`，与 `POST /api/pipelines` 的 tasks 相同。 */
export type TemplateTask = Record<string, unknown>

export const DEFAULT_TIMEZONE = 'Asia/Shanghai'
export const DEFAULT_TIME = '07:00'
const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]

/** 定时任务的排队方式沿用旧页面的固定值：最低优先级、错过触发 5 分钟内仍执行、不补跑、上一次未完成时跳过。 */
export const SCHEDULE_POLICY = { priority: 2, misfire_grace_seconds: 300, catch_up: false, skip_if_running: true } as const

export class ScheduleTemplateError extends Error {
  /** 出错任务在模板里的序号（从 0 开始）。 */
  readonly index: number
  readonly field: string | undefined

  constructor(message: string, index: number, field?: string) {
    super(message)
    this.name = 'ScheduleTemplateError'
    this.index = index
    this.field = field
  }
}

/**
 * 白名单内的一条任务：参数原样透传，只固定判别字段 `name`。
 *
 * 生成类型 `api.d.ts` 把带 `default` 的参数标成了必填（openapi-typescript 对 `default` 的已知行为），
 * 而模板本来就允许只写改过的字段——省略的参数由服务端按默认值补齐，所以这里显式断言一次。
 */
function templateTask(task: TemplateTask, name: ScheduleTemplateTask['name']): ScheduleTemplateTask {
  return { ...task, name } as ScheduleTemplateTask
}

/**
 * 定时任务模板白名单：只放行服务端可定时的十种任务，Copilot 只保留 `asset_id` 与 `formation`。
 * 不合格的任务抛出 `ScheduleTemplateError`，带上它的序号。
 */
export function toScheduleTemplate(tasks: readonly TemplateTask[]): ScheduleTemplate {
  return tasks.map((task, index): ScheduleTemplateTask => {
    switch (task.name) {
      case 'StartUp': return templateTask(task, 'StartUp')
      case 'CloseDown': return templateTask(task, 'CloseDown')
      case 'Fight': return templateTask(task, 'Fight')
      case 'Recruit': return templateTask(task, 'Recruit')
      case 'Infrast': return templateTask(task, 'Infrast')
      case 'Mall': return templateTask(task, 'Mall')
      case 'Award': return templateTask(task, 'Award')
      case 'Roguelike': return templateTask(task, 'Roguelike')
      case 'Reclamation': return templateTask(task, 'Reclamation')
      case 'Copilot': {
        if (typeof task.asset_id !== 'string' || !task.asset_id) {
          throw new ScheduleTemplateError('请选择一个已保存的 Copilot 作业。', index, 'asset_id')
        }
        return { name: 'Copilot', asset_id: task.asset_id, formation: task.formation === true }
      }
      default:
        throw new ScheduleTemplateError(`定时任务不支持「${String(task.name)}」，请删除第 ${index + 1} 项。`, index)
    }
  })
}

export interface ScheduleForm {
  name: string
  /** `cron`：已有表达式超出按星期设置的范围，按原始表达式编辑并原样保存。 */
  mode: 'weekly' | 'cron'
  weekdays: number[]
  time: string
  cron: string
  timezone: string
  enabled: boolean
}

export function emptyScheduleForm(name = ''): ScheduleForm {
  return { name, mode: 'weekly', weekdays: [...EVERY_DAY], time: DEFAULT_TIME, cron: '', timezone: DEFAULT_TIMEZONE, enabled: true }
}

export function scheduleFormFromView(view: Pick<ScheduleView, 'name' | 'cron' | 'timezone' | 'enabled'>): ScheduleForm {
  const base = { ...emptyScheduleForm(view.name), timezone: view.timezone || DEFAULT_TIMEZONE, enabled: view.enabled }
  const visual = parseWeeklyCron(view.cron, base.timezone)
  return visual ? { ...base, weekdays: visual.weekdays, time: visual.time } : { ...base, mode: 'cron', cron: view.cron }
}

export function scheduleCron(form: ScheduleForm): string | null {
  return form.mode === 'cron' ? form.cron.trim() || null : buildWeeklyCron(form.weekdays, form.time)
}

export type ScheduleField = 'name' | 'weekdays' | 'time' | 'cron' | 'timezone' | 'tasks'
export type ScheduleFieldErrors = Partial<Record<ScheduleField, string>>

/** 提交前的本地检查；服务端仍会完整校验。 */
export function validateScheduleForm(form: ScheduleForm, taskCount: number): ScheduleFieldErrors {
  const errors: ScheduleFieldErrors = {}
  if (!form.name.trim()) errors.name = '请填写名称。'
  if (form.mode === 'cron') {
    if (!form.cron.trim()) errors.cron = '请填写 cron 表达式。'
  } else {
    if (form.weekdays.length === 0) errors.weekdays = '至少选择一天。'
    if (!buildWeeklyCron(EVERY_DAY, form.time)) errors.time = '请填写有效的时间。'
  }
  if (taskCount === 0) errors.tasks = '至少添加一个任务。'
  return errors
}

/** `POST /api/schedules` 与 `PUT /api/schedules/{id}` 的请求体；调用前先用 `validateScheduleForm` 检查。 */
export function buildSchedulePayload(form: ScheduleForm, template: readonly TemplateTask[]): ScheduleWrite {
  const cron = scheduleCron(form)
  if (!cron) throw new Error('请先设置执行时间。')
  return {
    name: form.name.trim(),
    cron,
    timezone: form.timezone.trim() || DEFAULT_TIMEZONE,
    template: toScheduleTemplate(template),
    enabled: form.enabled,
    ...SCHEDULE_POLICY,
  }
}

/** 用来判断有没有未保存的更改：只比较会保存的内容，星期顺序与草稿 id 不算改动。 */
export function scheduleSnapshot(form: ScheduleForm, template: readonly TemplateTask[]): string {
  const when = form.mode === 'cron'
    ? { cron: form.cron.trim() }
    : { weekdays: [...new Set(form.weekdays)].sort((left, right) => left - right), time: form.time }
  return JSON.stringify({ name: form.name.trim(), mode: form.mode, ...when, timezone: form.timezone.trim(), enabled: form.enabled, template })
}

export interface SchedulePrefill {
  source: 'composer' | 'api-console'
  name: string
  template: TemplateTask[]
}

function namedTasks(value: unknown): TemplateTask[] {
  return Array.isArray(value) ? value.filter((task): task is TemplateTask => isObject(task) && typeof task.name === 'string') : []
}

/** 编排器「存为定时」（`composerPrefill`）与 API 调试台（`apiConsolePrefill`）经路由 state 带来的任务模板。 */
export function readSchedulePrefill(state: unknown): SchedulePrefill | null {
  if (!isObject(state)) return null
  if (isObject(state.composerPrefill)) {
    const template = namedTasks(state.composerPrefill.template)
    const name = typeof state.composerPrefill.name === 'string' ? state.composerPrefill.name.trim() : ''
    if (template.length > 0) return { source: 'composer', name, template }
  }
  if (isObject(state.apiConsolePrefill)) {
    const template = namedTasks(state.apiConsolePrefill.template)
    if (template.length > 0) return { source: 'api-console', name: '', template }
  }
  return null
}

export function hasSchedulePrefill(state: unknown): boolean {
  return isObject(state) && ('composerPrefill' in state || 'apiConsolePrefill' in state)
}
