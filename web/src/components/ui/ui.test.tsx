import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Trash2 } from 'lucide-react'
import { ActionMenu, ActionSheet } from '@/components/ui/action-sheet'
import { Button } from '@/components/ui/button'
import { Chip } from '@/components/ui/chip'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { Disclosure } from '@/components/ui/disclosure'
import { ListGroup, ListItem } from '@/components/ui/list'
import { Progress } from '@/components/ui/progress'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { ResponsiveSheet } from '@/components/ui/sheet'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { Switch, SwitchRow } from '@/components/ui/switch'
import { MemoryRouter } from 'react-router'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function ConfirmHarness({ onResult }: { onResult: (value: boolean) => void }) {
  const [confirm, confirmElement] = useConfirm()
  return (
    <>
      <Button
        onClick={async () => {
          onResult(await confirm({ title: '清空 3 条待执行？', description: '已开始的任务不受影响。', confirmText: '清空 3 条待执行', tone: 'danger' }))
        }}
      >
        清空
      </Button>
      {confirmElement}
    </>
  )
}

describe('useConfirm', () => {
  it('resolves true when the consequence button is pressed', async () => {
    const onResult = vi.fn()
    render(<ConfirmHarness onResult={onResult} />)

    fireEvent.click(screen.getByRole('button', { name: '清空' }))
    const dialog = await screen.findByRole('alertdialog', { name: '清空 3 条待执行？' })
    expect(dialog).toHaveTextContent('已开始的任务不受影响。')
    fireEvent.click(screen.getByRole('button', { name: '清空 3 条待执行' }))

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })

  it('resolves false when cancelled', async () => {
    const onResult = vi.fn()
    render(<ConfirmHarness onResult={onResult} />)

    fireEvent.click(screen.getByRole('button', { name: '清空' }))
    await screen.findByRole('alertdialog')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false))
    expect(onResult).toHaveBeenCalledTimes(1)
  })
})

describe('ResponsiveSheet', () => {
  function SheetHarness() {
    const [open, setOpen] = useState(false)
    return (
      <>
        <Button onClick={() => setOpen(true)}>打开</Button>
        <ResponsiveSheet open={open} onOpenChange={setOpen} title="运行定时任务" description="选择一个定时任务立即执行" footer={<Button>运行</Button>}>
          <p>内容区域</p>
        </ResponsiveSheet>
      </>
    )
  }

  it('opens as a labelled dialog and closes from the close button', async () => {
    render(<SheetHarness />)

    fireEvent.click(screen.getByRole('button', { name: '打开' }))
    const dialog = await screen.findByRole('dialog', { name: '运行定时任务' })
    expect(dialog).toHaveTextContent('选择一个定时任务立即执行')
    expect(dialog).toHaveTextContent('内容区域')
    expect(screen.getByRole('button', { name: '运行' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})

describe('ActionSheet', () => {
  it('runs the chosen action and closes', async () => {
    const onDelete = vi.fn()
    function Harness() {
      const [open, setOpen] = useState(true)
      return <ActionSheet open={open} onOpenChange={setOpen} title="任务操作" actions={[{ label: '删除任务', icon: Trash2, tone: 'danger', onSelect: onDelete }]} />
    }
    render(<Harness />)

    fireEvent.click(await screen.findByRole('button', { name: '删除任务' }))

    expect(onDelete).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('opens from a labelled trigger and skips disabled actions', async () => {
    const onMove = vi.fn()
    render(<ActionMenu label="任务 1 的更多操作" title="任务操作" actions={[{ label: '上移', onSelect: onMove, disabled: true }]} />)

    fireEvent.click(screen.getByRole('button', { name: '任务 1 的更多操作' }))
    const moveUp = await screen.findByRole('button', { name: '上移' })
    expect(moveUp).toBeDisabled()
    fireEvent.click(moveUp)
    expect(onMove).not.toHaveBeenCalled()
  })
})

describe('form controls', () => {
  it('exposes switches with switch semantics', () => {
    const onChange = vi.fn()
    render(<Switch aria-label="启用本任务" checked={false} onCheckedChange={onChange} />)

    const control = screen.getByRole('switch', { name: '启用本任务' })
    expect(control).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(control)
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('toggles a switch row from its label', () => {
    function Harness() {
      const [checked, setChecked] = useState(true)
      return <SwitchRow label="自动诊断" description="失败时自动分析原因" checked={checked} onCheckedChange={setChecked} />
    }
    render(<Harness />)

    const control = screen.getByRole('switch', { name: '自动诊断' })
    expect(control).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByText('自动诊断'))
    expect(control).toHaveAttribute('aria-checked', 'false')
  })

  it('selects one segment at a time and never clears the selection', () => {
    function Harness() {
      const [value, setValue] = useState<'light' | 'dark' | 'system'>('system')
      return (
        <>
          <SegmentedControl
            ariaLabel="外观"
            value={value}
            onValueChange={setValue}
            options={[
              { value: 'light', label: '浅色' },
              { value: 'dark', label: '深色' },
              { value: 'system', label: '跟随系统' },
            ]}
          />
          <output data-testid="value">{value}</output>
        </>
      )
    }
    render(<Harness />)

    const group = screen.getByRole('radiogroup', { name: '外观' })
    expect(group).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: '深色' }))
    expect(screen.getByTestId('value')).toHaveTextContent('dark')
    expect(screen.getByRole('radio', { name: '深色' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('radio', { name: '深色' }))
    expect(screen.getByTestId('value')).toHaveTextContent('dark')
  })

  it('reports chip selection with aria-pressed', () => {
    render(<Chip selected>周一</Chip>)
    expect(screen.getByRole('button', { name: '周一' })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('display primitives', () => {
  it('renders progress with an accessible value', () => {
    render(<Progress value={42} label="流水线进度" />)
    const bar = screen.getByRole('progressbar', { name: '流水线进度' })
    expect(bar).toHaveAttribute('aria-valuenow', '42')
  })

  it('shows an error with a retry action', () => {
    const onRetry = vi.fn()
    render(<ErrorState message="服务不可用" onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('服务不可用')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalled()
  })

  it('shows an empty state with its next step', () => {
    render(<EmptyState title="还没有定时任务" description="按星期与时间重复执行一组任务。" action={<Button>新建定时任务</Button>} />)
    expect(screen.getByText('还没有定时任务')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建定时任务' })).toBeInTheDocument()
  })

  it('renders list items as links with their description in the accessible name', () => {
    render(
      <MemoryRouter>
        <ListGroup title="运维">
          <ListItem to="/more/updates" title="热更新" description="内核、活动资源与游戏" />
        </ListGroup>
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: '运维' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /热更新/ })).toHaveAttribute('href', '/more/updates')
  })

  it('discloses collapsed content on demand', async () => {
    render(<Disclosure title="高级" badge="已修改 2 项"><p>时区</p></Disclosure>)
    const summary = screen.getByText('高级')
    expect(screen.getByText('已修改 2 项')).toBeInTheDocument()
    await act(async () => { fireEvent.click(summary) })
    expect(screen.getByText('时区')).toBeVisible()
  })
})
