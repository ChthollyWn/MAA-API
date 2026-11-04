import { useCallback, useState, type ReactNode } from 'react'
import { ApiError, toApiError } from '@/api/errors'
import { useConfirm, type ConfirmOptions } from '@/components/ui/confirm-dialog'
import { toast } from '@/components/ui/toaster'
import { rewindSession, type RewindResult, type RewindSideEffect } from './api'

/**
 * 回溯确认框的描述（M80 §4.6）：第一句是受影响的条数，有副作用时在下面逐条列出
 * 「已经执行的操作不会撤销：」与「工具名（detail）」。
 */
export function rewindEffectDescription(
  sentence: string,
  effects: readonly RewindSideEffect[],
): ReactNode {
  return (
    <>
      <span className="block">{sentence}</span>
      {effects.length > 0 ? (
        <span className="mt-1.5 block">
          <span className="block">已经执行的操作不会撤销：</span>
          {effects.map((effect) => (
            <span key={`${effect.seq}-${effect.tool_name}`} className="block">
              {effect.detail ? `${effect.tool_name}（${effect.detail}）` : effect.tool_name}
            </span>
          ))}
        </span>
      ) : null}
    </>
  )
}

export interface RewindFlowRequest {
  /** 保留到这条消息（含）。 */
  toSeq: number
  /** 用 dry_run 的结果构造确认框；返回 null 表示不弹框，直接执行。 */
  buildConfirm: (dryRun: RewindResult) => ConfirmOptions | null
  /** 正式回溯成功后的 toast；`undefined` 表示不弹。 */
  successToast?: string
}

export interface RewindFlow {
  /** dry_run →（需要时）确认 → 正式请求；成功返回结果，取消或失败返回 null。 */
  run: (request: RewindFlowRequest) => Promise<RewindResult | null>
  pending: boolean
  /** 确认框元素，调用方负责渲染。 */
  dialog: ReactNode
}

/**
 * 回溯流程（M80 §4.6）：永远先 `dry_run` 拿到受影响条数与副作用，再决定要不要
 * 弹确认框，最后才发正式请求。「重新生成」在 dry_run 没有副作用时不弹框。
 */
export function useRewindFlow({
  sessionId,
  onRewound,
}: {
  sessionId: string | null
  onRewound?: (result: RewindResult) => void
}): RewindFlow {
  const [confirm, confirmElement] = useConfirm()
  const [pending, setPending] = useState(false)

  const run = useCallback(async (request: RewindFlowRequest): Promise<RewindResult | null> => {
    if (pending) return null
    setPending(true)
    try {
      if (!sessionId) throw new ApiError({ message: '会话还没有准备好，请稍后再试。' })
      const dryRun = await rewindSession(sessionId, { to_seq: request.toSeq, dry_run: true })
      const options = request.buildConfirm(dryRun)
      if (options && !(await confirm(options))) return null
      const result = await rewindSession(sessionId, { to_seq: request.toSeq, dry_run: false })
      onRewound?.(result)
      if (request.successToast) toast.success(request.successToast)
      return result
    } catch (error) {
      toast.error(`回溯失败：${toApiError(error).message}`)
      return null
    } finally {
      setPending(false)
    }
  }, [confirm, onRewound, pending, sessionId])

  return { run, pending, dialog: confirmElement }
}
