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
  agent: {
    all: () => [...root, 'agent'] as const,
    sessions: (filters?: Readonly<Record<string, unknown>>) =>
      [...root, 'agent', 'sessions', filters ?? {}] as const,
    session: (sessionId: string) =>
      [...root, 'agent', 'sessions', 'detail', sessionId] as const,
    messages: (sessionId: string) =>
      [...root, 'agent', 'sessions', sessionId, 'messages'] as const,
    /** 压缩检查点（M74 §4.6）：只随 `compaction` / `compaction_created` 失效。 */
    checkpoints: (sessionId: string) =>
      [...root, 'agent', 'checkpoints', sessionId] as const,
    capabilities: () => [...root, 'agent', 'capabilities'] as const,
    tools: () => [...root, 'agent', 'tools'] as const,
    audits: () => [...root, 'agent', 'audits'] as const,
    auditList: (filters?: Readonly<Record<string, unknown>>) =>
      [...root, 'agent', 'audits', filters ?? {}] as const,
    auditDetail: (id: number) => [...root, 'agent', 'audits', 'detail', id] as const,
    modelProfiles: () => [...root, 'agent', 'model-profiles'] as const,
    modelRoles: () => [...root, 'agent', 'model-roles'] as const,
    specs: {
      all: () => [...root, 'agent', 'specs'] as const,
      detail: (name: string) => [...root, 'agent', 'specs', 'detail', name] as const,
    },
    runs: {
      all: () => [...root, 'agent', 'runs'] as const,
      list: (filters?: Readonly<Record<string, unknown>>) =>
        [...root, 'agent', 'runs', 'list', filters ?? {}] as const,
      detail: (runId: string) => [...root, 'agent', 'runs', 'detail', runId] as const,
      spans: (runId: string) => [...root, 'agent', 'runs', 'detail', runId, 'spans'] as const,
    },
    costs: (filters?: Readonly<Record<string, unknown>>) =>
      [...root, 'agent', 'costs', filters ?? {}] as const,
    confirmations: () => [...root, 'confirmations'] as const,
    pendingConfirmations: () => [...root, 'confirmations', 'pending'] as const,
    /** 本会话待答的提问（Agent 主动提问卡）；按会话再分一段，便于整片失效。 */
    pendingQuestions: () => [...root, 'agent', 'questions', 'pending'] as const,
    /** 指挥台上「待你处理」的全局待答提问。 */
    attentionQuestions: () => [...root, 'agent', 'questions', 'pending', 'all'] as const,
    /** @ 提及的干员候选；query 进 key，防抖后的不同查询各有一份缓存。 */
    mentionCandidates: (type: string, query: string) =>
      [...root, 'agent', 'mentions', type, query] as const,
    orchestrator: {
      all: () => [...root, 'agent', 'orchestrator'] as const,
      state: () => [...root, 'agent', 'orchestrator', 'state'] as const,
      messages: () => [...root, 'agent', 'orchestrator', 'messages'] as const,
    },
    tickets: {
      all: () => [...root, 'agent', 'tickets'] as const,
      lists: () => [...root, 'agent', 'tickets', 'list'] as const,
      list: (filters: Readonly<Record<string, unknown>>) =>
        [...root, 'agent', 'tickets', 'list', filters] as const,
      scope: (ticketId: string) =>
        [...root, 'agent', 'tickets', 'detail', ticketId] as const,
      detail: (ticketId: string) =>
        [...root, 'agent', 'tickets', 'detail', ticketId, 'summary'] as const,
      reports: (ticketId: string) =>
        [...root, 'agent', 'tickets', 'detail', ticketId, 'reports'] as const,
      messagesAll: (ticketId: string) =>
        [...root, 'agent', 'tickets', 'detail', ticketId, 'messages'] as const,
      messages: (ticketId: string, includeSubagents: boolean) =>
        [...root, 'agent', 'tickets', 'detail', ticketId, 'messages', includeSubagents] as const,
      runs: (ticketId: string) =>
        [...root, 'agent', 'tickets', 'detail', ticketId, 'runs'] as const,
    },
    deviceLease: () => [...root, 'agent', 'device-lease'] as const,
  },
} as const
