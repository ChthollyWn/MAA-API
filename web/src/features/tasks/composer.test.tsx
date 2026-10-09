import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { materializeDefaults } from './form'
import { useComposerStore } from './composer'
import { installTaskApi, renderTaskArea, taskTypeEnvelope } from './test-utils'

const typeByName = (name: string) => {
  const found = taskTypeEnvelope.items.find((type) => type.name === name)
  if (!found) throw new Error(`fixture 缺少任务类型 ${name}`)
  return found
}
/** M17 起新建/复制/从模板加载都会预填 schema 默认值，提交体也带着它们。 */
const withDefaults = (name: string, params: Record<string, unknown> = {}) => ({ name, ...materializeDefaults(typeByName(name), params) })

async function openTypeSheet() {
  fireEvent.click(await screen.findByRole('button', { name: '添加任务' }))
  return screen.findByRole('dialog', { name: '添加任务' })
}

/** 从类型面板添加一个任务；添加后直接打开它的编辑器。 */
async function addTask(label: string) {
  const sheet = await openTypeSheet()
  fireEvent.click(within(sheet).getByRole('button', { name: new RegExp(`^${label}`) }))
  return screen.findByRole('dialog', { name: label })
}

async function closeEditor(editor: HTMLElement) {
  fireEvent.click(within(editor).getByRole('button', { name: '完成' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
}

async function openRowMenu(position: number, label: string) {
  fireEvent.click(screen.getByRole('button', { name: `第 ${position} 项的更多操作` }))
  return screen.findByRole('dialog', { name: `第 ${position} 项 · ${label}` })
}

function taskRows() {
  return screen.getAllByRole('button', { name: /^第 \d+ 项：/ })
}

function alertsWith(text: string) {
  return screen.queryAllByRole('alert').filter((node) => node.textContent?.includes(text))
}

describe('task composer', () => {
  beforeEach(() => {
    useComposerStore.getState().reset()
  })

  afterEach(() => {
    cleanup()
    useComposerStore.getState().reset()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists the ten task types with icons and one-line descriptions and keeps the pipeline within 32 tasks', async () => {
    installTaskApi()
    renderTaskArea()

    expect(await screen.findByRole('button', { name: '开始执行' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '存为定时任务' })).toBeDisabled()
    const sheet = await openTypeSheet()
    const options = within(sheet).getAllByRole('button').filter((button) => button.getAttribute('aria-label') !== '关闭')
    expect(options).toHaveLength(10)
    for (const type of taskTypeEnvelope.items) {
      const option = within(sheet).getByRole('button', { name: new RegExp(`^${type.label}`) })
      expect(option.querySelector('svg')).not.toBeNull()
    }
    expect(within(sheet).getByText('刷指定关卡，支持理智药、碎石与连战。')).toBeInTheDocument()
    expect(within(sheet).getByText('领取各种奖励。')).toBeInTheDocument()
    expect(within(sheet).queryByText(/docs\/05/)).not.toBeInTheDocument()
    fireEvent.click(within(sheet).getByRole('button', { name: '关闭' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    act(() => useComposerStore.getState().setDrafts(Array.from({ length: 32 }, (_, index) => ({ id: `draft-${index}`, name: 'Award', params: {} }))))
    expect(await screen.findByRole('button', { name: '开始执行' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '添加任务' })).toBeDisabled()
    expect(screen.getByText('最多 32 个任务')).toBeInTheDocument()
    const menu = await openRowMenu(1, '领取奖励')
    expect(within(menu).getByRole('button', { name: '复制' })).toBeDisabled()
  })

  it('edits a task in a full-screen sheet driven by its schema and summarizes it on the row (T1, T2)', async () => {
    installTaskApi()
    renderTaskArea()

    const fight = await addTask('刷理智')
    expect(within(fight).getByText('刷指定关卡，支持理智药、碎石与连战。')).toBeInTheDocument()
    for (const label of ['关卡名', '最大理智药数', '指定掉落数量']) {
      expect(within(fight).getAllByLabelText(label).length).toBeGreaterThan(0)
    }
    await closeEditor(fight)
    expect(screen.getByRole('button', { name: /^第 1 项：/ })).toHaveTextContent('使用默认参数')

    const copilot = await addTask('Copilot 作业')
    const picker = await within(copilot).findByLabelText('Copilot 作业')
    expect(await within(copilot).findByRole('option', { name: '主线 1-7 · 1-7' })).toBeInTheDocument()
    fireEvent.change(picker, { target: { value: 'copilot-1' } })
    await closeEditor(copilot)

    expect(screen.getByRole('button', { name: /^第 2 项：/ })).toHaveTextContent('主线 1-7')
    expect(useComposerStore.getState().drafts[1]).toMatchObject({ name: 'Copilot', params: { asset_id: 'copilot-1' } })
  })

  it('confirms x-risk changes, validates first and submits only the changed fields with an Idempotency-Key (T5)', async () => {
    const network = installTaskApi()
    renderTaskArea()

    const editor = await addTask('刷理智')
    fireEvent.change(within(editor).getByLabelText('最大理智药数'), { target: { value: '2' } })
    await closeEditor(editor)
    expect(screen.getByRole('button', { name: /^第 1 项：/ })).toHaveTextContent('理智药 2')

    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))
    const dismissed = await screen.findByRole('alertdialog', { name: '确认消耗资源？' })
    expect(dismissed).toHaveTextContent('最大理智药数')
    fireEvent.click(within(dismissed).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(network.find('POST', '/api/tasks/validate')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))
    const dialog = await screen.findByRole('alertdialog', { name: '确认消耗资源？' })
    expect(dialog).toHaveTextContent('刷理智 · 最大理智药数：2')
    fireEvent.click(within(dialog).getByRole('button', { name: '开始执行' }))

    const success = await screen.findByText('流水线已加入队列')
    expect(success.closest('[role="status"]')).not.toBeNull()
    expect(screen.getByRole('link', { name: '查看队列' })).toHaveAttribute('href', '/tasks/queue')
    const [validation] = network.find('POST', '/api/tasks/validate')
    expect(await validation.clone().json()).toEqual({ tasks: [withDefaults('Fight', { medicine: 2 })] })
    const [submission] = network.find('POST', '/api/pipelines')
    expect(submission.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await submission.clone().json()).toEqual({ tasks: [withDefaults('Fight', { medicine: 2 })] })
    expect(network.requests.indexOf(validation)).toBeLessThan(network.requests.indexOf(submission))
    expect(useComposerStore.getState().drafts).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /^第 1 项：/ })).not.toBeInTheDocument()
  })

  it('sends the optional pipeline name and skips the risk confirmation when nothing consumes resources', async () => {
    const network = installTaskApi()
    renderTaskArea()

    fireEvent.change(await screen.findByLabelText('流水线名称（可选）'), { target: { value: '  早间日常  ' } })
    await closeEditor(await addTask('开始唤醒'))
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))

    expect(await screen.findByText('流水线已加入队列')).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(await network.find('POST', '/api/pipelines')[0].clone().json()).toEqual({ tasks: [withDefaults('StartUp')], title: '早间日常' })
    expect(screen.getByLabelText('流水线名称（可选）')).toHaveValue('')
  })

  it('maps a validate details.field error onto the task row and the field inside its editor (T6)', async () => {
    const network = installTaskApi({
      validateStatus: 422,
      validateBody: { error: { code: 'TASK_PARAM_INVALID', message: '任务参数无效', details: { field: 'stage', message: '关卡名无效，请检查格式' } } },
    })
    renderTaskArea()

    const editor = await addTask('刷理智')
    fireEvent.change(within(editor).getByLabelText('关卡名'), { target: { value: 'unknown' } })
    await closeEditor(editor)
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))

    expect(await screen.findByText('第 1 个任务有 1 处错误，点开修改。')).toBeInTheDocument()
    const row = screen.getByRole('button', { name: /^第 1 项：/ })
    expect(row).toHaveTextContent('1 处错误')
    expect(network.find('POST', '/api/pipelines')).toHaveLength(0)

    fireEvent.click(row)
    const reopened = await screen.findByRole('dialog', { name: '刷理智' })
    expect(within(reopened).getByText('关卡名无效，请检查格式')).toHaveAttribute('role', 'alert')
    expect(within(reopened).getByLabelText('关卡名')).toHaveAttribute('aria-invalid', 'true')
  })

  it('maps pydantic loc errors onto the indexed task, opens it from the alert and clears an error once edited (T6)', async () => {
    const network = installTaskApi({
      validateStatus: 422,
      validateBody: {
        error: {
          code: 'VALIDATION_ERROR',
          message: '请求参数校验失败（1 处）',
          details: { fields: [{ loc: ['body', 'tasks', 1, 'FightInput', 'stage'], msg: 'String should have at least 1 character', type: 'string_too_short' }] },
        },
      },
    })
    renderTaskArea()

    await closeEditor(await addTask('开始唤醒'))
    await closeEditor(await addTask('刷理智'))
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))

    expect(await screen.findByText('第 2 个任务有 1 处错误，点开修改。')).toBeInTheDocument()
    const [startUp, fight] = taskRows()
    expect(startUp).not.toHaveTextContent('处错误')
    expect(fight).toHaveTextContent('1 处错误')
    expect(network.find('POST', '/api/pipelines')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '修改' }))
    const editor = await screen.findByRole('dialog', { name: '刷理智' })
    expect(within(editor).getByText('String should have at least 1 character')).toHaveAttribute('role', 'alert')
    fireEvent.change(within(editor).getByLabelText('关卡名'), { target: { value: '1-7' } })
    expect(within(editor).queryByText('String should have at least 1 character')).not.toBeInTheDocument()
    await closeEditor(editor)
    expect(taskRows()[1]).not.toHaveTextContent('处错误')
    expect(screen.queryByText(/个任务有 \d+ 处错误/)).not.toBeInTheDocument()
  })

  it('checks the current pipeline after a 504 and retries with the same Idempotency-Key (T7)', async () => {
    const network = installTaskApi({ submitStatuses: [504, 202] })
    renderTaskArea()

    await closeEditor(await addTask('开始唤醒'))
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))

    await waitFor(() => expect(alertsWith('504')).toHaveLength(1))
    await waitFor(() => expect(network.find('GET', '/api/pipelines/current')).toHaveLength(1))
    const firstKey = network.find('POST', '/api/pipelines')[0].headers.get('Idempotency-Key')
    expect(firstKey).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))
    expect(await screen.findByText('流水线已加入队列')).toBeInTheDocument()
    const writes = network.find('POST', '/api/pipelines')
    expect(writes).toHaveLength(2)
    expect(writes[1].headers.get('Idempotency-Key')).toBe(firstKey)
  })

  it('shows other submission failures in place', async () => {
    installTaskApi({ submitStatuses: [500] })
    renderTaskArea()

    await closeEditor(await addTask('开始唤醒'))
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))

    await waitFor(() => expect(alertsWith('提交流水线失败：提交失败')).toHaveLength(1))
    expect(taskRows()).toHaveLength(1)
  })

  it('moves, duplicates and deletes tasks from the row menu, confirming the deletion', async () => {
    const network = installTaskApi()
    renderTaskArea()

    await closeEditor(await addTask('开始唤醒'))
    const fight = await addTask('刷理智')
    fireEvent.change(within(fight).getByLabelText('关卡名'), { target: { value: '1-7' } })
    await closeEditor(fight)

    const second = await openRowMenu(2, '刷理智')
    expect(within(second).getByRole('button', { name: '下移' })).toBeDisabled()
    fireEvent.click(within(second).getByRole('button', { name: '上移' }))
    await waitFor(() => expect(taskRows()[0]).toHaveAccessibleName(/^第 1 项：\s*刷理智/))

    fireEvent.click(within(await openRowMenu(1, '刷理智')).getByRole('button', { name: '复制' }))
    await waitFor(() => expect(taskRows()).toHaveLength(3))
    expect(taskRows()[1]).toHaveAccessibleName(/^第 2 项：\s*刷理智/)
    expect(taskRows()[1]).toHaveTextContent('1-7')

    fireEvent.click(within(await openRowMenu(3, '开始唤醒')).getByRole('button', { name: '移除' }))
    const confirm = await screen.findByRole('alertdialog', { name: '移除第 3 项「开始唤醒」？' })
    fireEvent.click(within(confirm).getByRole('button', { name: '移除任务' }))
    await waitFor(() => expect(taskRows()).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))
    await waitFor(() => expect(network.find('POST', '/api/pipelines')).toHaveLength(1))
    expect(await network.find('POST', '/api/pipelines')[0].clone().json()).toEqual({ tasks: [withDefaults('Fight', { stage: '1-7' }), withDefaults('Fight', { stage: '1-7' })] })
  })

  it('prefills the schema defaults on add and duplicate and sends them with the submission (M17)', async () => {
    const network = installTaskApi()
    renderTaskArea()

    const editor = await addTask('刷理智')
    // 新建即预填：控件里显示的是具体值，而不是空白加「默认」。
    expect(within(editor).getByLabelText('服务器')).toHaveValue('"CN"')
    expect(within(editor).getByLabelText('连战次数')).toHaveValue('1')
    expect(within(editor).getByLabelText('最大理智药数')).toHaveValue(0)
    expect(within(editor).getByRole('switch', { name: '汇报企鹅数据' })).not.toBeChecked()
    await closeEditor(editor)
    // 参数摘要只在改过时才变化。
    expect(screen.getByRole('button', { name: /^第 1 项：/ })).toHaveTextContent('使用默认参数')
    expect(useComposerStore.getState().drafts[0]?.params).toEqual(materializeDefaults(typeByName('Fight'), {}))

    fireEvent.click(within(await openRowMenu(1, '刷理智')).getByRole('button', { name: '复制' }))
    await waitFor(() => expect(taskRows()).toHaveLength(2))
    expect(useComposerStore.getState().drafts[1]?.params).toEqual(materializeDefaults(typeByName('Fight'), {}))

    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))
    await waitFor(() => expect(network.find('POST', '/api/pipelines')).toHaveLength(1))
    const sent = { tasks: [withDefaults('Fight'), withDefaults('Fight')] }
    expect(await network.find('POST', '/api/tasks/validate')[0].clone().json()).toEqual(sent)
    expect(await network.find('POST', '/api/pipelines')[0].clone().json()).toEqual(sent)
  })

  it('keeps the draft while switching to the queue and back', async () => {
    installTaskApi()
    const { router } = renderTaskArea()

    fireEvent.change(await screen.findByLabelText('流水线名称（可选）'), { target: { value: '保留的草稿' } })
    const editor = await addTask('刷理智')
    fireEvent.change(within(editor).getByLabelText('关卡名'), { target: { value: '1-7' } })
    await closeEditor(editor)

    fireEvent.click(screen.getByRole('radio', { name: '队列' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/queue'))
    expect(await screen.findByText('夜间定时')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: '新建' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'))

    expect(await screen.findByRole('button', { name: /^第 1 项：\s*刷理智/ })).toHaveTextContent('1-7')
    expect(screen.getByLabelText('流水线名称（可选）')).toHaveValue('保留的草稿')
  })

  it('carries the composed tasks and the name into a new schedule (T13)', async () => {
    const network = installTaskApi()
    const { router } = renderTaskArea()

    fireEvent.change(await screen.findByLabelText('流水线名称（可选）'), { target: { value: '每日刷图' } })
    const editor = await addTask('刷理智')
    fireEvent.change(within(editor).getByLabelText('关卡名'), { target: { value: '1-7' } })
    fireEvent.change(within(editor).getByLabelText('最大理智药数'), { target: { value: '2' } })
    await closeEditor(editor)
    fireEvent.click(screen.getByRole('button', { name: '存为定时任务' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/schedules/new'))
    expect(router.state.location.state).toEqual({
      composerPrefill: { name: '每日刷图', template: [withDefaults('Fight', { stage: '1-7', medicine: 2 })] },
    })
    expect(network.find('POST', '/api/tasks/validate')).toHaveLength(0)
  })
})
