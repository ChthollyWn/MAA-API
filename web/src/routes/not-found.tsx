import { Link } from 'react-router'
import { Compass } from 'lucide-react'
import { Page } from '@/components/layout/Page'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/states'

export default function NotFoundPage() {
  return (
    <Page title="页面不存在" width="narrow">
      <EmptyState
        icon={Compass}
        title="找不到这个页面"
        description="地址可能已更改，或页面尚未开放。"
        action={<Button asChild><Link to="/">返回首页</Link></Button>}
        className="py-16"
      />
    </Page>
  )
}
