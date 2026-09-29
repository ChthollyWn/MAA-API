import { Link, useParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { SearchX } from 'lucide-react'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { ListGroup, ListItem } from '@/components/ui/list'
import { EmptyState } from '@/components/ui/states'
import { useAuth } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { AgentCapabilityCheck } from './AgentCapabilityCheck'
import { KnowledgeSection } from './knowledge/KnowledgeSection'
import { AccessSection, AppearanceSection, themeLabel } from './LocalSections'
import { NotificationChannels } from './NotificationChannels'
import { PushNotifications } from './PushNotifications'
import { SchemaSection } from './SchemaSection'
import {
  SETTINGS_INDEX_PATH,
  SETTINGS_SECTIONS,
  findSection,
  hasUnmappedGroups,
  sectionPath,
  type SettingsSection,
  type SettingsSectionId,
} from './sections'
import { settingsQueryOptions } from './use-settings'

function SettingsIndex() {
  const settings = useQuery(settingsQueryOptions)
  const theme = useUiStore((state) => state.theme)
  const token = useAuth((state) => state.token)
  const showOther = settings.data ? hasUnmappedGroups(settings.data.fields) : false
  const trailing: Partial<Record<SettingsSectionId, string>> = {
    appearance: themeLabel(theme),
    access: token ? '已保存' : '未保存',
  }
  const row = ({ id, title, summary, icon }: SettingsSection) => (
    <ListItem key={id} to={sectionPath(id)} icon={icon} iconTone="primary" title={title} description={summary} trailing={trailing[id]} />
  )
  return (
    <Page title="设置" width="narrow">
      <div className="space-y-6 pb-4">
        <ListGroup title="此设备" footer="只影响当前浏览器。">
          {SETTINGS_SECTIONS.filter((section) => section.scope === 'local').map(row)}
        </ListGroup>
        <ListGroup title="服务" footer="保存在服务端，对所有连接这台 MAA-API 的设备生效。">
          {SETTINGS_SECTIONS.filter((section) => section.scope === 'server' && (section.id !== 'other' || showOther)).map(row)}
        </ListGroup>
      </div>
    </Page>
  )
}

function UnknownSection() {
  return (
    <Page title="设置" back={SETTINGS_INDEX_PATH} width="narrow">
      <EmptyState
        icon={SearchX}
        title="找不到这个设置分区"
        description="链接可能有误，或者这个分区已经不存在。"
        action={<Button asChild variant="outline"><Link to={SETTINGS_INDEX_PATH}>返回设置</Link></Button>}
      />
    </Page>
  )
}

function SectionPage({ section }: { section: SettingsSection }) {
  switch (section.id) {
    case 'appearance':
    case 'access':
    case 'notifications':
      return (
        <Page title={section.title} back={SETTINGS_INDEX_PATH} width="narrow">
          {section.id === 'appearance' ? <AppearanceSection /> : null}
          {section.id === 'access' ? <AccessSection /> : null}
          {section.id === 'notifications' ? (
            <div className="space-y-8 pb-4">
              <PushNotifications />
              <NotificationChannels />
            </div>
          ) : null}
        </Page>
      )
    case 'agent':
      return <SchemaSection section={section} intro={<AgentCapabilityCheck />} />
    case 'knowledge':
      // 知识库不是设置项：它有自己的列表与整页编辑器，不走设置 schema。
      return (
        <Page title={section.title} back={SETTINGS_INDEX_PATH} width="narrow">
          <KnowledgeSection />
        </Page>
      )
    default:
      return <SchemaSection section={section} />
  }
}

/** `/more/settings` 显示分区索引，`/more/settings/:section` 显示对应分区。 */
export default function SettingsPage() {
  const { section } = useParams()
  if (section === undefined) return <SettingsIndex />
  const meta = findSection(section)
  if (!meta) return <UnknownSection />
  // 分区之间切换时路由元素不会重建；按分区重置草稿与拦截状态。
  return <SectionPage key={meta.id} section={meta} />
}
