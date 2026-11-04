import { useQuery } from '@tanstack/react-query'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { ListGroup, ListItem } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { ErrorState, LoadingState } from '@/components/ui/states'
import { specsQuery } from './api'
import { agentTitle, groupSpecs } from './format'

/**
 * 代理定义列表（M72 §4.6.5）：只读，按种类分成总控、Worker、子代理、后台代理四组，
 * 空组不渲染；每行链到定义详情，右侧是定义版本。
 */
export default function AgentsPage() {
  const specs = useQuery(specsQuery)
  const groups = groupSpecs(specs.data ?? [])

  let content
  if (specs.isPending) {
    content = (
      <div className="rounded-xl border bg-card shadow-card">
        <LoadingState label="正在读取代理定义…" />
      </div>
    )
  } else if (specs.error) {
    content = (
      <div className="rounded-xl border bg-card shadow-card">
        <ErrorState
          message={`读取代理定义失败：${toApiError(specs.error).message}`}
          onRetry={() => void specs.refetch()}
          retrying={specs.isFetching}
        />
      </div>
    )
  } else {
    content = (
      <div className="space-y-5 pb-4">
        {groups.map((group) => (
          <ListGroup key={group.kind} title={group.title} headingLevel="h3">
            {group.specs.map((spec) => (
              <ListItem
                key={spec.name}
                title={agentTitle(spec)}
                description={spec.description}
                trailing={`v${spec.version}`}
                to={`/more/agents/${spec.name}`}
              />
            ))}
          </ListGroup>
        ))}
      </div>
    )
  }

  return (
    <Page title="代理定义" back="/more">
      <Section title="代理定义" hint="代理定义随服务版本发布，只能查看。">
        {content}
      </Section>
    </Page>
  )
}
