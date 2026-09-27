import { Page } from '@/components/layout/Page'
import { CurrentPipelineCard } from '@/features/dashboard/CurrentPipelineCard'
import { DeviceScreenshot } from '@/features/dashboard/DeviceScreenshot'
import { PendingBanner } from '@/features/dashboard/PendingBanner'
import { QuickActions } from '@/features/dashboard/QuickActions'
import { StatusOverview } from '@/features/dashboard/StatusOverview'
import { useDashboard } from '@/features/dashboard/use-dashboard'

export default function DashboardPage() {
  const dashboard = useDashboard()

  return (
    <Page title="首页" width="wide" hideConfirmationEntry>
      <div className="grid gap-5 pb-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:items-start lg:gap-6">
        <div className="space-y-5">
          <PendingBanner />
          <CurrentPipelineCard
            error={dashboard.pipelineQuery.error}
            isPending={dashboard.pipelineQuery.isPending}
            onRetry={() => void dashboard.pipelineQuery.refetch()}
            pipeline={dashboard.pipeline}
          />
          <QuickActions />
          <StatusOverview dashboard={dashboard} />
        </div>
        <div>
          <DeviceScreenshot
            connected={dashboard.deviceConnected}
            deviceKnown={dashboard.healthQuery.data?.device != null}
            loading={dashboard.healthQuery.isPending && !dashboard.healthQuery.data}
            pipelineRunning={dashboard.pipelineRunning}
          />
        </div>
      </div>
    </Page>
  )
}
