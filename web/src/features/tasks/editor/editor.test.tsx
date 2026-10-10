import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import taskTypesFixture from '../task-types.fixture.json'
import { materializeDefaults, type JsonSchema, type TaskTypeSchema } from '../form'
import { DynamicTaskForm, TaskEditor, type TaskEditorProps } from '.'

const types = (taskTypesFixture as unknown as { items: TaskTypeSchema[] }).items
const typeByName = (name: string) => {
  const found = types.find((type) => type.name === name)
  if (!found) throw new Error(`fixture 缺少任务类型 ${name}`)
  return found
}

const catalog = [
  { item_id: '30013', name: '固源岩', icon_url: null },
  { item_id: '30014', name: '固源岩组', icon_url: null },
  { item_id: '3301', name: '技巧概要·卷1', icon_url: null },
]

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

interface FetchOptions {
  itemsStatus?: number
  copilotStatuses?: number[]
}

function installFetch(options: FetchOptions = {}) {
  const requests: Request[] = []
  let copilotCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    requests.push(request.clone())
    const url = new URL(request.url)
    if (url.pathname === '/api/resources/items') {
      return options.itemsStatus ? jsonResponse({ error: { code: 'RESOURCE_UNAVAILABLE', message: '物品索引缺失' } }, options.itemsStatus) : jsonResponse(catalog)
    }
    if (url.pathname === '/api/resources/copilots') {
      const status = options.copilotStatuses?.[copilotCalls++] ?? 200
      if (status >= 400) return jsonResponse({ error: { code: 'INTERNAL_ERROR', message: '作业目录暂时不可用' } }, status)
      return jsonResponse({ items: [{ id: 'copilot-1', name: '主线 1-7', meta: { stage_name: '1-7', title: '剿灭行动' } }], total: 1, page: 1, size: 200 })
    }
    return jsonResponse({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { requests }
}

type HarnessProps = Omit<TaskEditorProps, 'value' | 'onChange'> & {
  initial?: Record<string, unknown>
  onChange?: (value: Record<string, unknown>) => void
}

function Harness({ initial = {}, onChange, ...props }: HarnessProps) {
  const [value, setValue] = useState(initial)
  return (
    <TaskEditor
      {...props}
      value={value}
      onChange={(next) => {
        onChange?.(next)
        setValue(next)
      }}
    />
  )
}

function renderEditor(props: HarnessProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={queryClient}><Harness {...props} /></QueryClientProvider>)
}

const synthetic: TaskTypeSchema = {
  name: 'Synthetic',
  label: '合成任务',
  description: '覆盖回退控件。',
  groups: ['基础'],
  runtime_immutable: [],
  schema: {
    type: 'object',
    properties: {
      name: { const: 'Synthetic', type: 'string' },
      payload: { anyOf: [{ type: 'string' }, { type: 'integer' }], 'x-label': '原始参数', 'x-group': '基础' },
      aliases: { type: 'object', additionalProperties: { type: 'string' }, 'x-label': '别名映射', 'x-group': '基础' },
      window: { type: 'object', properties: { start: { type: 'integer', 'x-label': '开始' } }, 'x-label': '时间窗口', 'x-group': '基础' },
      note: { type: 'string', default: 'auto', 'x-label': '备注', 'x-group': '基础' },
      flag: { type: 'boolean', 'x-label': '无默认开关', 'x-group': '基础' },
      lane: {
        anyOf: [{ enum: ['alpha', 'beta', 'gamma', 'delta'], type: 'string' }, { type: 'null' }],
        'x-label': '无默认下拉',
        'x-widget': 'select',
        'x-enum-labels': { alpha: '阿尔法', beta: '贝塔', gamma: '伽马', delta: '德尔塔' },
      },
    },
  },
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('TaskEditor', () => {
  it('renders a labelled control for every property of the twelve fixture task types (T1)', () => {
    installFetch()
    // fixture 与 `GET /api/tasks/types` 一致（M27 的 OperBox / Depot 已并入）。
    expect(types).toHaveLength(13)
    for (const taskType of types) {
      const { container, unmount } = renderEditor({ taskType })
      for (const [name, field] of Object.entries(taskType.schema.properties as Record<string, JsonSchema>)) {
        if (name === 'name') continue
        const label = field['x-label'] as string
        expect(container.textContent).toContain(label)
        expect(screen.getAllByLabelText(label).length).toBeGreaterThan(0)
      }
      unmount()
    }
  })

  it('exports DynamicTaskForm as the same component', () => {
    expect(DynamicTaskForm).toBe(TaskEditor)
  })

  it('loads saved Copilot assets and stores only the selected asset id (T2)', async () => {
    const network = installFetch()
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Copilot'), onChange })

    const picker = await screen.findByLabelText('Copilot 作业')
    expect(await screen.findByRole('option', { name: '主线 1-7 · 1-7' })).toBeInTheDocument()
    fireEvent.change(picker, { target: { value: 'copilot-1' } })
    expect(onChange).toHaveBeenLastCalledWith({ asset_id: 'copilot-1' })
    expect(screen.getByRole('switch', { name: '重新编队' })).toBeInTheDocument()
    const listRequest = network.requests.find((request) => new URL(request.url).pathname === '/api/resources/copilots')
    expect(new URL(listRequest!.url).searchParams.get('size')).toBe('200')
  })

  it('shows the Copilot list error in place and retries on request', async () => {
    const network = installFetch({ copilotStatuses: [500, 200] })
    renderEditor({ taskType: typeByName('Copilot') })

    expect(await screen.findByText(/读取 Copilot 作业失败：作业目录暂时不可用/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('option', { name: '主线 1-7 · 1-7' })).toBeInTheDocument()
    expect(network.requests.filter((request) => new URL(request.url).pathname === '/api/resources/copilots')).toHaveLength(2)
  })

  it('searches Fight drops by Chinese name and writes the item id and quantity (T3)', async () => {
    installFetch()
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Fight'), onChange })

    const search = await screen.findByLabelText('搜索物品名称或 ID')
    fireEvent.change(search, { target: { value: '固源' } })
    fireEvent.click(screen.getByRole('button', { name: '添加 固源岩' }))
    expect(onChange).toHaveBeenLastCalledWith({ drops: { '30013': 1 } })
    expect(search).toHaveValue('')

    fireEvent.change(screen.getByLabelText('数量：固源岩'), { target: { value: '5' } })
    expect(onChange).toHaveBeenLastCalledWith({ drops: { '30013': 5 } })
    fireEvent.click(screen.getByRole('button', { name: '移除物品：固源岩' }))
    expect(onChange).toHaveBeenLastCalledWith({})
  })

  it('falls back to manual item ids when the item catalog is unavailable', async () => {
    installFetch({ itemsStatus: 503 })
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Fight'), onChange })

    expect(await screen.findByText(/读取物品目录失败/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('物品 ID'), { target: { value: '30013' } })
    fireEvent.click(screen.getByRole('button', { name: '添加 ID' }))
    expect(onChange).toHaveBeenLastCalledWith({ drops: { '30013': 1 } })
  })

  it('edits Recruit durations as hours and minutes and stores minutes (T4)', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Recruit'), onChange })

    const hours = screen.getByRole('spinbutton', { name: '时长小时：3' })
    expect(hours).toHaveValue(9)
    expect(screen.getByRole('spinbutton', { name: '时长分钟：3' })).toHaveValue(0)
    expect(screen.getByRole('spinbutton', { name: '时长小时：6' })).toHaveValue(9)
    fireEvent.change(hours, { target: { value: '8' } })
    expect(onChange).toHaveBeenLastCalledWith({ recruitment_time: { '3': 480 } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '时长分钟：3' }), { target: { value: '30' } })
    expect(onChange).toHaveBeenLastCalledWith({ recruitment_time: { '3': 510 } })
  })

  it('keeps untouched fields out of the draft, restores defaults and writes explicit nulls', async () => {
    installFetch()
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Fight'), onChange })
    await screen.findByLabelText('搜索物品名称或 ID')
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getAllByText('默认').length).toBeGreaterThan(0)

    fireEvent.change(screen.getByLabelText('关卡名'), { target: { value: '1-7' } })
    expect(onChange).toHaveBeenLastCalledWith({ stage: '1-7' })
    expect(screen.getByText('已修改')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '恢复默认：关卡名' }))
    expect(onChange).toHaveBeenLastCalledWith({})
    expect(screen.getByLabelText('关卡名')).toHaveValue('')
    expect(screen.queryByText('已修改')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '关卡名的更多操作' }))
    const sheet = await screen.findByRole('dialog', { name: '关卡名' })
    fireEvent.click(within(sheet).getByRole('button', { name: '明确留空' }))
    expect(onChange).toHaveBeenLastCalledWith({ stage: null })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByLabelText('关卡名')).toHaveAttribute('placeholder', '已设为空')
    expect(screen.getByRole('button', { name: '恢复默认：关卡名' })).toBeInTheDocument()

    // 有默认值的字段：改动后「恢复默认」写回默认值而不是删键。
    fireEvent.change(screen.getByLabelText('最大理智药数'), { target: { value: '2' } })
    expect(onChange).toHaveBeenLastCalledWith({ stage: null, medicine: 2 })
    fireEvent.click(screen.getByRole('button', { name: '恢复默认：最大理智药数' }))
    expect(onChange).toHaveBeenLastCalledWith({ stage: null, medicine: 0 })
    expect(screen.getByLabelText('最大理智药数')).toHaveValue(0)
    expect(screen.queryByRole('button', { name: '恢复默认：最大理智药数' })).not.toBeInTheDocument()
  })

  it('only offers 明确留空 for fields whose schema allows null', () => {
    installFetch()
    renderEditor({ taskType: typeByName('Copilot') })
    expect(screen.queryByRole('button', { name: '重新编队的更多操作' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Copilot 作业的更多操作' })).not.toBeInTheDocument()
    expect(screen.getByText('必填')).toBeInTheDocument()
    cleanup()
    renderEditor({ taskType: typeByName('Award') })
    expect(screen.getByRole('button', { name: '领取邮件奖励的更多操作' })).toBeInTheDocument()
  })

  it('writes booleans from switches and enum values from segmented controls and selects', () => {
    const onChange = vi.fn()
    const defaults = materializeDefaults(typeByName('Reclamation'), {})
    renderEditor({ taskType: typeByName('Reclamation'), initial: defaults, onChange })

    // 有默认值的开关显示开/关本身：开着的开关点一下就是关。
    fireEvent.click(screen.getByRole('switch', { name: '启用本任务' }))
    expect(onChange).toHaveBeenLastCalledWith({ ...defaults, enable: false })
    fireEvent.click(screen.getByRole('switch', { name: '启用本任务' }))
    expect(onChange).toHaveBeenLastCalledWith(defaults)

    fireEvent.click(within(screen.getByRole('radiogroup', { name: '主题' })).getByRole('radio', { name: '沙中之火' }))
    expect(onChange).toHaveBeenLastCalledWith({ ...defaults, theme: 'Fire' })
    expect(within(screen.getByRole('radiogroup', { name: '主题' })).getByRole('radio', { name: '沙中之火' })).toHaveAttribute('aria-checked', 'true')

    fireEvent.change(screen.getByLabelText('模式'), { target: { value: '1' } })
    expect(onChange).toHaveBeenLastCalledWith({ ...defaults, theme: 'Fire', mode: 1 })

    fireEvent.click(within(screen.getByRole('radiogroup', { name: '点击类型' })).getByRole('radio', { name: '长按' }))
    expect(onChange).toHaveBeenLastCalledWith({ ...defaults, theme: 'Fire', mode: 1, increment_mode: 1 })
  })

  it('offers Fight.series choices derived from its labels', () => {
    installFetch()
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Fight'), onChange })
    const series = screen.getByLabelText('连战次数')
    expect(within(series).getByRole('option', { name: '禁用切换' })).toBeInTheDocument()
    fireEvent.change(series, { target: { value: '-1' } })
    expect(onChange).toHaveBeenLastCalledWith({ series: -1 })
    fireEvent.change(screen.getByLabelText('服务器'), { target: { value: '"CN"' } })
    expect(onChange).toHaveBeenLastCalledWith({ series: -1, server: 'CN' })
  })

  it('shows field errors under the control, marks it invalid and opens folded groups with errors', () => {
    installFetch()
    renderEditor({ taskType: typeByName('Fight'), errors: { stage: '关卡名无效，请检查格式', drops: '物品数量必须大于 0' } })

    const message = screen.getByText('关卡名无效，请检查格式')
    expect(message).toHaveAttribute('role', 'alert')
    const stage = screen.getByLabelText('关卡名')
    expect(stage).toHaveAttribute('aria-invalid', 'true')
    expect(stage.getAttribute('aria-describedby')?.split(' ')).toContain(message.id)
    expect(screen.getByText('高级').closest('details')).toHaveAttribute('open')
    expect(screen.getByText('物品数量必须大于 0')).toHaveAttribute('role', 'alert')
  })

  it('scrolls the first field with an error into view when the editor opens', () => {
    installFetch()
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    try {
      renderEditor({ taskType: typeByName('Fight'), errors: { drops: '物品数量必须大于 0', stage: '关卡名无效' } })
      expect(scrollIntoView).toHaveBeenCalledTimes(1)
      const target = scrollIntoView.mock.contexts[0] as HTMLElement
      expect(within(target).getByLabelText('关卡名')).toBeInTheDocument()
    } finally {
      delete (Element.prototype as Partial<Element>).scrollIntoView
    }
  })

  it('disables fields whose x-depends-on is not met and names the field they depend on', () => {
    installFetch()
    renderEditor({ taskType: typeByName('Fight') })

    expect(screen.getByLabelText('企鹅数据 ID')).toBeDisabled()
    expect(screen.getByText('需先开启「汇报企鹅数据」')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('switch', { name: '汇报企鹅数据' }))
    expect(screen.getByLabelText('企鹅数据 ID')).toBeEnabled()
    expect(screen.queryByText('需先开启「汇报企鹅数据」')).not.toBeInTheDocument()
  })

  it('locks runtime_immutable fields only while editing a running pipeline', () => {
    installFetch()
    renderEditor({ taskType: typeByName('Fight'), runtimeEditing: true })
    expect(screen.getByLabelText('关卡名')).toBeDisabled()
    expect(screen.getByText('运行中不可修改')).toBeInTheDocument()
    expect(screen.getByLabelText('指定次数')).toBeEnabled()
    cleanup()

    renderEditor({ taskType: typeByName('Fight') })
    expect(screen.getByLabelText('关卡名')).toBeEnabled()
    expect(screen.queryByText('运行中不可修改')).not.toBeInTheDocument()
  })

  it('disables every control while the whole form is disabled', () => {
    renderEditor({ taskType: typeByName('Award'), disabled: true })
    const switches = screen.getAllByRole('switch')
    expect(switches).toHaveLength(7)
    for (const control of switches) expect(control).toBeDisabled()
  })

  it('opens the first group, folds the rest and counts modified fields per group', () => {
    installFetch()
    const { container } = renderEditor({ taskType: typeByName('Fight'), initial: { medicine: 2, stone: 1 } })

    const groups = Array.from(container.querySelectorAll('details'))
    expect(groups.map((group) => group.open)).toEqual([true, false, false, false, false])
    expect(within(groups[1]).getByText('已修改 2 项')).toBeInTheDocument()
    expect(within(groups[1]).getAllByText('消耗资源')).toHaveLength(3)
    expect(within(groups[0]).queryByText(/已修改 \d+ 项/)).not.toBeInTheDocument()
  })

  it('steps numbers within the schema range', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Infrast'), initial: { threshold: 0 }, onChange })

    expect(screen.getByText('范围 0–1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '减少工作心情阈值' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '增加工作心情阈值' }))
    expect(onChange).toHaveBeenLastCalledWith({ threshold: 0.05 })
    fireEvent.click(screen.getByRole('button', { name: '减少工作心情阈值' }))
    expect(onChange).toHaveBeenLastCalledWith({ threshold: 0 })
    expect(screen.getByRole('button', { name: '减少工作心情阈值' })).toBeDisabled()
  })

  it('clears an input back to the schema default, or to unset when there is none', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Infrast'), initial: { threshold: 0.5, filename: 'plan.json' }, onChange })

    // 有默认值：清空 = 写回默认值（0.3），键留在草稿里。
    fireEvent.change(screen.getByLabelText('工作心情阈值'), { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith({ threshold: 0.3, filename: 'plan.json' })
    // 没有默认值：清空 = 删键，回到「未设置」。文件名没有默认值，placeholder 才有意义。
    expect(screen.getByLabelText('自定义配置路径')).toHaveAttribute('placeholder', '未设置')
    fireEvent.change(screen.getByLabelText('自定义配置路径'), { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith({ threshold: 0.3 })
  })

  it('clears a text input with a default back to that default', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: synthetic, initial: materializeDefaults(synthetic, {}), onChange })

    expect(screen.getByLabelText('备注')).toHaveValue('auto')
    expect(screen.getByLabelText('备注')).not.toHaveAttribute('placeholder')
    fireEvent.change(screen.getByLabelText('备注'), { target: { value: '手写' } })
    expect(onChange).toHaveBeenLastCalledWith({ note: '手写' })
    fireEvent.change(screen.getByLabelText('备注'), { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith({ note: 'auto' })
  })

  it('prefills switches, numbers, selects and segmented controls from the materialized defaults', () => {
    const fight = typeByName('Fight')
    renderEditor({ taskType: fight, initial: materializeDefaults(fight, {}) })

    expect(screen.getByRole('switch', { name: '汇报企鹅数据' })).not.toBeChecked()
    expect(screen.getByRole('switch', { name: '汇报企鹅数据' })).not.toHaveAttribute('data-unset')
    expect(screen.getByLabelText('最大理智药数')).toHaveValue(0)
    expect(screen.getByLabelText('连战次数')).toHaveValue('1')
    expect(screen.getByLabelText('服务器')).toHaveValue('"CN"')
    expect(screen.getByLabelText('客户端版本')).toHaveValue('"Bilibili"')
    // 有默认值的下拉不再渲染空选项（值总在草稿里）。
    expect(within(screen.getByLabelText('服务器')).queryByRole('option', { name: '未设置' })).not.toBeInTheDocument()
    cleanup()

    renderEditor({ taskType: synthetic, initial: materializeDefaults(synthetic, {}) })
    expect(screen.getByLabelText('无默认下拉')).toHaveValue('')
    expect(within(screen.getByLabelText('无默认下拉')).getByRole('option', { name: '未设置' })).toBeInTheDocument()
  })

  it('centers the switch only for boolean fields without a default', () => {
    const roguelike = typeByName('Roguelike')
    renderEditor({ taskType: roguelike, initial: materializeDefaults(roguelike, {}) })

    // Roguelike.use_foldartal 是条件默认值，保持居中表示交给内核。
    const foldartal = screen.getByRole('switch', { name: '使用密文板' })
    expect(foldartal).not.toBeChecked()
    expect(foldartal).toHaveAttribute('data-unset', 'true')
    // 有默认值的开关直接显示开/关。
    expect(screen.getByRole('switch', { name: '投资源石锭' })).toBeChecked()
    expect(screen.getByRole('switch', { name: '投资源石锭' })).not.toHaveAttribute('data-unset')
    cleanup()

    const onChange = vi.fn()
    renderEditor({ taskType: synthetic, onChange })
    const flag = screen.getByRole('switch', { name: '无默认开关' })
    expect(flag).toHaveAttribute('data-unset', 'true')
    fireEvent.click(flag)
    expect(onChange).toHaveBeenLastCalledWith({ flag: true })
  })

  it('marks the five field states and only offers 恢复默认 once a field is modified', () => {
    const fight = typeByName('Fight')
    renderEditor({ taskType: fight, initial: materializeDefaults(fight, {}) })

    expect(screen.getAllByText('默认').length).toBeGreaterThan(0)
    expect(screen.getAllByText('未设置').length).toBeGreaterThan(0)
    expect(screen.queryByText('已修改')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '恢复默认：最大理智药数' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('switch', { name: '汇报企鹅数据' }))
    expect(screen.getByText('已修改')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '恢复默认：汇报企鹅数据' }))
    expect(screen.getByRole('switch', { name: '汇报企鹅数据' })).not.toBeChecked()
    expect(screen.queryByText('已修改')).not.toBeInTheDocument()
  })

  it('adds, reorders and removes facilities from scratch, in Chinese', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Infrast'), onChange })

    expect(screen.getByText('尚未添加')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '添加 制造站' }))
    fireEvent.click(screen.getByRole('button', { name: '添加 贸易站' }))
    expect(onChange).toHaveBeenLastCalledWith({ facility: ['Mfg', 'Trade'] })
    fireEvent.click(screen.getByRole('button', { name: '上移 贸易站' }))
    expect(onChange).toHaveBeenLastCalledWith({ facility: ['Trade', 'Mfg'] })
    expect(screen.getByRole('button', { name: '上移 贸易站' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '移除 制造站' }))
    expect(onChange).toHaveBeenLastCalledWith({ facility: ['Trade'] })
  })

  it('renders the materialized facility default with Chinese labels and lets the user reorder it', () => {
    const infrast = typeByName('Infrast')
    const defaults = materializeDefaults(infrast, {})
    const onChange = vi.fn()
    renderEditor({ taskType: infrast, initial: defaults, onChange })

    // 七个设施按标准换班顺序预填，显示中文而不是 Mfg / Trade。
    const group = screen.getByRole('group', { name: '要换班的设施（有序）' })
    const rows = within(group).getAllByRole('listitem')
    expect(rows.map((row) => row.textContent)).toEqual(['1制造站', '2贸易站', '3发电站', '4控制中枢', '5会客室', '6办公室', '7宿舍'])
    const reordered = { ...defaults, facility: ['Trade', 'Mfg', 'Power', 'Control', 'Reception', 'Office', 'Dorm'] }
    fireEvent.click(within(group).getByRole('button', { name: '上移 贸易站' }))
    expect(onChange).toHaveBeenLastCalledWith(reordered)
    fireEvent.click(within(group).getByRole('button', { name: '移除 发电站' }))
    expect(onChange).toHaveBeenLastCalledWith({ ...reordered, facility: ['Trade', 'Mfg', 'Control', 'Reception', 'Office', 'Dorm'] })
    expect(within(group).getByRole('button', { name: '添加 发电站' })).toBeInTheDocument()
  })

  it('toggles integer tag chips and adds free-form tags with Enter', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Recruit'), onChange })

    const select = screen.getByRole('group', { name: '点击的 Tag 等级' })
    fireEvent.click(within(select).getByRole('button', { name: '4' }))
    fireEvent.click(within(select).getByRole('button', { name: '5' }))
    expect(onChange).toHaveBeenLastCalledWith({ select: [4, 5] })
    expect(within(select).getByRole('button', { name: '4' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(select).getByRole('button', { name: '4' }))
    expect(onChange).toHaveBeenLastCalledWith({ select: [5] })

    const firstTags = screen.getByLabelText('首选 Tags')
    fireEvent.change(firstTags, { target: { value: '高级资深干员' } })
    fireEvent.keyDown(firstTags, { key: 'Enter' })
    expect(onChange).toHaveBeenLastCalledWith({ select: [5], first_tags: ['高级资深干员'] })
    expect(firstTags).toHaveValue('')
    fireEvent.click(screen.getByRole('button', { name: '移除 高级资深干员' }))
    expect(onChange).toHaveBeenLastCalledWith({ select: [5], first_tags: [] })
  })

  it('falls back to a JSON editor for complex values and only writes valid JSON', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: synthetic, onChange })

    const payload = screen.getByLabelText('原始参数')
    fireEvent.change(payload, { target: { value: '{"a":' } })
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText('JSON 格式尚未完整或无效。')).toBeInTheDocument()
    fireEvent.change(payload, { target: { value: '42' } })
    expect(onChange).toHaveBeenLastCalledWith({ payload: 42 })
    expect(screen.queryByText('JSON 格式尚未完整或无效。')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '恢复默认：原始参数' }))
    expect(onChange).toHaveBeenLastCalledWith({})
    expect(screen.getByLabelText('原始参数')).toHaveValue('')
  })

  it('edits key-value maps and nested objects', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: synthetic, onChange })

    fireEvent.change(screen.getByLabelText('新键'), { target: { value: 'amiya' } })
    fireEvent.change(screen.getByLabelText('新值'), { target: { value: '阿米娅' } })
    fireEvent.click(screen.getByRole('button', { name: '添加映射项' }))
    expect(onChange).toHaveBeenLastCalledWith({ aliases: { amiya: '阿米娅' } })
    fireEvent.change(screen.getByLabelText('开始'), { target: { value: '8' } })
    expect(onChange).toHaveBeenLastCalledWith({ aliases: { amiya: '阿米娅' }, window: { start: 8 } })
    fireEvent.click(screen.getByRole('button', { name: '移除键 amiya' }))
    expect(onChange).toHaveBeenLastCalledWith({ window: { start: 8 } })
  })

  it('clamps long descriptions to one line with an expand toggle', () => {
    installFetch()
    renderEditor({ taskType: typeByName('Fight') })
    const toggle = screen.getAllByRole('button', { name: '展开说明' })[0]
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(toggle).toHaveTextContent('收起')
  })

  it('edits the D5 Roguelike fields (character list as JSON, rewards as switches)', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Roguelike'), initial: { mode: 4 }, onChange })

    // 对象数组没有专用控件：退回 JSON 文本框，粘贴后立刻写入草稿。
    const charList = screen.getByLabelText('开局干员顺位')
    fireEvent.change(charList, { target: { value: '[{"name":"维什戴尔","use_support":true}]' } })
    expect(onChange).toHaveBeenLastCalledWith({ mode: 4, core_char_list: [{ name: '维什戴尔', use_support: true }] })
    fireEvent.change(charList, { target: { value: '[' } })
    expect(screen.getByText('JSON 格式尚未完整或无效。')).toBeInTheDocument()

    // 凹开局的奖励对象逐项渲染成开关，改一项只写这一项。
    const rewards = screen.getByRole('group', { name: '凹开局期望的奖励' })
    fireEvent.click(within(rewards).getByRole('switch', { name: '热水壶' }))
    expect(onChange).toHaveBeenLastCalledWith({
      mode: 4,
      core_char_list: [{ name: '维什戴尔', use_support: true }],
      collectible_mode_start_list: { hot_water: true },
    })
  })

  it('disables the mode-specific Roguelike fields until the mode matches', () => {
    renderEditor({ taskType: typeByName('Roguelike'), initial: { mode: 0 } })
    expect(screen.getByRole('switch', { name: '投资后再购物' })).toBeDisabled()
    expect(screen.getByText('需先将「模式」设为「刷源石锭」')).toBeInTheDocument()
  })

  it('offers 加工站 and 训练室 when adding facilities', () => {
    const onChange = vi.fn()
    renderEditor({ taskType: typeByName('Infrast'), initial: { facility: ['Mfg'] }, onChange })
    const group = screen.getByRole('group', { name: '要换班的设施（有序）' })

    fireEvent.click(within(group).getByRole('button', { name: '添加 加工站' }))
    expect(onChange).toHaveBeenLastCalledWith({ facility: ['Mfg', 'Processing'] })
    fireEvent.click(within(group).getByRole('button', { name: '添加 训练室' }))
    expect(onChange).toHaveBeenLastCalledWith({ facility: ['Mfg', 'Processing', 'Training'] })
  })
})
