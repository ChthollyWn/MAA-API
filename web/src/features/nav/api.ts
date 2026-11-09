import type { QueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import type { components } from '@/types/api'

/**
 * 界面导航图页的接口层（M86 规格 §4.3.2、§4.6）。
 *
 * 一个 GET 读全图、三个 GET 之外的写操作：新建或修改界面、新建或修改跳转、删除跳转。
 * 写成功后统一失效 `keys.agent.navGraph()`。「识别当前画面」走工具调用入口，结果只
 * 在页面上显示，不写缓存。
 */
export type NavGraph = components['schemas']['NavGraphOut']
export type NavNode = components['schemas']['NavNodeOut']
export type NavNodePut = components['schemas']['NavNodePut']
export type NavEdge = components['schemas']['NavEdgeOut']
export type NavEdgePut = components['schemas']['NavEdgePut']
export type NavRecognizer = components['schemas']['NavRecognizerModel']
export type NavKind = NavNodePut['kind']

/** 与服务端 `NODE_ID_PATTERN` 同形（spec §4.2）。 */
export const NODE_ID_PATTERN = /^(?:[a-z][a-z0-9_]{1,47}|stage:[A-Za-z0-9][A-Za-z0-9_-]{0,41})$/

export const NODE_KINDS: readonly NavKind[] = ['screen', 'stage', 'menu', 'dialog']

const NODE_KIND_LABELS: Record<NavKind, string> = {
  screen: '界面',
  stage: '关卡',
  menu: '菜单',
  dialog: '弹窗',
}

const NODE_ORIGIN_LABELS: Record<string, string> = {
  seed: '种子',
  learned: '学到的',
  user: '手动',
}

export function kindLabel(kind: string): string {
  return NODE_KIND_LABELS[kind as NavKind] ?? '界面'
}

export function originLabel(origin: string): string {
  return NODE_ORIGIN_LABELS[origin] ?? '手动'
}

/** 表格「动作」列：taps 写坐标，locate 写按描述定位，sequence 写步数，key 写返回键。 */
export function actionLabel(action: Record<string, unknown>): string {
  const type = typeof action.type === 'string' ? action.type : ''
  if (type === 'tap') {
    const x = typeof action.x === 'number' ? action.x : 0
    const y = typeof action.y === 'number' ? action.y : 0
    return `点击 (${x}, ${y})`
  }
  if (type === 'locate') return '按描述定位'
  if (type === 'sequence') {
    const steps = Array.isArray(action.steps) ? action.steps.length : 0
    return `${steps} 步操作`
  }
  if (type === 'key') return '返回键'
  return '其它动作'
}

/** 表格「耗时」列；没有实测耗时时按规格写占位符。 */
export function costLabel(costMs: number | null): string {
  if (costMs === null) return '—'
  return `${(costMs / 1000).toFixed(1)} 秒`
}

/** `where_am_i` 的返回（spec §4.3.1）；`node` 认不出时为 null。 */
export interface WhereAmIResult {
  node: { id: string; title: string; kind: string } | null
  method: string
  distance: number | null
  confidence: number
  candidates: { id: string; title: string; distance: number }[]
}

export async function fetchNavGraph(signal?: AbortSignal): Promise<NavGraph> {
  const { data, error, response } = await api.GET('/api/agent/nav/graph', { signal })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function putNavNode(nodeId: string, body: NavNodePut): Promise<NavNode> {
  const { data, error, response } = await api.PUT('/api/agent/nav/nodes/{node_id}', {
    params: { path: { node_id: nodeId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function putNavEdge(edgeId: string, body: NavEdgePut): Promise<NavEdge> {
  const { data, error, response } = await api.PUT('/api/agent/nav/edges/{edge_id}', {
    params: { path: { edge_id: edgeId } },
    body,
  })
  if (error || !data) throw toApiError(error, response)
  return data
}

export async function deleteNavEdge(edgeId: string): Promise<void> {
  const { error, response } = await api.DELETE('/api/agent/nav/edges/{edge_id}', {
    params: { path: { edge_id: edgeId } },
  })
  if (error) throw toApiError(error, response)
}

/** 截一张图问导航员当前在哪；`mode` 服务端缺省 sync，请求体只带 `arguments`。 */
export async function invokeWhereAmI(): Promise<WhereAmIResult> {
  const { data, error, response } = await api.POST('/api/agent/tools/{name}/invoke', {
    params: { path: { name: 'where_am_i' } },
    body: { arguments: {} } as components['schemas']['InvokeRequest'],
  })
  if (error || !data) throw toApiError(error, response)
  return data as unknown as WhereAmIResult
}

/** 三个写操作成功后一起失效整张图。 */
export function invalidateNavGraph(queryClient: QueryClient): Promise<unknown> {
  return queryClient.invalidateQueries({ queryKey: keys.agent.navGraph() })
}
