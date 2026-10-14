import { useLocation, useNavigate } from 'react-router'
import { Package, Users } from 'lucide-react'
import { Page } from '@/components/layout/Page'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { DepotTab } from './DepotTab'
import { OperatorsTab } from './OperatorsTab'
import { UpgradePlansSection } from './UpgradePlansSection'

export type AccountView = 'operators' | 'depot'

export const ACCOUNT_PATHS: Record<AccountView, string> = {
  operators: '/more/account',
  depot: '/more/account/depot',
}

const options = [
  { value: 'operators', label: '干员', icon: Users },
  { value: 'depot', label: '仓库', icon: Package },
] as const satisfies readonly { value: AccountView; label: string; icon: typeof Users }[]

/** 路径 → 标签页；`/more/account/operators` 也当作干员页。 */
export function accountView(pathname: string): AccountView {
  return pathname.replace(/\/+$/, '') === ACCOUNT_PATHS.depot ? 'depot' : 'operators'
}

/** 「干员」「仓库」两个标签页的分段导航；切换写入浏览器历史，可前进后退。 */
export function AccountAreaNav({ value }: { value: AccountView }) {
  const navigate = useNavigate()
  return (
    <SegmentedControl
      ariaLabel="账号数据视图"
      value={value}
      onValueChange={(next) => navigate(ACCOUNT_PATHS[next])}
      options={options}
      fullWidth
      className="sm:max-w-xs"
    />
  )
}

/** 账号数据页：`/more/account` 是干员，`/more/account/depot` 是仓库（规格 §7.5、§7.7）。 */
export default function AccountPage() {
  const location = useLocation()
  const view = accountView(location.pathname)
  return (
    <Page title="账号数据" back="/more" width="wide" toolbar={<AccountAreaNav value={view} />}>
      <div className="space-y-6 pb-4">
        {view === 'depot' ? <DepotTab /> : <OperatorsTab />}
        {/* 养成计划与当前标签页无关，两个标签页下都显示（规格 §7.7）。 */}
        <div className="border-t pt-6">
          <UpgradePlansSection />
        </div>
      </div>
    </Page>
  )
}
