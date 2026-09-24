import { ArrowRight, CalendarClock, Settings2, Upload } from 'lucide-react'
import { Link } from 'react-router'
import { PageHeader } from '@/components/layout/PageHeader'
import { Card, CardContent } from '@/components/ui/card'

const groups = [
  {
    title: '运行与维护',
    items: [
      {
        to: '/more/updates',
        title: '热更新',
        description: '检查并管理内核、活动资源与游戏更新。',
        Icon: Upload,
      },
      {
        to: '/more/schedules',
        title: '定时任务',
        description: '按星期与时间配置可重复运行的任务组合。',
        Icon: CalendarClock,
      },
    ],
  },
  {
    title: '管理',
    items: [
      {
        to: '/more/settings',
        title: '设置',
        description: '配置设备、账号默认值、通知通道与服务接入。',
        Icon: Settings2,
      },
    ],
  },
] as const

export default function MorePage() {
  return (
    <div className="mx-auto w-full max-w-3xl space-y-5 pb-6">
      <PageHeader title="更多" description="维护服务与管理常用配置" />
      {groups.map(({ title, items }) => (
        <section key={title} className="space-y-2" aria-labelledby={`more-${title}`}>
          <h2 id={`more-${title}`} className="px-1 text-sm font-semibold text-muted-foreground">{title}</h2>
          <Card>
            <CardContent className="p-0">
              <ul className="divide-y">
                {items.map(({ to, title: itemTitle, description, Icon }) => (
                  <li key={to}>
                    <Link
                      to={to}
                      className="flex min-h-16 items-center gap-3 rounded-xl p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                    >
                      <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-muted text-foreground">
                        <Icon aria-hidden="true" className="size-5" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium">{itemTitle}</span>
                        <span className="mt-1 block text-sm text-muted-foreground">{description}</span>
                      </span>
                      <ArrowRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
                    </Link>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </section>
      ))}
    </div>
  )
}
