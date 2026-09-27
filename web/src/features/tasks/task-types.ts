import { useQuery } from '@tanstack/react-query'
import { Dices, Factory, Gift, Joystick, ListChecks, PowerOff, ShoppingBag, Sprout, Sunrise, Swords, UserPlus, type LucideIcon } from 'lucide-react'
import { api } from '@/api/client'
import { toApiError } from '@/api/errors'
import { keys } from '@/api/keys'
import { taskTypeSummary, type TaskTypeSchema } from './form'

export const taskTypesKey = [...keys.tasks.all(), 'catalog'] as const

type TaskTypesEnvelope = { items?: TaskTypeSchema[] }

/** 服务端任务 schema（`GET /api/tasks/types`），编排器与定时任务模板共用。 */
export function useTaskTypes() {
  return useQuery({
    queryKey: taskTypesKey,
    queryFn: async () => {
      const { data, error, response } = await api.GET('/api/tasks/types')
      if (error) throw toApiError(error, response)
      return data as TaskTypesEnvelope
    },
    select: (envelope) => envelope.items ?? [],
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
}

const icons: Record<string, LucideIcon> = {
  StartUp: Sunrise,
  CloseDown: PowerOff,
  Fight: Swords,
  Recruit: UserPlus,
  Infrast: Factory,
  Mall: ShoppingBag,
  Award: Gift,
  Roguelike: Dices,
  Reclamation: Sprout,
  Copilot: Joystick,
}

export function taskTypeIcon(name: string): LucideIcon {
  return icons[name] ?? ListChecks
}

/** 类型的一句说明：去掉开头重复的「中文名：」。 */
export function typeDescription(taskType: TaskTypeSchema): string {
  const summary = taskTypeSummary(taskType)
  const prefix = `${taskType.label}：`
  return summary.startsWith(prefix) ? summary.slice(prefix.length) : summary
}
