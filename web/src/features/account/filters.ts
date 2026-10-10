import type { OperatorListQuery, OperatorRow } from './api'

/**
 * 干员列表的筛选条件（规格 §7.5）。服务端只提供 `rarity_min` / `elite_min` 这样的
 * 下限参数，所以「稀有度」与「精英化」都按「最低」理解；「是否拥有」用 `owned_only`
 * 表达，默认只看已拥有的（默认隐藏未拥有）。
 */
export type OwnedFilter = 'owned' | 'all' | 'unowned'

export interface OperatorFilters {
  query: string
  owned: OwnedFilter
  /** null＝不限；否则是最低稀有度。 */
  rarityMin: number | null
  /** ''＝不限。 */
  profession: string
  /** null＝不限；否则是最低精英化。 */
  eliteMin: number | null
}

export const DEFAULT_OPERATOR_FILTERS: OperatorFilters = {
  query: '',
  owned: 'owned',
  rarityMin: null,
  profession: '',
  eliteMin: null,
}

export const OPERATOR_PAGE_SIZE = 50

/** 筛选条件 → 请求参数；「未拥有」只能先要全部再由前端过滤（服务端没有 unowned 参数）。 */
export function toOperatorQuery(filters: OperatorFilters, offset: number, limit = OPERATOR_PAGE_SIZE): OperatorListQuery {
  const query: OperatorListQuery = {
    limit,
    offset,
    owned_only: filters.owned === 'owned',
  }
  const text = filters.query.trim()
  if (text) query.query = text
  if (filters.rarityMin !== null) query.rarity_min = filters.rarityMin
  if (filters.profession) query.profession = filters.profession
  if (filters.eliteMin !== null) query.elite_min = filters.eliteMin
  return query
}

/** 查询键：跟着请求参数走，改筛选就换一份缓存。 */
export function operatorQueryKeyPart(filters: OperatorFilters): Record<string, unknown> {
  const { limit: _limit, offset: _offset, ...rest } = toOperatorQuery(filters, 0)
  return rest
}

/** 默认隐藏未拥有；「未拥有」是当前页上的二次过滤。 */
export function matchesOwned(row: OperatorRow, owned: OwnedFilter): boolean {
  if (owned === 'owned') return row.own
  if (owned === 'unowned') return !row.own
  return true
}

export function activeFilterCount(filters: OperatorFilters): number {
  let count = 0
  if (filters.query.trim()) count += 1
  if (filters.owned !== DEFAULT_OPERATOR_FILTERS.owned) count += 1
  if (filters.rarityMin !== null) count += 1
  if (filters.profession) count += 1
  if (filters.eliteMin !== null) count += 1
  return count
}
