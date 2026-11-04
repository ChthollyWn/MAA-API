import { useMemo, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { toApiError } from '@/api/errors'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { Chip } from '@/components/ui/chip'
import { Disclosure } from '@/components/ui/disclosure'
import { ListGroup, ListItem } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states'
import { roleLabel } from '@/features/models/labels'
import { modelRolesQuery, specDetailQuery, specsQuery } from './api'
import {
  actualModelLabel,
  agentTitle,
  deviceLabel,
  kindLabel,
  riskCeilingLabel,
  toolLabel,
} from './format'

/** 找不到定义时的空态（404）。 */
function MissingAgent() {
  return (
    <Page title="代理定义" back="/more/agents">
      <div className="rounded-xl border bg-card shadow-card">
        <EmptyState
          title="没有这个代理定义"
          description="返回列表重新选择"
          action={(
            <Button asChild variant="secondary" size="sm">
              <Link to="/more/agents">返回代理定义</Link>
            </Button>
          )}
        />
      </div>
    </Page>
  )
}

function Facts({ children }: { children: ReactNode }) {
  return <dl className="divide-y overflow-hidden rounded-xl border bg-card text-sm shadow-card">{children}</dl>
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 px-4 py-3">
      <dt className="w-20 shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 flex-1 break-words font-medium">{children}</dd>
    </div>
  )
}

/**
 * 代理定义详情（M72 §4.6.5）：只读。基本信息、设备与风险、可委派、工具、预加载技能
 * 与提示词正文；技能为空时不渲染整块，提示词默认折叠。
 */
export default function AgentDetailPage() {
  const { name = '' } = useParams<{ name: string }>()
  const detail = useQuery(specDetailQuery(name))
  const specs = useQuery(specsQuery)
  const roles = useQuery(modelRolesQuery)

  const titles = useMemo(
    () => new Map((specs.data ?? []).map((spec) => [spec.name, agentTitle(spec)])),
    [specs.data],
  )

  const failure = detail.error ? toApiError(detail.error) : null

  if (detail.isPending) {
    return (
      <Page title="代理定义" back="/more/agents">
        <div className="rounded-xl border bg-card shadow-card">
          <LoadingState label="正在读取代理定义…" />
        </div>
      </Page>
    )
  }

  if (failure?.status === 404) return <MissingAgent />

  if (failure || !detail.data) {
    return (
      <Page title="代理定义" back="/more/agents">
        <div className="rounded-xl border bg-card shadow-card">
          <ErrorState
            message={`读取代理定义失败：${failure?.message ?? '返回内容为空'}`}
            onRetry={() => void detail.refetch()}
            retrying={detail.isFetching}
          />
        </div>
      </Page>
    )
  }

  const spec = detail.data
  const roleItems = roles.data?.items ?? []

  return (
    <Page title={agentTitle(spec)} back="/more/agents">
      <div className="space-y-6 pb-4">
        <Section title="基本信息">
          <Facts>
            <Fact label="代码名">
              <code className="font-mono text-[0.8125rem]">{spec.name}</code>
            </Fact>
            <Fact label="种类">{kindLabel(spec.kind)}</Fact>
            <Fact label="模型角色">{roleLabel(spec.model_role)}</Fact>
            <Fact label="实际模型">{actualModelLabel(roleItems, spec.model_role)}</Fact>
            <Fact label="版本">v{spec.version}</Fact>
          </Facts>
        </Section>

        <Section title="设备与风险">
          <Facts>
            <Fact label="设备">{deviceLabel(spec.device)}</Fact>
            <Fact label="风险上限">{riskCeilingLabel(spec.risk_ceiling)}</Fact>
          </Facts>
        </Section>

        {spec.delegates.length > 0 ? (
          <Section title="可委派">
            <ListGroup>
              {spec.delegates.map((delegate) => {
                const title = titles.get(delegate)
                return (
                  <ListItem
                    key={delegate}
                    to={`/more/agents/${delegate}`}
                    title={title ?? delegate}
                    description={title ? delegate : undefined}
                  />
                )
              })}
            </ListGroup>
          </Section>
        ) : null}

        <Section title="工具" description={`${spec.tools.length} 个`}>
          <div className="flex flex-wrap gap-2">
            {spec.tools.map((tool) => (
              <Chip key={tool} title={tool} className="cursor-default">
                {toolLabel(tool) ?? tool}
              </Chip>
            ))}
          </div>
        </Section>

        {spec.skills.length > 0 ? (
          <Section title="预加载技能">
            <div className="flex flex-wrap gap-2">
              {spec.skills.map((skill) => (
                <Chip key={skill} title={skill} className="cursor-default">
                  {skill}
                </Chip>
              ))}
            </div>
          </Section>
        ) : null}

        <Disclosure title="提示词正文" defaultOpen={false}>
          <pre className="max-h-[40rem] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-3 font-mono text-xs leading-relaxed">
            {spec.body}
          </pre>
        </Disclosure>
      </div>
    </Page>
  )
}
