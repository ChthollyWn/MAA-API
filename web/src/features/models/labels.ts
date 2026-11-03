/** 九个模型角色的中文名；顺序与后端 ModelRole 一致（规格 §4.6）。 */
export const ROLE_LABELS: Record<string, string> = {
  orchestrator: '总控',
  worker: 'Worker',
  subagent: '子代理',
  summarizer: '摘要',
  title: '标题',
  trigger_eval: '事件研判',
  memory: '记忆整理',
  vision: '视觉',
  embedding: '向量',
}

/** 服务端新增角色时保留原英文名，不把未知枚举摆成空白。 */
export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
}
