import { PageHeader } from '@/components/layout/PageHeader'
import { DeviceScreenshot } from '@/features/dashboard/DeviceScreenshot'
import { PipelineProgress } from '@/features/dashboard/PipelineProgress'
import { RuntimeStatus } from '@/features/dashboard/RuntimeStatus'
import { useDashboard } from '@/features/dashboard/use-dashboard'

export default function DashboardPage() {
  const dashboard = useDashboard()

  return (
    <>
      <PageHeader title="仪表盘" description="服务、设备与当前任务状态" />
      <div className="space-y-4 py-4">
        <DeviceScreenshot
          connected={dashboard.deviceConnected}
          deviceKnown={dashboard.healthQuery.data?.device != null}
          loading={dashboard.healthQuery.isPending && !dashboard.healthQuery.data}
          pipelineRunning={dashboard.pipelineRunning}
        />
        <RuntimeStatus dashboard={dashboard} />
        <PipelineProgress
          error={dashboard.pipelineQuery.error}
          isPending={dashboard.pipelineQuery.isPending}
          onRetry={() => void dashboard.pipelineQuery.refetch()}
          pipeline={dashboard.pipeline}
        />
      </div>
    </>
  )
}
