import { Link } from 'react-router'
import { CircleHelp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function NotFoundPage() {
  return (
    <div className="grid min-h-[50dvh] place-items-center py-10">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CircleHelp aria-hidden="true" className="mb-2 size-7 text-muted-foreground" />
          <CardTitle>找不到这个页面</CardTitle>
          <CardDescription>地址可能已更改，或页面尚未开放。</CardDescription>
        </CardHeader>
        <CardContent><Button asChild><Link to="/">返回首页</Link></Button></CardContent>
      </Card>
    </div>
  )
}
