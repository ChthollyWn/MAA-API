import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, Plus } from 'lucide-react'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { useConfirm } from '@/components/ui/confirm-dialog'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Section } from '@/components/ui/section'
import { ErrorState, LoadingState } from '@/components/ui/states'
import { toast } from '@/components/ui/toaster'
import { EdgeEditor } from './EdgeEditor'
import { EdgeTable } from './EdgeTable'
import { NodeEditor } from './NodeEditor'
import { NodeList } from './NodeList'
import {
  deleteNavEdge,
  fetchNavGraph,
  invalidateNavGraph,
  invokeWhereAmI,
  putNavEdge,
  type NavEdge,
  type WhereAmIResult,
} from './api'

const NAV_HINT = '导航员按这张图从当前界面一步走到目标。跳转失败三次会自动停用，可以在这里重新启用或删掉。'

/**
 * 「更多 › 界面导航图」（M86 §4.6）。
 *
 * 上方是识别当前画面与整张图的计数；「界面」卡片点开编辑样本与识别区域；「跳转」表格
 * 可以启停与删除。三个写操作成功后只失效 `keys.agent.navGraph()`；识别结果放本地
 * state，不进缓存。
 */
export default function NavGraphPage() {
  const queryClient = useQueryClient()
  const graphQuery = useQuery({
    queryKey: keys.agent.navGraph(),
    queryFn: ({ signal }) => fetchNavGraph(signal),
    staleTime: 15_000,
    retry: false,
  })
  const [confirm, confirmElement] = useConfirm()

  const [recognizing, setRecognizing] = useState(false)
  const [recognition, setRecognition] = useState<WhereAmIResult | null>(null)
  const [recognizeError, setRecognizeError] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<string | 'new' | null>(null)
  const [edgeEditorOpen, setEdgeEditorOpen] = useState(false)

  const nodes = graphQuery.data?.nodes ?? []
  const edges = graphQuery.data?.edges ?? []
  const editingNode = editingId !== null && editingId !== 'new'
    ? nodes.find((node) => node.id === editingId) ?? null
    : null

  const invalidate = () => {
    void invalidateNavGraph(queryClient)
  }

  const toggle = useMutation({
    mutationFn: (edge: NavEdge) => putNavEdge(edge.id, { enabled: !edge.enabled }),
    onSuccess: (_edge, previous) => {
      toast.success(previous.enabled ? '已停用跳转' : '已启用跳转')
      invalidate()
    },
    onError: (error) => toast.error(`保存跳转失败：${toApiError(error).message}`),
  })

  const remove = useMutation({
    mutationFn: (edge: NavEdge) => deleteNavEdge(edge.id),
    onSuccess: () => {
      toast.success('已删除跳转')
      invalidate()
    },
    onError: (error) => toast.error(`删除跳转失败：${toApiError(error).message}`),
  })

  const askDelete = async (edge: NavEdge) => {
    const ok = await confirm({
      title: '删除这条跳转？',
      description: '删除后导航不会再走这条路，需要时可以重新新建。',
      confirmText: '删除跳转',
      tone: 'danger',
    })
    if (ok) remove.mutate(edge)
  }

  const recognize = async () => {
    setRecognizing(true)
    setRecognizeError(null)
    try {
      setRecognition(await invokeWhereAmI())
    } catch (caught) {
      setRecognition(null)
      setRecognizeError(toApiError(caught).message)
    } finally {
      setRecognizing(false)
    }
  }

  const hint = graphQuery.data && !graphQuery.data.vision_available
    ? `${NAV_HINT}当前没有可用的视觉模型，标着「按描述定位」的跳转不会被使用。`
    : NAV_HINT

  const titleBar = { title: '界面导航图', back: '/more' }

  if (graphQuery.isPending) {
    return (
      <Page {...titleBar}>
        <div className="rounded-xl border bg-card shadow-card">
          <LoadingState label="正在读取导航图…" />
        </div>
      </Page>
    )
  }

  if (graphQuery.error) {
    return (
      <Page {...titleBar}>
        <div className="rounded-xl border bg-card shadow-card">
          <ErrorState
            title="导航图读取失败"
            message={`读取导航图失败：${toApiError(graphQuery.error).message}`}
            onRetry={() => void graphQuery.refetch()}
            retrying={graphQuery.isFetching}
          />
        </div>
      </Page>
    )
  }

  return (
    <Page {...titleBar}>
      <div className="space-y-5 pb-4">
        <Section
          title="界面导航图"
          description={`${nodes.length} 个界面 · ${edges.length} 条跳转`}
          hint={hint}
          actions={
            <Button
              type="button"
              size="sm"
              variant="outline"
              loading={recognizing}
              onClick={() => void recognize()}
            >
              <Camera aria-hidden="true" />
              识别当前画面
            </Button>
          }
        >
          <div className="space-y-2">
            {recognition ? (
              <InlineAlert tone={recognition.node ? 'info' : 'warning'}>
                {recognition.node ? `当前画面：${recognition.node.title}` : '没有认出当前画面'}
              </InlineAlert>
            ) : null}
            {recognizeError ? (
              <InlineAlert tone="danger">识别当前画面失败：{recognizeError}</InlineAlert>
            ) : null}
          </div>
        </Section>

        <Section
          title="界面"
          actions={
            <Button type="button" size="sm" variant="outline" onClick={() => setEditingId('new')}>
              <Plus aria-hidden="true" />
              新建界面
            </Button>
          }
        >
          <NodeList nodes={nodes} onSelect={(node) => setEditingId(node.id)} onCreate={() => setEditingId('new')} />
        </Section>

        <Section
          title="跳转"
          actions={
            <Button type="button" size="sm" variant="outline" onClick={() => setEdgeEditorOpen(true)}>
              <Plus aria-hidden="true" />
              新建跳转
            </Button>
          }
        >
          <EdgeTable
            edges={edges}
            nodes={nodes}
            onToggle={(edge) => toggle.mutate(edge)}
            onDelete={(edge) => void askDelete(edge)}
            onCreate={() => setEdgeEditorOpen(true)}
          />
        </Section>
      </div>

      <NodeEditor
        key={`nav-node-${editingId ?? 'closed'}`}
        open={editingId !== null}
        node={editingNode}
        onClose={() => setEditingId(null)}
        onSaved={invalidate}
      />
      <EdgeEditor
        key={`nav-edge-${edgeEditorOpen ? 'open' : 'closed'}`}
        open={edgeEditorOpen}
        nodes={nodes}
        onClose={() => setEdgeEditorOpen(false)}
        onSaved={invalidate}
      />
      {confirmElement}
    </Page>
  )
}
