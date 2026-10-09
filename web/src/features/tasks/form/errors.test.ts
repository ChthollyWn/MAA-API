import { describe, expect, it } from 'vitest'
import { ApiError, apiErrorFromResponse } from '@/api/errors'
import taskTypesFixture from '../task-types.fixture.json'
import { collectFieldIssues, errorMessage, mapFieldIssues, unwrapHttpError } from './errors'
import type { TaskTypeSchema } from './schema'

const types = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items

function validationError(body: unknown, status = 422): ApiError {
  return apiErrorFromResponse({ status, statusText: 'Unprocessable Entity' }, body)
}

const startUp = { id: 'task-startup', name: 'StartUp' }
const firstFight = { id: 'task-fight-1', name: 'Fight' }
const secondFight = { id: 'task-fight-2', name: 'Fight' }

describe('collectFieldIssues and mapFieldIssues', () => {
  it('maps the details.field shape onto the first task that owns the field', () => {
    const error = validationError({ error: { code: 'TASK_PARAM_INVALID', message: '任务参数无效', details: { field: 'stage', message: '关卡名无效，请检查格式' } } })
    const { issues, message } = collectFieldIssues(error, 2)
    expect(message).toBe('任务参数无效')
    expect(issues).toEqual([{ taskIndex: undefined, field: 'stage', message: '关卡名无效，请检查格式' }])
    expect(mapFieldIssues(issues, [startUp, firstFight], types)).toEqual({ 'task-fight-1': { stage: '关卡名无效，请检查格式' } })
  })

  it('maps pydantic details.fields locations onto the indexed task', () => {
    const error = validationError({
      error: {
        code: 'VALIDATION_ERROR',
        message: '请求参数校验失败（1 处）',
        details: { fields: [{ loc: ['body', 'tasks', 1, 'FightInput', 'stage'], msg: 'String should have at least 1 character', type: 'string_too_short' }] },
      },
    })
    const { issues, message } = collectFieldIssues(error, 2)
    expect(message).toBe('请求参数校验失败（1 处）')
    expect(issues).toEqual([{ taskIndex: 1, field: 'stage', message: 'String should have at least 1 character' }])
    expect(mapFieldIssues(issues, [firstFight, secondFight], types)).toEqual({ 'task-fight-2': { stage: 'String should have at least 1 character' } })
  })

  it('honours task_index on the details.field shape and falls back to the envelope message', () => {
    const error = validationError({ error: { message: '任务参数无效', details: { errors: [{ field: 'medicine', task_index: 1 }] } } })
    const { issues } = collectFieldIssues(error, 2)
    expect(issues).toEqual([{ taskIndex: 1, field: 'medicine', message: '任务参数无效' }])
    expect(mapFieldIssues(issues, [firstFight, secondFight], types)).toEqual({ 'task-fight-2': { medicine: '任务参数无效' } })
  })

  it('reads FastAPI detail arrays and plain envelopes, and drops duplicate issues', () => {
    const { issues } = collectFieldIssues({ detail: [
      { loc: ['body', 'tasks', 0, 'stage'], msg: '重复的错误' },
      { loc: ['body', 'tasks', 0, 'stage'], msg: '重复的错误' },
    ] }, 1)
    expect(issues).toEqual([{ taskIndex: 0, field: 'stage', message: '重复的错误' }])
  })

  it('ignores issues for unknown fields or tasks outside the draft list', () => {
    const issues = [
      { field: 'not_a_field', message: '未知字段' },
      { taskIndex: 5, field: 'stage', message: '越界' },
    ]
    expect(mapFieldIssues(issues, [firstFight], types)).toEqual({})
  })
})

describe('error messages', () => {
  it('explains policy rejections and reads nested messages', () => {
    expect(errorMessage(validationError({ error: { message: 'forbidden' } }, 403))).toContain('没有权限执行此操作（403）')
    expect(errorMessage({ error: { message: '队列已满' } })).toBe('队列已满')
    expect(errorMessage('纯文本错误')).toBe('纯文本错误')
    expect(errorMessage(42)).toBe('请求失败，请稍后重试')
  })

  it('unwraps error envelopes', () => {
    expect(unwrapHttpError({ error: { message: 'x' } })).toEqual({ message: 'x' })
    expect(unwrapHttpError({ message: 'y' })).toEqual({ message: 'y' })
    expect(unwrapHttpError('z')).toEqual({ message: 'z' })
    expect(unwrapHttpError(undefined)).toEqual({ message: '请求失败，请稍后重试' })
  })
})
