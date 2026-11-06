/**
 * 计划状态与步骤状态的中文与色调（M75 §4.6）。
 *
 * 工单页的计划面板与指挥台「待你处理」里的计划卡共用这一份；未知取值原样显示，
 * 避免服务端新增枚举时把值写丢。
 */
export type PlanTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

/** 计划状态的中文（M75 §4.6）。 */
export const PLAN_STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  awaiting_approval: '待批准',
  active: '进行中',
  done: '已完成',
  abandoned: '已放弃',
}

/** 步骤状态的中文（M75 §4.6）。 */
export const PLAN_ITEM_STATUS_LABELS: Record<string, string> = {
  pending: '待开始',
  in_progress: '进行中',
  done: '已完成',
  cancelled: '已取消',
}

export const PLAN_STATUS_TONES: Record<string, PlanTone> = {
  draft: 'neutral',
  awaiting_approval: 'warning',
  active: 'primary',
  done: 'success',
  abandoned: 'neutral',
}

export const ITEM_STATUS_TONES: Record<string, PlanTone> = {
  pending: 'neutral',
  in_progress: 'primary',
  done: 'success',
  cancelled: 'neutral',
}
