import { ModelRolesCard } from './ModelRolesCard'
import { ProfileList } from './ProfileList'

/** 「设置 › 模型档案」：上面是角色到档案的绑定，下面是档案本身。 */
export function ModelsSection() {
  return (
    <div className="space-y-8 pb-4">
      <ModelRolesCard />
      <ProfileList />
    </div>
  )
}
