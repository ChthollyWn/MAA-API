import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { ApiError, toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { toast } from '@/components/ui/toaster'
import { collectFieldIssues, errorMessage, mapFieldIssues, serializeTask, type TaskDraft, type TaskTypeSchema } from '../form'
import { materializeDrafts } from './drafts'
import { useComposerStore } from './store'

export const composerSubmitKey = ['tasks', 'composer', 'submit'] as const

export interface SubmitVariables {
  idempotencyKey: string
  drafts: TaskDraft[]
  title: string
}

export interface ComposerNotice {
  tone: 'warning' | 'danger'
  message: string
}

interface SubmitCallbacks {
  onNotice: (notice: ComposerNotice | null) => void
  onSubmitted: () => void
  /** 字段错误已写入草稿 store。 */
  onFieldErrors: () => void
}

const TIMEOUT_NOTICE = '服务端未在预期时间内响应（504），这条流水线可能已经入队。正在确认状态；再次点击会使用同一请求编号。'

/**
 * 编排提交：先 `POST /api/tasks/validate`，通过后带 `Idempotency-Key` 提交 `POST /api/pipelines`。
 * 504 时查询当前流水线；草稿与请求编号保留，再次提交复用同一编号。
 */
export function useSubmitPipeline(taskTypes: readonly TaskTypeSchema[], callbacks: SubmitCallbacks) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: composerSubmitKey,
    mutationFn: async ({ idempotencyKey, drafts, title }: SubmitVariables) => {
      // 兜底再物化一次：草稿可能来自旧缓存或直接写进 store 的调用方。
      const tasks = materializeDrafts(drafts, taskTypes).map(serializeTask)
      const validation = await api.POST('/api/tasks/validate', { body: { tasks } as never })
      if (validation.error) throw toApiError(validation.error, validation.response)
      const submitted = await api.POST('/api/pipelines', {
        body: { tasks, ...(title ? { title } : {}) } as never,
        headers: { 'Idempotency-Key': idempotencyKey },
      })
      if (submitted.error) throw toApiError(submitted.error, submitted.response)
      return submitted.data
    },
    onSuccess: async () => {
      useComposerStore.getState().reset()
      toast.success('流水线已加入队列')
      callbacks.onSubmitted()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.queue.all() }),
        queryClient.invalidateQueries({ queryKey: keys.pipelines.lists() }),
      ])
    },
    onError: async (error, variables) => {
      const { issues, message } = collectFieldIssues(error, variables.drafts.length)
      const mapped = mapFieldIssues(issues, variables.drafts, taskTypes)
      useComposerStore.getState().setErrors(mapped)
      if (error instanceof ApiError && error.status === 504) {
        callbacks.onNotice({ tone: 'warning', message: TIMEOUT_NOTICE })
        try {
          const { data, error: currentError, response } = await api.GET('/api/pipelines/current')
          if (!currentError) queryClient.setQueryData(keys.pipelines.current(), data)
          else callbacks.onNotice({ tone: 'warning', message: `服务端未在预期时间内响应（504），且当前流水线查询失败：${toApiError(currentError, response).message}。再次点击会使用同一请求编号。` })
        } catch (statusError) {
          callbacks.onNotice({ tone: 'warning', message: `服务端未在预期时间内响应（504），当前状态查询失败：${errorMessage(statusError)}。再次点击会使用同一请求编号。` })
        }
        await queryClient.invalidateQueries({ queryKey: keys.queue.all() })
      } else if (Object.keys(mapped).length > 0) {
        callbacks.onNotice(null)
        callbacks.onFieldErrors()
      } else {
        callbacks.onNotice({ tone: 'danger', message: issues.length > 0 ? `校验未通过：${message}` : `提交失败：${message}` })
      }
    },
  })
}
