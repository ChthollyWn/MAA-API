import { Page } from '@/components/layout/Page'
import { GuideMarkdown } from './GuideMarkdown'

/** 接入指南整页显示 docs/14；标题栏返回调试台。 */
export function ApiGuidePage() {
  return (
    <Page title="接入指南" back="/more/api-console" width="default">
      <div className="pb-8">
        <GuideMarkdown />
      </div>
    </Page>
  )
}
