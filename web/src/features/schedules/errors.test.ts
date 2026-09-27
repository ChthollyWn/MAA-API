import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/errors'
import taskTypesFixture from '@/features/tasks/task-types.fixture.json'
import type { TaskDraft, TaskTypeSchema } from '@/features/tasks/form'
import { describeSaveFailure, pruneTaskErrors } from './errors'

const taskTypes = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items
const drafts: TaskDraft[] = [
  { id: 'start', name: 'StartUp', params: {} },
  { id: 'fight', name: 'Fight', params: { stage: '' } },
  { id: 'rogue', name: 'Roguelike', params: { mode: 2 } },
]

function apiError(status: number, code: string, message: string, details: unknown = {}) {
  return new ApiError({ status, code, message, details, body: { error: { code, message, details } } })
}

function context(overrides: Partial<Parameters<typeof describeSaveFailure>[1]> = {}) {
  return { name: '重复日常', mode: 'weekly' as const, timezone: 'Asia/Shanghai', drafts, taskTypes, ...overrides }
}

describe('schedule save failures', () => {
  it('marks the name and explains a name conflict (S4)', () => {
    expect(describeSaveFailure(apiError(409, 'SCHEDULE_NAME_CONFLICT', '已存在同名定时任务', { name: '重复日常' }), context())).toEqual({
      fields: { name: '已有同名的定时任务。' },
      tasks: {},
      notice: { title: '名称已被使用', message: '已有同名的定时任务「重复日常」，请换一个名称。' },
    })
  })

  it('maps pydantic template locations to the indexed task and body locations to the form', () => {
    const failure = describeSaveFailure(apiError(422, 'VALIDATION_ERROR', '请求参数校验失败（2 处）', {
      fields: [
        { loc: ['body', 'template', 1, 'Fight', 'stage'], msg: 'String should have at least 1 character', type: 'string_too_short' },
        { loc: ['body', 'name'], msg: 'String should have at most 64 characters', type: 'string_too_long' },
        { loc: ['body', 'template', 0, 'StartUp', 'unknown_field'], msg: 'Extra inputs are not permitted', type: 'extra_forbidden' },
      ],
    }), context())
    expect(failure.tasks).toEqual({ fight: { stage: 'String should have at least 1 character' } })
    expect(failure.fields).toEqual({ name: 'String should have at most 64 characters' })
    expect(failure.notice).toBeNull()
  })

  it('reads template issues from TASK_PARAM_INVALID and a bare details.field', () => {
    const issues = describeSaveFailure(apiError(422, 'TASK_PARAM_INVALID', '定时任务模板参数校验失败', {
      issues: [{ loc: ['tasks', 1, 'Fight', 'stage'], msg: '关卡名无效', type: 'value_error' }],
    }), context())
    expect(issues.tasks).toEqual({ fight: { stage: '关卡名无效' } })

    const deprecated = describeSaveFailure(apiError(400, 'TASK_PARAM_DEPRECATED', 'Roguelike.mode=2 已弃用', { field: 'mode', value: 2 }), context())
    expect(deprecated.tasks).toEqual({ rogue: { mode: 'Roguelike.mode=2 已弃用' } })
  })

  it('only reports form fields when nothing lands on a task', () => {
    const failure = describeSaveFailure(apiError(422, 'VALIDATION_ERROR', '请求参数校验失败（1 处）', {
      fields: [{ loc: ['body', 'timezone'], msg: 'String should have at most 48 characters', type: 'string_too_long' }],
    }), context())
    expect(failure).toEqual({
      fields: { timezone: 'String should have at most 48 characters' },
      tasks: {},
      notice: { title: '校验未通过', message: '请检查标出的内容。' },
    })
  })

  it('blames the timezone for an invalid cron unless a raw expression is being edited with a valid timezone', () => {
    const invalid = apiError(400, 'SCHEDULE_CRON_INVALID', 'cron 必须是有效的五段标准表达式，时区须为 IANA 名称', { cron: '0 7 * * *', timezone: 'Mars/Olympus' })
    const notice = { title: '时间设置无效', message: 'cron 必须是有效的五段标准表达式，时区须为 IANA 名称' }
    expect(describeSaveFailure(invalid, context({ timezone: 'Mars/Olympus' }))).toEqual({ fields: { timezone: '不是有效的时区名，例如 Asia/Shanghai。' }, tasks: {}, notice })
    expect(describeSaveFailure(invalid, context({ mode: 'cron', timezone: 'Asia/Shanghai' }))).toEqual({ fields: { cron: '不是有效的五段 cron 表达式。' }, tasks: {}, notice })
    expect(describeSaveFailure(invalid, context({ mode: 'cron', timezone: 'Mars/Olympus' })).fields).toEqual({ timezone: '不是有效的时区名，例如 Asia/Shanghai。' })
  })

  it('falls back to the server message for other validation and request failures', () => {
    expect(describeSaveFailure(apiError(400, 'UNKNOWN_TASK_TYPE', '未知任务类型：SingleStep', { fields: [{ loc: ['body', 'template', 0], msg: 'bad tag' }] }), context())).toEqual({
      fields: {}, tasks: {}, notice: { title: '校验未通过', message: '未知任务类型：SingleStep' },
    })
    expect(describeSaveFailure(apiError(500, 'INTERNAL_ERROR', '服务内部错误'), context()).notice).toEqual({ title: '保存失败', message: '服务内部错误' })
    expect(describeSaveFailure(new TypeError('Failed to fetch'), context()).notice).toEqual({ title: '保存失败', message: 'Failed to fetch' })
  })
})

describe('task error pruning', () => {
  it('drops errors of edited fields and removed tasks and keeps the rest', () => {
    const errors = { fight: { stage: '关卡名无效', medicine: '太多了' }, rogue: { mode: '已弃用' } }
    const next: TaskDraft[] = [
      { id: 'start', name: 'StartUp', params: {} },
      { id: 'fight', name: 'Fight', params: { stage: '1-7' } },
    ]
    expect(pruneTaskErrors(errors, drafts, next)).toEqual({ fight: { medicine: '太多了' } })
    expect(pruneTaskErrors(errors, drafts, drafts)).toBe(errors)
  })
})
