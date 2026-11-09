import { Plus, Route } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/states'
import { Switch } from '@/components/ui/switch'
import { actionLabel, costLabel, type NavEdge, type NavNode } from './api'

export interface EdgeTableProps {
  edges: NavEdge[]
  nodes: NavNode[]
  onToggle: (edge: NavEdge) => void
  onDelete: (edge: NavEdge) => void
  onCreate: () => void
}

const COLUMNS = ['从', '到', '动作', '成功/失败', '耗时', '状态', '操作'] as const

/**
 * 「跳转」区的表格（M86 §4.6）。
 *
 * 动作列把四类动作翻成中文；每行末尾是启用开关与删除按钮。界面标题用 `nodes` 的
 * 中文名，节点缺失时退回 id，避免表格里只剩空白。
 */
export function EdgeTable({ edges, nodes, onToggle, onDelete, onCreate }: EdgeTableProps) {
  const titles = new Map(nodes.map((node) => [node.id, node.title]))
  const title = (nodeId: string) => titles.get(nodeId) ?? nodeId

  if (edges.length === 0) {
    return (
      <div className="rounded-xl border bg-card shadow-card">
        <EmptyState
          icon={Route}
          title="还没有跳转"
          description="新建一条常用界面之间的跳转，导航员就能一步走到"
          action={
            <Button size="sm" variant="outline" onClick={onCreate}>
              <Plus aria-hidden="true" />
              新建跳转
            </Button>
          }
        />
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-xl border bg-card shadow-card">
      <table className="w-full min-w-[44rem] text-sm">
        <thead className="border-b bg-surface-2/60 text-left text-footnote text-muted-foreground">
          <tr>
            {COLUMNS.map((column) => (
              <th key={column} scope="col" className="whitespace-nowrap px-3 py-2 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {edges.map((edge) => (
            <tr key={edge.id} data-nav-edge-id={edge.id}>
              <td className="whitespace-nowrap px-3 py-2">{title(edge.from_node)}</td>
              <td className="whitespace-nowrap px-3 py-2">{title(edge.to_node)}</td>
              <td className="whitespace-nowrap px-3 py-2">{actionLabel(edge.action)}</td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                {edge.success_count}/{edge.fail_count}
              </td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">{costLabel(edge.cost_ms)}</td>
              <td className="whitespace-nowrap px-3 py-2">
                <Badge variant={edge.enabled ? 'success' : 'neutral'}>{edge.enabled ? '已启用' : '已停用'}</Badge>
              </td>
              <td className="px-3 py-2">
                <div className="flex items-center justify-end gap-2">
                  <Switch aria-label="启用" checked={edge.enabled} onCheckedChange={() => onToggle(edge)} />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="text-danger"
                    aria-label="删除跳转"
                    onClick={() => onDelete(edge)}
                  >
                    删除跳转
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
