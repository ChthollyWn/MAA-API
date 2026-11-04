import { Disclosure } from '@/components/ui/disclosure'
import { ORIGIN_LABELS, RISK_LABELS, STRENGTH_LABELS, formatDateTime, type MandateView } from './format'

export interface MandateCardProps {
  mandate: MandateView
}

/** 委托（M72 §4.6.5）：范围、强度、风险上限、发起方与时间；默认折叠。 */
export function MandateCard({ mandate }: MandateCardProps) {
  const strength = STRENGTH_LABELS[mandate.strength] ?? mandate.strength
  const source = ORIGIN_LABELS[mandate.source] ?? mandate.source
  return (
    <Disclosure title="委托" description={strength}>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">
        <dt className="text-muted-foreground">范围</dt>
        <dd className="min-w-0 whitespace-pre-wrap break-words">{mandate.scope}</dd>
        <dt className="text-muted-foreground">强度</dt>
        <dd className="min-w-0">{strength}</dd>
        <dt className="text-muted-foreground">风险上限</dt>
        <dd className="min-w-0">{RISK_LABELS[mandate.riskCeiling] ?? mandate.riskCeiling}</dd>
        <dt className="text-muted-foreground">发起方</dt>
        <dd className="min-w-0 break-words">{mandate.sender ? `${source} · ${mandate.sender}` : source}</dd>
        <dt className="text-muted-foreground">时间</dt>
        <dd className="min-w-0">{formatDateTime(mandate.createdAt)}</dd>
      </dl>
    </Disclosure>
  )
}
