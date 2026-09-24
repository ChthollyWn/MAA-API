const root = ['api'] as const

export const keys = {
  all: root,
  system: {
    all: () => [...root, 'system'] as const,
    health: () => [...root, 'system', 'health'] as const,
    logs: (filters?: Readonly<Record<string, unknown>>) =>
      [...root, 'system', 'logs', filters ?? {}] as const,
  },
  device: {
    all: () => [...root, 'device'] as const,
    status: () => [...root, 'device', 'status'] as const,
    candidates: () => [...root, 'device', 'candidates'] as const,
  },
  pipelines: {
    all: () => [...root, 'pipelines'] as const,
    lists: () => [...root, 'pipelines', 'list'] as const,
    list: (filters?: Readonly<Record<string, unknown>>) =>
      [...root, 'pipelines', 'list', filters ?? {}] as const,
    current: () => [...root, 'pipelines', 'current'] as const,
    detail: (pipelineId: string) => [...root, 'pipelines', 'detail', pipelineId] as const,
  },
  queue: {
    all: () => [...root, 'queue'] as const,
  },
  tasks: {
    all: () => [...root, 'tasks'] as const,
    types: () => [...root, 'tasks', 'types'] as const,
    type: (typeName: string) => [...root, 'tasks', 'types', typeName] as const,
  },
  settings: {
    all: () => [...root, 'settings'] as const,
    schema: () => [...root, 'settings', 'schema'] as const,
  },
  updates: {
    all: () => [...root, 'updates'] as const,
    status: () => [...root, 'updates', 'status'] as const,
  },
} as const
