import {
  Bot,
  Boxes,
  ClipboardList,
  CodeXml,
  Download,
  House,
  LayoutGrid,
  ListChecks,
  ScrollText,
  Settings2,
  Swords,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  /** 只在路径完全相同时高亮（首页）。 */
  end?: boolean
}

/** 手机底部 Tab 与桌面侧边栏共用的主入口。 */
export const primaryNav: readonly NavItem[] = [
  { to: '/', label: '首页', icon: House, end: true },
  { to: '/tasks', label: '任务', icon: ListChecks },
  { to: '/logs', label: '日志', icon: ScrollText },
  { to: '/agent', label: 'Agent', icon: Bot },
  { to: '/more', label: '更多', icon: LayoutGrid },
]

/** 「更多」里的次级入口；桌面侧边栏直接展开显示。 */
export const secondaryNav: readonly (NavItem & { description: string })[] = [
  { to: '/more/updates', label: '热更新', icon: Download, description: '内核、活动资源与游戏版本' },
  { to: '/more/account', label: '账号数据', icon: Boxes, description: '森空岛练度同步、仓库识别与养成计划' },
  { to: '/more/copilots', label: '作业', icon: Swords, description: '作业来源、版本链与每次尝试的结果' },
  { to: '/more/audit', label: 'Agent 审计', icon: ClipboardList, description: '工具调用、确认结果与流水线' },
  { to: '/more/api-console', label: 'API 调试台', icon: CodeXml, description: '选接口、发请求、看日志' },
  { to: '/more/settings', label: '设置', icon: Settings2, description: '设备、客户端渠道、LLM、通知与服务接入' },
]
