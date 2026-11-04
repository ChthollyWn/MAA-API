import { useState } from 'react'
import { Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BookMarked, Plus } from 'lucide-react'
import { Page } from '@/components/layout/Page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ListGroup } from '@/components/ui/list'
import { Section } from '@/components/ui/section'
import { EmptyState, ErrorState, LoadingState, Spinner } from '@/components/ui/states'
import { Switch } from '@/components/ui/switch'
import { toast } from '@/components/ui/toaster'
import {
  agentSpecsQuery,
  invalidateSkills,
  setSkillEnabled,
  skillsListQuery,
  type SkillSummary,
  type SpecSummary,
} from './api'
import {
  NEW_SKILL_PATH,
  agentNamesLabel,
  describeSkillError,
  groupSkills,
  skillPath,
  sourceBadge,
} from './format'

const card = 'rounded-xl border bg-card shadow-card'

function SkillRow({
  skill,
  specs,
  busy,
  disabled,
  onToggle,
}: {
  skill: SkillSummary
  specs: readonly SpecSummary[]
  busy: boolean
  disabled: boolean
  onToggle: (enabled: boolean) => void
}) {
  const badge = sourceBadge(skill)
  return (
    <div className="flex items-center gap-1 pr-1.5">
      <Link
        to={skillPath(skill.id)}
        className="flex min-h-14 min-w-0 flex-1 items-center gap-3 py-3 pl-4 pr-1 text-left transition-colors hover:bg-surface-2/60 active:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[0.9375rem] font-medium leading-5">{skill.title}</span>
          <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-footnote text-muted-foreground">
            <code className="truncate font-mono">{skill.id}</code>
            <Badge variant={badge.tone} title={badge.description}>{badge.label}</Badge>
            <span className="truncate">{agentNamesLabel(skill.agents, specs)}</span>
          </span>
        </span>
      </Link>
      {busy ? <Spinner className="text-muted-foreground" /> : null}
      <Switch
        aria-label={`启用「${skill.title}」`}
        checked={skill.enabled}
        disabled={disabled}
        onCheckedChange={onToggle}
        className="mx-2"
      />
    </div>
  )
}

/**
 * 「更多 › 技能」的列表：按来源分成内置、自定义与草稿三组，
 * 每行链到编辑页；开关要把整份生效内容写回，所以先读详情再 `PUT`。
 */
export default function SkillsPage() {
  const queryClient = useQueryClient()
  const skills = useQuery(skillsListQuery)
  const specs = useQuery(agentSpecsQuery)
  const [busyId, setBusyId] = useState<string | null>(null)

  const items = skills.data ?? []
  const specItems = specs.data ?? []

  const toggle = async (skill: SkillSummary, enabled: boolean) => {
    setBusyId(skill.id)
    try {
      await setSkillEnabled(skill.id, enabled)
      toast.success(enabled ? `已启用「${skill.title}」` : `已停用「${skill.title}」`)
    } catch (caught) {
      toast.error(`${enabled ? '启用' : '停用'}「${skill.title}」失败：${describeSkillError(caught)}`)
    } finally {
      setBusyId(null)
      await invalidateSkills(queryClient, skill.id)
    }
  }

  let content
  if (skills.isPending) {
    content = <LoadingState label="正在读取技能…" />
  } else if (skills.isError) {
    content = (
      <div className={card}>
        <ErrorState
          title="技能读取失败"
          message={describeSkillError(skills.error)}
          onRetry={() => void skills.refetch()}
          retrying={skills.isFetching}
        />
      </div>
    )
  } else if (items.length === 0) {
    content = (
      <div className={card}>
        <EmptyState
          icon={BookMarked}
          title="还没有技能"
          description="内置技能随服务发布，也可以自己新建"
        />
      </div>
    )
  } else {
    content = groupSkills(items).map((group) => (
      <ListGroup key={group.key} title={group.label} headingLevel="h3">
        {group.items.map((skill) => (
          <SkillRow
            key={skill.id}
            skill={skill}
            specs={specItems}
            busy={busyId === skill.id}
            disabled={busyId !== null}
            onToggle={(enabled) => void toggle(skill, enabled)}
          />
        ))}
      </ListGroup>
    ))
  }

  return (
    <Page title="技能" back="/more">
      <div className="space-y-4 pb-4">
        <Section
          title="技能"
          hint="Agent 平时只看到技能的名字和说明，用到时才读全文。"
          actions={(
            <Button asChild variant="soft" size="sm">
              <Link to={NEW_SKILL_PATH}><Plus aria-hidden="true" />新建</Link>
            </Button>
          )}
        >
          {content}
        </Section>
      </div>
    </Page>
  )
}
