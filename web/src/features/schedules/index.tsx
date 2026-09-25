import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Clock3, Pencil, Play, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import { DynamicTaskForm, type TaskTypeSchema } from '@/features/tasks'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { components } from '@/types/api'
import { buildWeeklyCron, parseWeeklyCron } from './schedule-time'

type ScheduleRecord = Omit<components['schemas']['ScheduleView'], 'template'> & {
  template: ScheduleTask[]
}
type ScheduleRun = components['schemas']['ScheduleRunView']
type ScheduleTask = Record<string, unknown> & {
  name: string
}
type ScheduleTemplate = components['schemas']['ScheduleWrite']['template']
type ScheduleTemplateTask = ScheduleTemplate[number]
interface ScheduleDraft {
  id: string | null
  name: string
  weekdays: number[]
  time: string
  timezone: string
  cron: string
  priority: string
  enabled: boolean
  tasks: ScheduleTask[]
}
const WEEKDAYS = [
  { value: 1, label: '周一' }, { value: 2, label: '周二' }, { value: 3, label: '周三' },
  { value: 4, label: '周四' }, { value: 5, label: '周五' }, { value: 6, label: '周六' },
  { value: 0, label: '周日' },
]
const EMPTY_DRAFT: ScheduleDraft = {
  id: null,
  name: '',
  weekdays: [1, 2, 3, 4, 5, 6, 0],
  time: '07:00',
  timezone: 'Asia/Shanghai',
  cron: '',
  priority: '2',
  enabled: true,
  tasks: [],
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function messageOf(value: unknown): string {
  if (!isObject(value)) return typeof value === 'string' ? value : '请求失败，请稍后重试'
  const body = isObject(value.error) ? value.error : value
  const nested = isObject(body.detail) ? body.detail : isObject(body.error) ? body.error : body
  const message = nested.message
  const code = nested.code
  const status = isObject(value.response) ? value.response.status : undefined
  const cause = typeof message === 'string' ? message : typeof code === 'string' ? code : '请求失败'
  if (status === 409 && code === 'SCHEDULE_NAME_CONFLICT') return '名称已被使用，请换一个名称。' + cause
  if (status === 409 && code === 'PIPELINE_ALREADY_RUNNING') return '该定时任务仍有未完成流水线，请稍后再试。'
  if (status === 422 || code === 'TASK_PARAM_INVALID' || code === 'SCHEDULE_CRON_INVALID') return '配置校验未通过：' + cause
  return cause
}
function scheduleRecord(value: components['schemas']['ScheduleView']): ScheduleRecord {
  return {
    ...value,
    template: value.template.map((task) => {
      if (typeof task.name !== 'string') throw new Error('定时任务模板中的任务缺少类型名称。')
      return { ...task, name: task.name }
    }),
  }
}
function dateLabel(value?: string | null): string {
  if (!value) return '暂无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
function runStatus(run?: ScheduleRun | null): string {
  if (!run) return '尚无执行记录'
  const status = run.status?.toLowerCase()
  if (status === 'completed' || status === 'success' || status === 'succeeded') return '成功'
  if (status === 'failed' || status === 'error') return '失败'
  if (status === 'running') return '运行中'
  if (status === 'pending') return '排队中'
  if (status === 'skipped') return '已跳过（该定时任务仍有未完成流水线）'
  return run.status || '结果未知'
}
function toScheduleTemplate(tasks: ScheduleTask[]): ScheduleTemplate {
  return tasks.map((task): ScheduleTemplateTask => {
    switch (task.name) {
      case 'StartUp': return { ...task, name: 'StartUp' }
      case 'CloseDown': return { ...task, name: 'CloseDown' }
      case 'Fight': return { ...task, name: 'Fight' }
      case 'Recruit': return { ...task, name: 'Recruit' }
      case 'Infrast': return { ...task, name: 'Infrast' }
      case 'Mall': return { ...task, name: 'Mall' }
      case 'Award': return { ...task, name: 'Award' }
      case 'Roguelike': return { ...task, name: 'Roguelike' }
      case 'Reclamation': return { ...task, name: 'Reclamation' }
      default: throw new Error(`服务端不接受任务类型「${task.name}」的 schedule 模板。请移除该任务或刷新任务类型。`)
    }
  })
}
function weeklyDescription(cron: string, timezone: string): string | null {
  const parsed = parseWeeklyCron(cron, timezone)
  if (!parsed) return null
  const days = parsed.weekdays.length === 7 ? '每天' : WEEKDAYS.filter(({ value }) => parsed.weekdays.includes(value)).map(({ label }) => label).join('、')
  return days + ' ' + parsed.time + '（' + timezone + '）'
}
function toDraft(record: ScheduleRecord): ScheduleDraft {
  const visual = parseWeeklyCron(record.cron, record.timezone)
  return {
    id: record.id,
    name: record.name,
    weekdays: visual?.weekdays ?? [],
    time: visual?.time ?? '07:00',
    timezone: record.timezone || 'UTC',
    cron: record.cron,
    priority: '2',
    enabled: record.enabled,
    tasks: Array.isArray(record.template) ? record.template : [],
  }
}

export default function SchedulesPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<ScheduleDraft>(EMPTY_DRAFT)
  const [editing, setEditing] = useState(false)
  const [notice, setNotice] = useState('')
  const [noticeIsError, setNoticeIsError] = useState(false)
  useEffect(() => {
    const navigationState = location.state && typeof location.state === 'object'
      ? location.state as { apiConsolePrefill?: { template?: unknown } }
      : null
    const template = navigationState?.apiConsolePrefill?.template
    if (!Array.isArray(template)) return
    const validTasks = template.filter((task): task is ScheduleTask =>
      Boolean(task && typeof task === 'object' && !Array.isArray(task) && typeof (task as Record<string, unknown>).name === 'string'))
    if (validTasks.length === 0) return
    setDraft({ ...EMPTY_DRAFT, tasks: validTasks.map((task) => ({ ...task })) })
    setEditing(true)
    setNotice('已载入 API 调试台收藏的流水线模板；设置星期与时间后即可保存。')
    setNoticeIsError(false)
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null })
  }, [location.key, location.pathname, location.search, location.state, navigate])
  const schedulesQuery = useQuery({
    queryKey: ['schedules'],
    queryFn: async () => {
      const result = await api.GET('/api/schedules')
      if (result.error) throw { error: result.error, response: result.response }
      if (!result.data) throw new Error('定时任务服务没有返回列表。')
      return result.data.items.map(scheduleRecord)
    },
    retry: false,
  })
  const typeQuery = useQuery({
    queryKey: ['tasks', 'catalog'],
    queryFn: async () => {
      const result = await api.GET('/api/tasks/types')
      if (result.error) throw result.error
      return result.data as { items: TaskTypeSchema[] }
    },
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
  const taskTypes = typeQuery.data?.items ?? []
  const schedules = schedulesQuery.data ?? []
  const saveMutation = useMutation({
    mutationFn: async (value: ScheduleDraft) => {
      const cron = value.cron || buildWeeklyCron(value.weekdays, value.time)
      if (!value.name.trim()) throw new Error('请填写 schedule 名称。')
      if (!cron) throw new Error('请选择至少一个星期，并填写有效的时间。')
      if (value.tasks.length === 0) throw new Error('请至少添加一个任务到模板。')
      const priority = Number(value.priority)
      if (!Number.isInteger(priority) || priority < 0 || priority > 2) throw new Error('队列优先级必须为 0、1 或 2。')
      const payload = {
        name: value.name.trim(),
        cron,
        timezone: value.timezone.trim() || 'Asia/Shanghai',
        template: toScheduleTemplate(value.tasks),
        enabled: value.enabled,
        priority,
        misfire_grace_seconds: 300,
        catch_up: false,
        skip_if_running: true,
      }
      const result = value.id
        ? await api.PUT('/api/schedules/{schedule_id}', { params: { path: { schedule_id: value.id } }, body: payload })
        : await api.POST('/api/schedules', { body: payload })
      if (result.error) throw { error: result.error, response: result.response }
      return value.id ? '定时任务已更新。' : '定时任务已创建。'
    },
    onSuccess: async (successMessage) => {
      setNotice(successMessage)
      setNoticeIsError(false)
      setDraft(EMPTY_DRAFT)
      setEditing(false)
      await queryClient.invalidateQueries({ queryKey: ['schedules'] })
    },
    onError: (error) => {
      setNotice('保存失败：' + messageOf(error))
      setNoticeIsError(true)
    },
  })
  const actionMutation = useMutation({
    mutationFn: async (action: { kind: 'toggle' | 'delete' | 'run'; schedule: ScheduleRecord }) => {
      const id = encodeURIComponent(action.schedule.id)
      if (action.kind === 'toggle') {
        const result = await api.PATCH('/api/schedules/{schedule_id}', { params: { path: { schedule_id: id } }, body: { enabled: !action.schedule.enabled } })
        if (result.error) throw { error: result.error, response: result.response }
        return action.schedule.enabled ? '定时任务已停用。' : '定时任务已启用。'
      }
      if (action.kind === 'delete') {
        const result = await api.DELETE('/api/schedules/{schedule_id}', { params: { path: { schedule_id: id } } })
        if (result.error) throw { error: result.error, response: result.response }
        return '定时任务已删除。'
      }
      const result = await api.POST('/api/schedules/{schedule_id}/run', { params: { path: { schedule_id: id } }, body: {} })
      if (result.error) throw { error: result.error, response: result.response }
      return '已提交立即运行请求；可在近期结果中查看状态。'
    },
    onSuccess: async (successMessage) => {
      setNotice(successMessage)
      setNoticeIsError(false)
      await queryClient.invalidateQueries({ queryKey: ['schedules'] })
    },
    onError: (error) => {
      setNotice('操作失败：' + messageOf(error))
      setNoticeIsError(true)
    },
  })
  const cronPreview = useMemo(() => draft.cron || buildWeeklyCron(draft.weekdays, draft.time), [draft.cron, draft.time, draft.weekdays])
  const beginCreate = () => { setDraft(EMPTY_DRAFT); setEditing(true); setNotice('') }
  const beginEdit = (schedule: ScheduleRecord) => { setDraft(toDraft(schedule)); setEditing(true); setNotice('') }
  const updateTask = (index: number, task: ScheduleTask) => {
    setDraft((current) => ({ ...current, tasks: current.tasks.map((item, taskIndex) => taskIndex === index ? task : item) }))
  }

  return <main className="mx-auto w-full max-w-5xl space-y-5 px-4 pb-24 pt-5 sm:px-6">
    <header className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">更多 · 定时任务</p>
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">定时任务</h1>
      <p className="max-w-2xl text-sm text-muted-foreground">按星期、时间和命名任务组合创建 schedule。一个组合可覆盖多个星期；不同任务组合分别建立 schedule。</p>
    </header>
    <Card className="border-primary/20 bg-primary/5">
      <CardHeader className="flex-row items-start gap-3">
        <CalendarClock className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
        <div className="space-y-1"><CardTitle>从旧版星期任务组开始</CardTitle><CardDescription>服务启动时，数据库迁移会读取旧 `resource/daily_task.json`，把按星期配置展开为 schedule；旧星期索引也会自动校正。新建时，勾选该任务组运行的星期，设置时刻、时区并配置任务模板。</CardDescription></div>
      </CardHeader>
      <CardContent className="pt-0"><p className="text-xs text-muted-foreground">页面通过 schedule API 管理迁移后的记录；它不会直接读取或写回旧配置文件。</p></CardContent>
    </Card>
    {notice && <p role={noticeIsError ? 'alert' : 'status'} className={'rounded-lg border px-4 py-3 text-sm ' + (noticeIsError ? 'border-destructive/40 text-destructive' : 'text-muted-foreground')}>{notice}</p>}

    {editing ? <Card>
      <CardHeader><CardTitle>{draft.id ? '编辑定时任务' : '新建定时任务'}</CardTitle><CardDescription>星期与时间会转换为 cron；时区决定 cron 按哪个本地时钟运行。</CardDescription></CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2"><Label htmlFor="schedule-name">命名任务组名称</Label><Input id="schedule-name" value={draft.name} maxLength={64} placeholder="例如：日常、剿灭、周本" onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} /></div>
          <div className="space-y-2"><Label htmlFor="schedule-timezone">时区</Label><Input id="schedule-timezone" value={draft.timezone} placeholder="例如 Asia/Shanghai" onChange={(event) => setDraft((current) => ({ ...current, timezone: event.target.value }))} /><p className="text-xs text-muted-foreground">使用 IANA 时区名；当前设备默认值为 {EMPTY_DRAFT.timezone}。</p></div>
          <div className="space-y-2 sm:col-span-2">
            <span className="text-sm font-medium">执行星期</span>
            <div className="grid grid-cols-4 gap-2 sm:grid-cols-7" role="group" aria-label="执行星期">
              {WEEKDAYS.map(({ value, label }) => {
                const selected = draft.weekdays.includes(value)
                return <Button key={value} type="button" size="sm" className="min-h-11" variant={selected ? 'default' : 'outline'} aria-pressed={selected} onClick={() => setDraft((current) => ({ ...current, cron: '', weekdays: selected ? current.weekdays.filter((day) => day !== value) : [...current.weekdays, value] }))}>{label}</Button>
              })}
            </div>
          </div>
          <div className="space-y-2"><Label htmlFor="schedule-time">执行时间</Label><Input id="schedule-time" type="time" value={draft.time} onChange={(event) => setDraft((current) => ({ ...current, time: event.target.value, cron: '' }))} /></div>
          <div className="space-y-2"><Label htmlFor="schedule-priority">队列优先级</Label><Input id="schedule-priority" type="number" min={2} max={2} step={1} value="2" disabled /><p className="text-xs text-muted-foreground">定时任务固定使用优先级 2，排在手动和 Agent 流水线之后。</p></div>
          <div className="flex min-h-11 items-center gap-3 sm:col-span-2"><input id="schedule-enabled" type="checkbox" className="size-5 accent-primary" checked={draft.enabled} onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))} /><Label htmlFor="schedule-enabled">创建后启用此定时任务</Label></div>
        </div>
        <div className="rounded-lg border bg-muted/30 p-3 text-sm">
          {cronPreview
            ? <p><span className="font-medium">cron 预览：</span><code className="ml-2 select-all rounded bg-background px-2 py-1">{cronPreview}</code><span className="ml-2 text-muted-foreground">{weeklyDescription(cronPreview, draft.timezone) ?? ''}</span></p>
            : <p className="text-destructive">选择至少一个星期并填写有效时间，才能生成 cron。</p>}
          <p className="mt-2 text-xs text-muted-foreground">cron 星期按标准约定：0=周日，1=周一，…，6=周六。</p>
        </div>
        {!parseWeeklyCron(draft.cron, draft.timezone) && draft.cron && <div className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
          <p className="text-sm font-medium">这条 cron 超出可视化星期范围</p>
          <p className="break-all text-sm text-muted-foreground">原值：<code>{draft.cron}</code>。它会按原值保存；如果要改成星期计划，请使用上面的星期和时间控件。</p>
          <Label htmlFor="schedule-raw-cron">编辑 cron 原值</Label><Input id="schedule-raw-cron" value={draft.cron} onChange={(event) => setDraft((current) => ({ ...current, cron: event.target.value }))} aria-describedby="schedule-raw-cron-help" />
          <p id="schedule-raw-cron-help" className="text-xs text-muted-foreground">仅适用于无法由星期与时刻控件表示的既有表达式。</p>
        </div>}
        <section className="space-y-3 border-t pt-4" aria-labelledby="schedule-template-heading">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><h3 id="schedule-template-heading" className="font-semibold">任务组合模板</h3><p className="mt-1 text-sm text-muted-foreground">模板参数由任务 API schema 驱动。</p></div>
            <select aria-label="要添加的任务类型" className="min-h-11 rounded-lg border border-input bg-background px-3 text-sm" value="" onChange={(event) => {
              const selectedType = taskTypes.find((type) => type.name === event.target.value)
              if (selectedType) setDraft((current) => ({ ...current, tasks: [...current.tasks, { name: selectedType.name }] }))
              event.currentTarget.value = ''
            }} disabled={typeQuery.isLoading || taskTypes.length === 0}>
              <option value="">添加任务类型</option>{taskTypes.map((type) => <option key={type.name} value={type.name}>{type.label}</option>)}
            </select>
          </div>
          {typeQuery.error && <p role="alert" className="text-sm text-destructive">任务类型读取失败：{messageOf(typeQuery.error)}</p>}
          {draft.tasks.length === 0 ? <p className="rounded-lg border border-dashed p-5 text-center text-sm text-muted-foreground">尚无模板任务，请从任务类型列表添加。</p> : <ol className="space-y-4">{draft.tasks.map((task, index) => {
            const taskType = taskTypes.find((type) => type.name === task.name)
            return <li key={index} className="rounded-xl border p-3 sm:p-4">
              <div className="mb-3 flex items-start justify-between gap-3"><div className="min-w-0"><h4 className="font-medium">{taskType?.label ?? task.name}</h4>{taskType?.description && <p className="mt-1 text-sm text-muted-foreground">{taskType.description}</p>}</div><Button type="button" size="icon" variant="ghost" aria-label={'移除' + (taskType?.label ?? task.name)} onClick={() => setDraft((current) => ({ ...current, tasks: current.tasks.filter((_, taskIndex) => taskIndex !== index) }))}><Trash2 aria-hidden="true" /></Button></div>
              {taskType ? <DynamicTaskForm taskType={taskType} value={Object.fromEntries(Object.entries(task).filter(([key]) => key !== 'name'))} disabled={saveMutation.isPending} onChange={(params) => updateTask(index, { name: task.name, ...params })} /> : <p role="alert" className="text-sm text-destructive">服务端未返回任务类型「{task.name}」的 schema。模板中的原任务会保留，请先刷新任务类型或移除此任务。</p>}
            </li>
          })}</ol>}
        </section>
        <div className="flex flex-wrap gap-2 border-t pt-4">
          <Button type="button" disabled={saveMutation.isPending || typeQuery.isLoading} onClick={() => {
            setNotice('正在由服务端校验 schedule 与任务模板…')
            setNoticeIsError(false)
            saveMutation.mutate(draft)
          }}>{saveMutation.isPending ? '正在保存…' : draft.id ? '保存修改' : '创建 schedule'}</Button>
          <Button type="button" variant="outline" disabled={saveMutation.isPending} onClick={() => { setEditing(false); setDraft(EMPTY_DRAFT); setNotice('编辑已取消。'); setNoticeIsError(false) }}>取消</Button>
        </div>
    </CardContent></Card> : <div className="flex justify-end"><Button type="button" onClick={beginCreate}><Plus aria-hidden="true" />新建定时任务</Button></div>}
    <section className="space-y-3" aria-labelledby="schedule-list-heading">
      <div className="flex items-center justify-between gap-3"><div><h2 id="schedule-list-heading" className="text-lg font-semibold">已配置的定时任务</h2><p className="mt-1 text-sm text-muted-foreground">启停、编辑、删除或立即运行，近期结果保留在每张卡片上。</p></div><Button type="button" size="icon" variant="outline" aria-label="刷新定时任务" disabled={schedulesQuery.isFetching} onClick={() => void schedulesQuery.refetch()}><RefreshCw aria-hidden="true" className={schedulesQuery.isFetching ? 'animate-spin motion-reduce:animate-none' : ''} /></Button></div>
      {schedulesQuery.error && <p role="alert" className="rounded-lg border border-destructive/40 p-3 text-sm text-destructive">定时任务列表读取失败：{messageOf(schedulesQuery.error)}</p>}
      {schedulesQuery.isLoading ? <p role="status" className="rounded-xl border p-6 text-center text-sm text-muted-foreground">正在读取定时任务…</p> : schedules.length === 0 ? <Card><CardContent className="py-10 text-center"><Clock3 className="mx-auto size-8 text-muted-foreground" aria-hidden="true" /><p className="mt-3 font-medium">还没有定时任务</p><p className="mt-1 text-sm text-muted-foreground">按星期新建一条 schedule，即可开始重建旧版日常任务组流程。</p><div className="mt-4"><Button type="button" onClick={beginCreate}><Plus aria-hidden="true" />创建第一条</Button></div></CardContent></Card> : <ul className="space-y-3">{schedules.map((schedule) => {
        const description = weeklyDescription(schedule.cron, schedule.timezone)
        const runs = schedule.recent_runs
        const latest = schedule.last_run_result ?? runs[0]
        return <li key={schedule.id}><Card>
          <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0 space-y-2"><div className="flex flex-wrap items-center gap-2"><CardTitle className="break-words">{schedule.name}</CardTitle><Badge variant={schedule.enabled ? 'secondary' : 'outline'}>{schedule.enabled ? '已启用' : '已停用'}</Badge></div>
              <CardDescription>{description ? '每周 ' + description : <>无法解析为星期计划，cron 原值：<code className="select-all">{schedule.cron}</code>（{schedule.timezone}）</>}</CardDescription>
              <p className="text-xs text-muted-foreground">队列优先级 2（定时来源） · 下次执行：{dateLabel(schedule.next_run_at)}</p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <Button type="button" size="sm" variant="outline" className="min-h-11" disabled={actionMutation.isPending} onClick={() => actionMutation.mutate({ kind: 'toggle', schedule })}>{schedule.enabled ? '停用' : '启用'}</Button>
              <Button type="button" size="sm" variant="outline" className="min-h-11" disabled={actionMutation.isPending} onClick={() => beginEdit(schedule)}><Pencil aria-hidden="true" />编辑</Button>
              <Button type="button" size="sm" variant="outline" disabled={actionMutation.isPending} onClick={() => {
                if (window.confirm('立即运行「' + schedule.name + '」的模板？将按定时来源优先级 2 提交一条流水线。')) actionMutation.mutate({ kind: 'run', schedule })
              }} className="min-h-11"><Play aria-hidden="true" />立即运行</Button>
              <Button type="button" size="sm" variant="destructive" disabled={actionMutation.isPending} onClick={() => {
                if (window.confirm('删除定时任务「' + schedule.name + '」？已执行的历史记录会按服务端策略保留。')) actionMutation.mutate({ kind: 'delete', schedule })
              }} className="min-h-11"><Trash2 aria-hidden="true" />删除</Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">模板包含 {schedule.template?.length ?? 0} 个任务 · 最近执行：{dateLabel(latest?.finished_at ?? latest?.started_at ?? schedule.last_run_at)} · 结果：{runStatus(latest)}</p>
            {typeof latest?.error?.message === 'string' && <p className="text-sm text-destructive">{latest.error.message}</p>}
            {runs.length > 1 && <div className="space-y-2 border-t pt-3"><h3 className="text-sm font-medium">近期结果</h3><ul className="space-y-2">{runs.slice(0, 5).map((run, index) => <li key={run.pipeline_id ?? String(run.started_at) + index} className="flex flex-wrap items-center justify-between gap-2 text-sm"><span>{dateLabel(run.finished_at ?? run.started_at)}</span><span className={run.status?.toLowerCase() === 'failed' ? 'text-destructive' : 'text-muted-foreground'}>{runStatus(run)}{run.pipeline_id ? ' · 流水线 ' + run.pipeline_id : ''}</span></li>)}</ul></div>}
          </CardContent>
        </Card></li>
      })}</ul>}
    </section>
  </main>
}
