import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { SearchX } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { InlineAlert } from '@/components/ui/inline-alert'
import { Input } from '@/components/ui/input'
import { Section } from '@/components/ui/section'
import { EmptyState, LoadingState } from '@/components/ui/states'
import { errorText } from '@/features/settings/format'
import {
  retrievalSearchQuery,
  type RetrievalHit,
  type RetrievalResults,
  type RetrievalSearchParams,
} from './retrieval-api'

const SEARCH_HINT = '用和 Agent 相同的方式检索，看看能查到什么。'

const VECTOR_LABELS: Record<RetrievalResults['vector'], string> = {
  used: '已用',
  disabled: '未启用',
  unavailable: '不可用',
  empty: '还没就绪',
}

function HitRow({ hit }: { hit: RetrievalHit }) {
  const external = typeof hit.url === 'string' && hit.url.startsWith('https://')
  return (
    <li className="space-y-1.5 p-4">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="min-w-0 break-words text-[0.9375rem] font-medium">{hit.title}</span>
        <Badge
          variant="neutral"
          title={hit.trusted ? '可信来源，内容可以当事实使用。' : '外部来源，内容只当参考。'}
        >
          {hit.source_title}
        </Badge>
      </div>
      {hit.heading ? <p className="break-words text-footnote text-muted-foreground">{hit.heading}</p> : null}
      <p className="line-clamp-3 break-words text-sm">{hit.snippet}</p>
      <div className="flex flex-wrap items-center justify-between gap-2 text-footnote text-muted-foreground">
        <span className="tabular-nums">分数 {hit.score.toFixed(4)}</span>
        {external ? (
          <a
            href={hit.url ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-primary-text underline-offset-4 hover:underline"
          >
            打开原文
          </a>
        ) : null}
      </div>
    </li>
  )
}

/** 「知识库 › 检索来源」底部的测试检索：只在点「检索」时发请求。 */
export function RetrievalSearch() {
  const [value, setValue] = useState('')
  const [submitted, setSubmitted] = useState<RetrievalSearchParams | null>(null)
  const search = useQuery(retrievalSearchQuery(submitted))
  const fetchedRef = useRef<string | null>(null)

  // 参数变化后 useQuery 拿到的是新的（enabled: false）查询；渲染完再手动发一次。
  // 同一个提交值只发一次，StrictMode 重新执行 effect 时不会重复请求。
  useEffect(() => {
    if (!submitted) return
    const signature = JSON.stringify(submitted)
    if (fetchedRef.current === signature) return
    fetchedRef.current = signature
    void search.refetch()
  }, [submitted, search])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const q = value.trim()
    if (!q) return
    if (submitted?.q === q) {
      void search.refetch()
      return
    }
    setSubmitted({ q })
  }

  const result = search.data
  return (
    <Section title="测试检索" hint={SEARCH_HINT}>
      <form onSubmit={submit}>
        <Field label="检索词" htmlFor="retrieval-search-q">
          <div className="flex items-start gap-2">
            <Input
              id="retrieval-search-q"
              value={value}
              placeholder="例如：怎么刷固源岩"
              onChange={(event) => setValue(event.target.value)}
            />
            <Button type="submit" className="shrink-0" disabled={!value.trim()} loading={search.isFetching}>
              检索
            </Button>
          </div>
        </Field>
      </form>
      {search.isError ? <InlineAlert tone="danger">检索失败：{errorText(search.error)}</InlineAlert> : null}
      {result ? (
        <div className="space-y-2">
          <p className="px-1 text-footnote text-muted-foreground">
            共 {result.hits.length} 条 · {result.took_ms} 毫秒 · 向量{VECTOR_LABELS[result.vector]}
          </p>
          {result.hits.length === 0 ? (
            <div className="rounded-xl border bg-card shadow-card">
              <EmptyState icon={SearchX} title="还没有匹配的片段" description="换个说法，或等索引建好再试" />
            </div>
          ) : (
            <ul aria-label="检索结果" className="divide-y overflow-hidden rounded-xl border bg-card shadow-card">
              {result.hits.map((hit) => (
                <HitRow key={hit.chunk_id} hit={hit} />
              ))}
            </ul>
          )}
        </div>
      ) : search.isFetching ? (
        <div className="rounded-xl border bg-card shadow-card">
          <LoadingState label="正在检索…" />
        </div>
      ) : null}
    </Section>
  )
}
