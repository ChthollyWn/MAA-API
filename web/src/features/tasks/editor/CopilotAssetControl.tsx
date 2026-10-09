import { toApiError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { InlineAlert } from '@/components/ui/inline-alert'
import { NativeSelect } from '@/components/ui/native-select'
import { UNSET } from '../form'
import { ariaProps, type ControlProps } from './control-props'
import { useCopilotAssets } from './resources'

/** Copilot 作业：从已保存的作业里选一个，任务只保存它的 asset_id。 */
export function CopilotAssetControl({ id, value, disabled, invalid, describedBy, onChange }: ControlProps) {
  const assets = useCopilotAssets()
  const list = assets.data ?? []
  const loading = assets.isPending && assets.isFetching
  const current = typeof value === 'string' ? value : ''
  const unlisted = current !== '' && assets.isSuccess && !list.some((asset) => asset.id === current)
  const errorText = assets.error ? toApiError(assets.error).message : null

  return (
    <div className="space-y-2">
      <NativeSelect
        id={id}
        value={current}
        disabled={disabled || loading || (list.length === 0 && !unlisted)}
        {...ariaProps({ invalid, describedBy })}
        onChange={(event) => onChange(event.target.value || UNSET)}
      >
        <option value="">{loading ? '正在读取已保存作业…' : list.length ? '选择已保存的 Copilot 作业' : '还没有已保存的 Copilot 作业'}</option>
        {unlisted ? <option value={current}>{`作业 ${current}（已不在列表中）`}</option> : null}
        {list.map((asset) => {
          const stage = asset.meta?.stage_name
          return <option key={asset.id} value={asset.id}>{asset.name}{stage ? ` · ${stage}` : ''}</option>
        })}
      </NativeSelect>
      {errorText ? (
        <InlineAlert
          tone="danger"
          action={<Button type="button" size="sm" variant="outline" loading={assets.isFetching} onClick={() => void assets.refetch()}>重试</Button>}
        >
          读取 Copilot 作业失败：{errorText}
        </InlineAlert>
      ) : list.length === 0 && !loading ? (
        <p className="text-footnote text-muted-foreground">先在 Agent 会话中保存一个 Copilot 作业</p>
      ) : null}
    </div>
  )
}
