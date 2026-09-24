import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TasksPage from '@/routes/tasks'
import { DynamicTaskForm, type JsonSchema, type TaskTypeSchema } from './index'

type Field = JsonSchema & { 'x-label': string; 'x-group': string }

const clientTypes = ['Official', 'Bilibili', 'txwy', 'YoStarEN', 'YoStarJP', 'YoStarKR']
const servers = ['CN', 'US', 'JP', 'KR']
const facilities = ['Mfg', 'Trade', 'Power', 'Control', 'Reception', 'Office', 'Dorm']
const themes = ['Phantom', 'Mizuki', 'Sami', 'Sarkaz']

function nullable(type: string, label: string, group = '基础', extra: Record<string, unknown> = {}): Field {
  const branch: Record<string, unknown> = { type }
  const extensions: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(extra)) {
    if (key.startsWith('x-')) extensions[key] = value
    else branch[key] = value
  }
  return {
    anyOf: [branch, { type: 'null' }],
    default: null,
    'x-label': label,
    'x-group': group,
    ...extensions,
  }
}

function stringField(label: string, group = '基础', extra: Record<string, unknown> = {}): Field {
  return nullable('string', label, group, extra)
}

function integerField(label: string, group = '基础', extra: Record<string, unknown> = {}): Field {
  return nullable('integer', label, group, extra)
}

function booleanField(label: string, group = '基础', extra: Record<string, unknown> = {}): Field {
  return nullable('boolean', label, group, extra)
}

function selectField(label: string, values: unknown[], group = '基础', enumLabels: Record<string, string> = {}): Field {
  return nullable('string', label, group, { enum: values, 'x-widget': 'select', 'x-enum-labels': enumLabels })
}

function arrayField(label: string, group = '基础', extra: Record<string, unknown> = {}): Field {
  return nullable('array', label, group, extra)
}

function typeSchema(name: string, label: string, fields: Record<string, Field>, groups: string[], runtimeImmutable: string[] = []): TaskTypeSchema {
  return {
    name,
    label,
    description: `${label}任务参数 schema`,
    groups,
    runtime_immutable: runtimeImmutable,
    schema: {
      type: 'object',
      properties: {
        name: { type: 'string', const: name, default: name, 'x-label': '任务类型', 'x-group': '基础' },
        ...fields,
      },
      additionalProperties: false,
    },
  }
}

const types: TaskTypeSchema[] = [
  typeSchema('StartUp', '开始唤醒', {
    client_type: selectField('客户端版本', clientTypes, '账号', { Official: '官服', Bilibili: 'B 服' }),
    start_game_enabled: booleanField('自动启动客户端', '账号'),
    account_name: stringField('切换账号', '账号'),
  }, ['基础', '账号']),
  typeSchema('CloseDown', '关闭游戏', {
    client_type: selectField('客户端版本', clientTypes, '账号'),
  }, ['基础', '账号']),
  typeSchema('Fight', '刷理智', {
    stage: stringField('关卡名', '基础'),
    times: integerField('指定次数', '基础', { minimum: 0 }),
    series: integerField('连战次数', '基础', { minimum: -1, maximum: 6, 'x-widget': 'select', enum: [-1, 0, 1, 2, 3, 4, 5, 6], 'x-enum-labels': { '-1': '禁用切换', '0': '自动选择' } }),
    medicine: integerField('最大理智药数', '资源消耗', { minimum: 0, 'x-risk': 'consume' }),
    expiring_medicine: integerField('最大 48 小时内过期理智药数', '资源消耗', { minimum: 0, 'x-risk': 'consume' }),
    stone: integerField('最大碎石数', '资源消耗', { minimum: 0, 'x-risk': 'consume' }),
    dr_grandet: booleanField('节省理智碎石模式', '资源消耗'),
    server: selectField('服务器', servers, '账号'),
    client_type: selectField('客户端版本', clientTypes, '账号'),
    report_to_penguin: booleanField('汇报企鹅数据', '数据上报'),
    penguin_id: stringField('企鹅数据 ID', '数据上报', { 'x-depends-on': { report_to_penguin: true } }),
    drops: nullable('object', '指定掉落数量', '高级', { 'x-widget': 'item-count-map', additionalProperties: { type: 'integer', minimum: 1 } }),
  }, ['基础', '资源消耗', '账号', '数据上报', '高级'], ['stage']),
  typeSchema('Recruit', '自动公招', {
    refresh: booleanField('刷新三星 Tags'),
    select: arrayField('点击的 Tag 等级', '基础', { 'x-widget': 'tags', items: { type: 'integer', minimum: 1, maximum: 6 } }),
    confirm: arrayField('确认的 Tag 等级', '基础', { 'x-widget': 'tags', items: { type: 'integer', minimum: 1, maximum: 6 } }),
    first_tags: arrayField('首选 Tags', '公招标签', { 'x-widget': 'tags', items: { type: 'string' } }),
    extra_tags_mode: integerField('选择更多的 Tags', '基础', { enum: [0, 1, 2], 'x-widget': 'select', 'x-enum-labels': { '0': '默认行为', '1': '选择三个', '2': '尽可能同时选择更多高星 Tag' } }),
    times: integerField('招募次数', '基础', { minimum: 0 }),
    set_time: booleanField('设置招募时限', '基础', { 'x-depends-on': { times: 0 } }),
    expedite: booleanField('使用加急许可', '加急与跳过', { 'x-risk': 'consume' }),
    expedite_times: integerField('加急次数', '加急与跳过', { minimum: 0, 'x-depends-on': { expedite: true } }),
    skip_robot: booleanField('跳过小车词条', '加急与跳过'),
    recruitment_time: nullable('object', '招募时限映射', '公招标签', { 'x-widget': 'duration-map', additionalProperties: { type: 'integer', minimum: 0 } }),
    report_to_penguin: booleanField('汇报企鹅数据', '数据上报'),
    penguin_id: stringField('企鹅数据 ID', '数据上报', { 'x-depends-on': { report_to_penguin: true } }),
    report_to_yituliu: booleanField('汇报一图流数据', '数据上报'),
    yituliu_id: stringField('一图流 ID', '数据上报', { 'x-depends-on': { report_to_yituliu: true } }),
    server: selectField('服务器', servers, '账号'),
  }, ['基础', '公招标签', '加急与跳过', '数据上报', '账号']),
  typeSchema('Infrast', '基建换班', {
    mode: integerField('换班工作模式', '基础', { enum: [0, 10000, 20000], 'x-widget': 'select' }),
    facility: arrayField('要换班的设施（有序）', '基础', { 'x-widget': 'ordered-list', items: { type: 'string', enum: facilities } }),
    drones: selectField('无人机用途', ['_NotUse', 'Money', 'SyntheticJade', 'CombatRecord', 'PureGold', 'OriginStone', 'Chip']),
    threshold: nullable('number', '工作心情阈值', '基础', { minimum: 0, maximum: 1 }),
    replenish: booleanField('源石碎片自动补货'),
    dorm_notstationed_enabled: booleanField('启用宿舍「未进驻」选项'),
    dorm_trust_enabled: booleanField('剩余位置填入信赖未满干员'),
    filename: stringField('自定义配置路径', '自定义换班', { 'x-depends-on': { mode: 10000 } }),
    plan_index: integerField('方案序号', '自定义换班', { minimum: 0, 'x-depends-on': { mode: 10000 } }),
  }, ['基础', '自定义换班'], ['facility', 'filename', 'plan_index']),
  typeSchema('Mall', '获取信用及购物', {
    shopping: booleanField('是否购物', '基础', { 'x-risk': 'consume' }),
    buy_first: arrayField('优先购买列表', '购买清单', { 'x-widget': 'tags', items: { type: 'string' } }),
    blacklist: arrayField('黑名单列表', '购买清单', { 'x-widget': 'tags', items: { type: 'string' } }),
    force_shopping_if_credit_full: booleanField('信用溢出时无视黑名单'),
    only_buy_discount: booleanField('只购买折扣物品'),
    reserve_max_credit: booleanField('信用点低于 300 时停止购买'),
  }, ['基础', '购买清单'], ['shopping', 'buy_first', 'blacklist']),
  typeSchema('Award', '领取奖励', {
    award: booleanField('领取每日/每周任务奖励'),
    mail: booleanField('领取邮件奖励', '奖励项'),
    recruit: booleanField('领取每日免费单抽', '奖励项'),
    orundum: booleanField('领取幸运墙合成玉', '奖励项'),
    mining: booleanField('领取限时开采许可奖励', '奖励项'),
    specialaccess: booleanField('领取五周年月卡奖励', '奖励项'),
  }, ['基础', '奖励项']),
  typeSchema('Roguelike', '自动肉鸽', {
    theme: selectField('主题', themes, '基础'),
    mode: integerField('模式', '基础', { enum: [0, 1, 2, 3, 4, 5], 'x-widget': 'select' }),
    squad: stringField('开局分队名'),
    roles: stringField('开局职业组'),
    core_char: stringField('开局干员名', '开局'),
    use_support: booleanField('开局干员为助战干员', '开局'),
    use_nonfriend_support: booleanField('允许非好友助战', '开局', { 'x-depends-on': { use_support: true } }),
    starts_count: integerField('开始探索次数', '开局'),
    difficulty: integerField('指定难度等级', '开局', { minimum: 0 }),
    start_with_elite_two: booleanField('凹干员精二直升', '开局'),
    only_start_with_elite_two: booleanField('只凹精二直升', '开局', { 'x-depends-on': { mode: 4, start_with_elite_two: true } }),
    first_floor_foldartal: stringField('第一层远见密文板', '开局', { 'x-depends-on': { theme: 'Sami' } }),
    start_foldartal_list: arrayField('开局奖励密文板列表', '开局', { 'x-widget': 'tags', items: { type: 'string' }, 'x-depends-on': { theme: 'Sami', mode: 4 } }),
    use_foldartal: booleanField('使用密文板', '主题专属', { 'x-depends-on': { theme: 'Sami' } }),
    stop_at_final_boss: booleanField('第 5 层险路恶敌前停止', '主题专属'),
    investment_enabled: booleanField('投资源石锭', '投资', { 'x-risk': 'consume' }),
    investments_count: integerField('投资次数', '投资'),
    stop_when_investment_full: booleanField('投资满后停止任务', '投资'),
    refresh_trader_with_dice: booleanField('用骰子刷新商店', '主题专属', { 'x-depends-on': { theme: 'Mizuki' } }),
    check_collapsal_paradigms: booleanField('检测坍缩范式', '坍缩范式', { 'x-depends-on': { theme: 'Sami' } }),
    double_check_collapsal_paradigms: booleanField('坍缩范式检测防漏', '坍缩范式', { 'x-depends-on': { check_collapsal_paradigms: true } }),
    expected_collapsal_paradigms: arrayField('希望触发的坍缩范式', '坍缩范式', { 'x-widget': 'tags', items: { type: 'string' }, 'x-depends-on': { theme: 'Sami', mode: 5 } }),
  }, ['基础', '开局', '主题专属', '投资', '坍缩范式']),
  typeSchema('Reclamation', '生息演算', {
    theme: selectField('主题', ['Fire', 'Tales'], '基础', { Fire: '沙中之火', Tales: '沙洲遗闻' }),
    mode: integerField('模式', '基础', { enum: [0, 1], 'x-widget': 'select' }),
    tools_to_craft: arrayField('自动制造的物品', '制造', { 'x-widget': 'tags', items: { type: 'string' } }),
    increment_mode: integerField('点击类型', '制造', { enum: [0, 1], 'x-widget': 'select' }),
    num_craft_batches: integerField('单次最大制造轮数', '制造', { minimum: 1 }),
  }, ['基础', '制造']),
]

const taskTypeEnvelope = { items: types, total: 9, page: 1, size: 9 }
const initialQueue = {
  running: { id: 'running-1', title: '正在运行', source: 'manual', priority: 0, status: 'RUNNING' },
  pending: [
    { id: 'queued-1', title: '定时任务', source: 'scheduled', priority: 2, status: 'PENDING', task_count: 1 },
    { id: 'queued-2', title: 'Agent 任务', source: 'agent', priority: 1, status: 'PENDING', task_count: 2 },
  ],
  paused: false,
  counts: { pending: 2, running: 1 },
}

interface MockOptions {
  validateStatus?: number
  validateBody?: unknown
  historyPages?: Record<number, { items: Array<Record<string, unknown>>; total: number; page: number; size: number }>
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function installFetch(options: MockOptions = {}) {
  const requests: Request[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin), init)
    requests.push(request.clone())
    const url = new URL(request.url)

    if (url.pathname === '/api/tasks/types') return jsonResponse(taskTypeEnvelope)
    if (url.pathname === '/api/resources/items') return jsonResponse([{ item_id: '30013', name: '固源岩', icon_url: null }])
    if (url.pathname === '/api/tasks/validate') return jsonResponse(options.validateBody ?? { items: [], total: 0, page: 1, size: 0 }, options.validateStatus ?? 200)
    if (url.pathname === '/api/pipelines' && request.method === 'POST') return jsonResponse({ pipeline_id: 'pipeline-new', status: 'PENDING', reused: false }, 202)
    if (url.pathname === '/api/queue' && request.method === 'GET') return jsonResponse(initialQueue)
    if (url.pathname === '/api/queue/queued-1' && request.method === 'PATCH') return jsonResponse({ pipeline_id: 'queued-1', priority: 0, status: 'PENDING' })
    if (url.pathname === '/api/pipelines/running-1' && request.method === 'DELETE') return jsonResponse({ pipeline_id: 'running-1', status: 'cancellation_requested' }, 202)
    if (url.pathname === '/api/pipelines/queued-1' && request.method === 'DELETE') return new Response(null, { status: 204 })
    if (url.pathname === '/api/queue/pause' && request.method === 'POST') return jsonResponse({ paused: true })
    if (url.pathname === '/api/queue/resume' && request.method === 'POST') return jsonResponse({ paused: false })
    if (url.pathname === '/api/queue' && request.method === 'DELETE') return new Response(null, { status: 204 })
    if (url.pathname === '/api/pipelines' && request.method === 'GET') {
      const page = Number(url.searchParams.get('page') ?? 1)
      const envelope = options.historyPages?.[page] ?? { items: [], total: 0, page, size: 20 }
      return jsonResponse(envelope)
    }
    if (url.pathname === '/api/pipelines/history-1' && request.method === 'GET') return jsonResponse({
      id: 'history-1', title: '完成的日常任务', source: 'manual', priority: 0, status: 'COMPLETED', task_count: 1,
      tasks: [{ id: 'task-1', type_name: 'Fight', task_name: '刷理智', status: 'COMPLETED', duration_seconds: 42 }],
    })
    if (url.pathname === '/api/pipelines/history-1/logs' && request.method === 'GET') return jsonResponse({ items: [{ id: 44, source: 'task', level: 'INFO', content: '已进入关卡 1-7', ts: 1790164800, pipeline_id: 'history-1', task_id: 'task-1', logger: 'maa.task', attachment: null }], total: 1, page: 1, size: 100 })
    if (url.pathname === '/api/pipelines/history-1/screenshots' && request.method === 'GET') return jsonResponse({ items: [{ id: 'shot-1', pipeline_id: 'history-1', task_id: 'task-1', trigger: '任务完成', backend: 'adb', path: 'screenshots/record.png', format: 'jpeg', width: 1280, height: 720, size_bytes: 32000, deleted_at: null, created_at: '2026-09-23T12:05:00Z' }], total: 1 })
    if (url.pathname === '/api/screenshots/shot-1') return new Response(new Blob(['jpeg'], { type: 'image/jpeg' }), { headers: { 'Content-Type': 'image/jpeg' } })
    return jsonResponse({ error: { message: `Unexpected request ${request.method} ${url.pathname}` } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { requests, fetchMock }
}

function renderTasksPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><TasksPage /></QueryClientProvider>)
}

function renderTaskForm(taskType: TaskTypeSchema, onChange: (value: Record<string, unknown>) => void = () => undefined) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={queryClient}><DynamicTaskForm taskType={taskType} value={{}} onChange={onChange} /></QueryClientProvider>)
}

describe('tasks schema forms, queue and history', () => {
  beforeEach(() => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders every property from the complete nine-type schema fixture', () => {
    expect(taskTypeEnvelope.items).toHaveLength(9)
    for (const taskType of taskTypeEnvelope.items) {
      const { container } = renderTaskForm(taskType)
      const properties = (taskType.schema.properties ?? {}) as Record<string, Field>
      for (const [name, field] of Object.entries(properties)) {
        if (name === 'name') continue
        expect(container.textContent).toContain(field['x-label'])
      }
      cleanup()
    }
  })

  it('filters Fight item catalog by Chinese name and stores the selected item ID', async () => {
    installFetch()
    const onChange = vi.fn()
    renderTaskForm(types.find((type) => type.name === 'Fight')!, onChange)
    const search = await screen.findByLabelText('搜索物品名称或 ID')
    fireEvent.change(search, { target: { value: '固源' } })
    fireEvent.click(screen.getByRole('button', { name: /添加 固源岩/ }))
    expect(onChange).toHaveBeenLastCalledWith({ drops: { '30013': 1 } })
  })

  it('edits Recruit duration-map at fixed star levels and converts time to minutes', () => {
    const onChange = vi.fn()
    renderTaskForm(types.find((type) => type.name === 'Recruit')!, onChange)
    const hours = screen.getByRole('spinbutton', { name: '时长小时：3' })
    expect(hours).toHaveValue(9)
    expect(screen.getByRole('spinbutton', { name: '时长分钟：3' })).toHaveValue(0)
    expect(screen.getByRole('spinbutton', { name: '时长小时：6' })).toHaveValue(9)
    fireEvent.change(hours, { target: { value: '8' } })
    expect(onChange).toHaveBeenLastCalledWith({ recruitment_time: { '3': 480 } })
  })

  it('shows x-risk confirmation and only submits the parameter changed by the user', async () => {
    const network = installFetch()
    renderTasksPage()
    await screen.findByRole('option', { name: /刷理智/ })
    fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'Fight' } })
    fireEvent.click(screen.getByRole('button', { name: '添加任务' }))
    fireEvent.change(screen.getByLabelText('最大理智药数'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: '校验并加入队列' }))

    await waitFor(() => expect(network.requests.some((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')).toBe(true))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('最大理智药数'))
    const validationRequest = network.requests.find((request) => request.url.endsWith('/api/tasks/validate'))
    expect(JSON.parse(await validationRequest!.clone().text())).toEqual({ tasks: [{ name: 'Fight', medicine: 2 }] })
    const submissionRequest = network.requests.find((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')
    expect(submissionRequest?.headers.get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(await submissionRequest!.clone().text())).toEqual({ tasks: [{ name: 'Fight', medicine: 2 }] })
    expect(await screen.findByRole('status')).toHaveTextContent('流水线已加入队列')
  })

  it('maps server validate details.field onto the corresponding form field', async () => {
    const network = installFetch({
      validateStatus: 422,
      validateBody: { error: { code: 'TASK_PARAM_INVALID', message: '任务参数无效', details: { field: 'stage', message: '关卡名无效，请检查格式' } } },
    })
    renderTasksPage()
    await screen.findByRole('option', { name: /刷理智/ })
    fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'Fight' } })
    fireEvent.click(screen.getByRole('button', { name: '添加任务' }))
    fireEvent.change(screen.getByLabelText('关卡名'), { target: { value: 'unknown' } })
    fireEvent.click(screen.getByRole('button', { name: '校验并加入队列' }))

    expect(await screen.findByText('关卡名无效，请检查格式')).toHaveAttribute('role', 'alert')
    expect(network.requests.some((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')).toBe(false)
  })

  it('maps pydantic details.fields locations onto controls', async () => {
    const network = installFetch({
      validateStatus: 422,
      validateBody: {
        error: {
          code: 'VALIDATION_ERROR',
          message: '请求参数校验失败（1 处）',
          details: {
            fields: [{
              loc: ['body', 'tasks', 0, 'FightInput', 'stage'],
              msg: 'String should have at least 1 character',
              type: 'string_too_short',
            }],
          },
        },
      },
    })
    renderTasksPage()
    await screen.findByRole('option', { name: /刷理智/ })
    fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'Fight' } })
    fireEvent.click(screen.getByRole('button', { name: '添加任务' }))
    fireEvent.change(screen.getByLabelText('关卡名'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '校验并加入队列' }))

    expect(await screen.findByText('String should have at least 1 character')).toHaveAttribute('role', 'alert')
    expect(network.requests.some((request) => request.url.endsWith('/api/pipelines') && request.method === 'POST')).toBe(false)
  })

  it('preserves server queue order and explicitly promotes before cancelling the current pipeline', async () => {
    const network = installFetch()
    renderTasksPage()
    fireEvent.click(screen.getByRole('tab', { name: '队列' }))
    expect(await screen.findByText('定时任务')).toBeInTheDocument()
    const buttons = screen.getAllByRole('button', { name: /停止当前并执行此条/ })
    fireEvent.click(buttons[0])

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('不能保证目标紧接当前项执行'))
    await screen.findByText(/目标已调至优先级/)
    const patchIndex = network.requests.findIndex((request) => request.method === 'PATCH' && request.url.endsWith('/api/queue/queued-1'))
    const cancelIndex = network.requests.findIndex((request) => request.method === 'DELETE' && request.url.endsWith('/api/pipelines/running-1'))
    expect(patchIndex).toBeGreaterThanOrEqual(0)
    expect(cancelIndex).toBeGreaterThan(patchIndex)
    expect(JSON.parse(await network.requests[patchIndex].clone().text())).toEqual({ priority: 0 })
  })

  it('uses page, size and total to stop history pagination and shows detail status', async () => {
    const network = installFetch({ historyPages: {
      1: { items: [{ id: 'history-1', title: '完成的日常任务', source: 'manual', priority: 0, status: 'COMPLETED', task_count: 1, created_at: '2026-09-23T12:00:00Z' }], total: 21, page: 1, size: 20 },
      2: { items: [{ id: 'history-2', title: '最后一条', source: 'scheduled', priority: 2, status: 'FAILED', task_count: 2 }], total: 21, page: 2, size: 20 },
    } })
    renderTasksPage()
    fireEvent.click(screen.getByRole('tab', { name: '历史' }))
    const firstRow = await screen.findByRole('button', { name: /完成的日常任务/ })
    fireEvent.click(firstRow)
    expect(await screen.findByText('刷理智')).toBeInTheDocument()
    expect(screen.getByText('状态：已完成')).toBeInTheDocument()
    expect(await screen.findByText('已进入关卡 1-7')).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: /流水线截图 1/ })).toBeInTheDocument()
    expect(network.requests.some((request) => request.url.endsWith('/api/pipelines/history-1/logs?order=asc&size=100'))).toBe(true)
    expect(network.requests.some((request) => request.url.endsWith('/api/pipelines/history-1/screenshots'))).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('最后一条')).toBeInTheDocument()
    await waitFor(() => expect(network.requests.some((request) => new URL(request.url).searchParams.get('page') === '2')).toBe(true))
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
    const historyRequests = network.requests.filter((request) => request.url.includes('/api/pipelines?'))
    expect(historyRequests.every((request) => new URL(request.url).searchParams.get('size') === '20')).toBe(true)
  })
})
