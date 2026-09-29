import { Bell, BookOpen, Bot, KeyRound, Palette, Server, SlidersHorizontal, Smartphone, Wrench, type LucideIcon } from 'lucide-react'
import type { SettingField } from './schema'

export type SettingsSectionId = 'appearance' | 'access' | 'device' | 'agent' | 'knowledge' | 'notifications' | 'maintenance' | 'system' | 'other'

export interface SettingsSection {
  id: SettingsSectionId
  title: string
  summary: string
  icon: LucideIcon
  /** local 只影响当前浏览器；server 保存在服务端。 */
  scope: 'local' | 'server'
}

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { id: 'appearance', title: '外观', summary: '浅色、深色或跟随系统', icon: Palette, scope: 'local' },
  { id: 'access', title: '访问令牌', summary: '替换或清除保存的 token', icon: KeyRound, scope: 'local' },
  { id: 'device', title: '设备与渠道', summary: '设备地址、连接方式与默认渠道', icon: Smartphone, scope: 'server' },
  { id: 'agent', title: 'LLM 与 Agent', summary: '模型接口、确认时限与自动诊断', icon: Bot, scope: 'server' },
  { id: 'knowledge', title: 'Agent 知识库', summary: '操作剧本、游戏知识与用户档案', icon: BookOpen, scope: 'server' },
  { id: 'notifications', title: '通知', summary: 'Web 推送与通知通道', icon: Bell, scope: 'server' },
  { id: 'maintenance', title: '更新与日志', summary: '更新检查、下载前缀与日志级别', icon: Wrench, scope: 'server' },
  { id: 'system', title: '应用与部署', summary: '代理、内核路径与反向代理', icon: Server, scope: 'server' },
  { id: 'other', title: '其他', summary: '尚未归类的设置项', icon: SlidersHorizontal, scope: 'server' },
]

export const SETTINGS_INDEX_PATH = '/more/settings'

export function sectionPath(id: SettingsSectionId): string {
  return `${SETTINGS_INDEX_PATH}/${id}`
}

const GROUP_SECTIONS = new Map<string, SettingsSectionId>([
  ['设备', 'device'],
  ['渠道', 'device'],
  ['LLM', 'agent'],
  ['Agent', 'agent'],
  ['更新', 'maintenance'],
  ['日志', 'maintenance'],
  ['应用', 'system'],
  ['部署', 'system'],
])

export function sectionForGroup(group: string): SettingsSectionId {
  return GROUP_SECTIONS.get(group) ?? 'other'
}

export function findSection(id: string | undefined): SettingsSection | undefined {
  return SETTINGS_SECTIONS.find((section) => section.id === id)
}

export function fieldsForSection(fields: readonly SettingField[], id: SettingsSectionId): SettingField[] {
  return fields.filter((field) => sectionForGroup(field.group) === id)
}

export interface FieldGroup {
  group: string
  fields: SettingField[]
}

/** 分区内按原 group 分小节，小节与字段都保持 schema 顺序。 */
export function groupFields(fields: readonly SettingField[]): FieldGroup[] {
  const groups = new Map<string, SettingField[]>()
  for (const field of fields) {
    const list = groups.get(field.group) ?? []
    list.push(field)
    groups.set(field.group, list)
  }
  return [...groups].map(([group, list]) => ({ group, fields: list }))
}

export function hasUnmappedGroups(fields: readonly SettingField[]): boolean {
  return fields.some((field) => sectionForGroup(field.group) === 'other')
}
