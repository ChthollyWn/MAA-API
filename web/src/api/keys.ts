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
    /** Worker 当前未结束的计划（M75 §4.6）：只随该会话的 `plan_updated` 失效。 */
    plan: (sessionId: string) =>
      [...root, 'agent', 'plan', sessionId] as const,
    /** 一个工单的产物列表（M75 §4.6）。 */
    artifacts: (ticketId: string) =>
      [...root, 'agent', 'artifacts', 'ticket', ticketId] as const,
    /** 产物列表的公共前缀；`artifact_created` 载荷没有 ticket_id，只能整片失效。 */
    artifactsAll: () => [...root, 'agent', 'artifacts'] as const,
    /** 单个产物的详情正文（拉开抽屉时才读）。 */
    artifact: (artifactId: string) =>
      [...root, 'agent', 'artifact', artifactId] as const,
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
    /** 长期记忆（M79 §4.6）：整片前缀，`memory_proposed` 一次失效本页与待审核清单。 */
    memories: () => [...root, 'agent', 'memories'] as const,
    /** 记忆页的完整列表（不传 status，客户端分三组）。 */
    memoryList: () => [...root, 'agent', 'memories', 'list'] as const,
    /** 指挥台「待你处理」里的待审核记忆。 */
    attentionMemories: () => [...root, 'agent', 'memories', 'pending'] as const,
    /** @ 提及的干员候选；query 进 key，防抖后的不同查询各有一份缓存。 */
    mentionCandidates: (type: string, query: string) =>
      [...root, 'agent', 'mentions', type, query] as const,
    /** 事件规则（M81 §4.6）：内置规则、覆盖与用户规则合成后的列表。 */
    triggerRules: () => [...root, 'agent', 'trigger-rules'] as const,
    /** 观察记录列表；带 `reviewed` 筛选，`observation_created` 走下面的整片前缀。 */
    observations: (f?: Readonly<Record<string, unknown>>) =>
      [...root, 'agent', 'observations', f ?? {}] as const,
    /** 观察记录的整片前缀：广播载荷不带 `reviewed`，一次失效待审阅与已审阅两份缓存。 */
    observationsAll: () => [...root, 'agent', 'observations'] as const,
    /** 事件记录列表；过滤条件进 key。 */
    triggers: (f?: Readonly<Record<string, unknown>>) =>
      [...root, 'agent', 'triggers', f ?? {}] as const,
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
