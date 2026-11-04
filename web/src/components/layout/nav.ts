import {
  Activity,
  BookMarked,
  Bot,
  Boxes,
  Brain,
  ClipboardList,
  Coins,
  CodeXml,
  Download,
  History,
  LayoutGrid,
  ListChecks,
  Network,
  ScrollText,
  Settings2,
  Swords,
  Ticket,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  /** 只在路径完全相同时高亮（首页）。 */
  end?: boolean
}

/** 次级入口的分组；桌面侧边栏与「更多」页都按这个顺序渲染。 */
export type NavGroup = 'agent' | 'ops' | 'system'

export const NAV_GROUP_TITLES: Record<NavGroup, string> = { agent: 'Agent', ops: '运维', system: '系统' }

/** 手机底部 Tab 与桌面侧边栏共用的主入口。 */
export const primaryNav: readonly NavItem[] = [
  { to: '/', label: '指挥台', icon: Bot, end: true },
  { to: '/tickets', label: '工单', icon: Ticket },
  { to: '/tasks', label: '任务', icon: ListChecks },
  { to: '/logs', label: '日志', icon: ScrollText },
  { to: '/more', label: '更多', icon: LayoutGrid },
]

/** 「更多」里的次级入口；桌面侧边栏按组分开展示。 */
export const secondaryNav: readonly (NavItem & { description: string; group: NavGroup })[] = [
  { to: '/more/agents', label: '代理定义', icon: Network, description: '各代理的提示词、工具、模型与风险上限', group: 'agent' },
  { to: '/more/sessions', label: '旧会话', icon: History, description: '指挥台上线前的会话与归档的总控会话', group: 'agent' },
  { to: '/more/runs', label: '运行追踪', icon: Activity, description: '每次代理调用的步骤、耗时与用量', group: 'agent' },
  { to: '/more/costs', label: '成本', icon: Coins, description: '按天、代理、模型统计模型费用', group: 'agent' },
  { to: '/more/status', label: '运行状态', icon: Activity, description: '设备、内核、队列与设备画面', group: 'ops' },
  { to: '/more/updates', label: '热更新', icon: Download, description: '内核、活动资源与游戏版本', group: 'ops' },
  { to: '/more/account', label: '账号数据', icon: Boxes, description: '森空岛练度同步、仓库识别与养成计划', group: 'ops' },
  { to: '/more/copilots', label: '作业', icon: Swords, description: '作业来源、版本链与每次尝试的结果', group: 'ops' },
  { to: '/more/skills', label: '技能', icon: BookMarked, description: 'Agent 按需加载的做事方法', group: 'ops' },
  { to: '/more/memories', label: '记忆', icon: Brain, description: 'Agent 从对话里记住的事实、经历与做法', group: 'ops' },
  { to: '/more/audit', label: 'Agent 审计', icon: ClipboardList, description: '工具调用、确认结果与流水线', group: 'ops' },
  { to: '/more/api-console', label: 'API 调试台', icon: CodeXml, description: '选接口、发请求、看日志', group: 'ops' },
  { to: '/more/settings', label: '设置', icon: Settings2, description: '设备、客户端渠道、LLM、通知与服务接入', group: 'system' },
]
