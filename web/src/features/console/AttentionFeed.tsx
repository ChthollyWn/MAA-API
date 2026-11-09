import { toApiError } from '@/api/errors'
import { Section } from '@/components/ui/section'
import { ErrorState } from '@/components/ui/states'
import { ErrorAlert } from '@/features/agent/StatusAlerts'
import { AttentionList } from './AttentionList'
import type { AttentionState } from './use-attention'

export interface AttentionFeedProps {
  attention: AttentionState
  specTitles: ReadonlyMap<string, string>
}

/**
 * 「待你处理」区块（spec `console-attention-inline` §4.1）：指挥台总控会话内容的最后一块，
 * 桌面与手机同一位置，随消息滚动。有事项时渲染标题与列表；空时不占位置；
 * 读取失败给出可重试的错误块；确认/计划/告警的 resolver 错误在列表清空后仍要留在页面上。
 */
export function AttentionFeed({ attention, specTitles }: AttentionFeedProps) {
  const resolverErrors = (
    <>
      {attention.resolver.error ? (
        <ErrorAlert error={attention.resolver.error} onDismiss={attention.resolver.clearError} />
      ) : null}
      {attention.planResolver.error ? (
        <ErrorAlert
          error={{ ...attention.planResolver.error, message: `处理计划失败：${attention.planResolver.error.message}` }}
          onDismiss={attention.planResolver.clearError}
        />
      ) : null}
      {attention.alertResolver.error ? (
        <ErrorAlert error={attention.alertResolver.error} onDismiss={attention.alertResolver.clearError} />
      ) : null}
    </>
  )
  const hasResolverErrors = Boolean(
    attention.resolver.error || attention.planResolver.error || attention.alertResolver.error,
  )

  if (attention.error) {
    return (
      <div className="mt-4 border-t pt-4">
        <ErrorState
          message={`读取待你处理的事项失败：${toApiError(attention.error).message}`}
          onRetry={attention.refetch}
        />
      </div>
    )
  }
  // 首屏还在读时与消息历史一起等待，不在会话里闪一块加载态。
  if (attention.isPending && attention.items.length === 0) return null
  if (attention.items.length === 0) {
    if (!hasResolverErrors) return null
    return <div className="mt-4 space-y-3 border-t pt-4">{resolverErrors}</div>
  }

  return (
    <Section title="待你处理" description={`${attention.items.length} 件`} className="mt-4 border-t pt-4">
      {resolverErrors}
      <AttentionList attention={attention} specTitles={specTitles} />
    </Section>
  )
}
