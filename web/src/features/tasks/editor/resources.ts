import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'

export interface ItemCatalogEntry {
  item_id: string
  name: string
  icon_url: string | null
}

export interface CopilotAsset {
  id: string
  name: string
  meta?: { stage_name?: string | null; title?: string | null } | null
}

export const itemCatalogKey = [...keys.all, 'resources', 'items'] as const
export const copilotAssetsKey = [...keys.all, 'resources', 'copilots'] as const

/** Fight.drops 可选的物品目录（MaaCore item_index）。 */
export function useItemCatalog(enabled = true) {
  return useQuery({
    queryKey: itemCatalogKey,
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/resources/items')
      if (error) throw toApiError(error, response)
      return (data ?? []) as ItemCatalogEntry[]
    },
    enabled,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
}

/** 已保存的 Copilot 作业；任务里只保存所选作业的 asset_id。 */
export function useCopilotAssets(enabled = true) {
  return useQuery({
    queryKey: copilotAssetsKey,
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/resources/copilots', {
        params: { query: { page: 1, size: 200 } },
      })
      if (error) throw toApiError(error, response)
      return (data?.items ?? []).map((item): CopilotAsset => ({
        id: item.id,
        name: item.name,
        meta: item.meta,
      }))
    },
    enabled,
    staleTime: 30_000,
    retry: false,
  })
}
