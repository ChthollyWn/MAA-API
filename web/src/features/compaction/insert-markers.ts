import type { CheckpointView } from './api'

/** `insertMarkers` 渲染项的键前缀，保证消息与标记在同一次渲染里唯一。 */
export type MarkerItem<TMessage> =
  | { kind: 'message'; key: string; message: TMessage }
  | { kind: 'checkpoint'; key: string; checkpoint: CheckpointView }

/**
 * 把压缩检查点摆进消息序列（M74 §4.6）：每个 `status != 'invalidated'` 的检查点
 * 放在 `seq == upto_seq` 的消息之后；对应消息不在当前窗口时放在窗口最前面。
 *
 * 检查点按 `upto_seq` 升序摆放，同一位置的多个标记保持这个顺序（旧摘要在前）。
 */
export function insertMarkers<TMessage extends { seq: number }>(
  messages: readonly TMessage[],
  checkpoints: readonly CheckpointView[],
): MarkerItem<TMessage>[] {
  const visible = checkpoints
    .filter((checkpoint) => checkpoint.status !== 'invalidated')
    .slice()
    .sort((left, right) => left.upto_seq - right.upto_seq)
  const sequence = new Set(messages.map((message) => message.seq))
  const leading = visible.filter((checkpoint) => !sequence.has(checkpoint.upto_seq))
  const trailing = new Map<number, CheckpointView[]>()
  for (const checkpoint of visible) {
    if (!sequence.has(checkpoint.upto_seq)) continue
    const group = trailing.get(checkpoint.upto_seq)
    if (group) group.push(checkpoint)
    else trailing.set(checkpoint.upto_seq, [checkpoint])
  }

  const items: MarkerItem<TMessage>[] = leading.map((checkpoint) => ({
    kind: 'checkpoint',
    key: `checkpoint-${checkpoint.id}`,
    checkpoint,
  }))
  messages.forEach((message, index) => {
    items.push({ kind: 'message', key: `message-${index}`, message })
    for (const checkpoint of trailing.get(message.seq) ?? []) {
      items.push({ kind: 'checkpoint', key: `checkpoint-${checkpoint.id}`, checkpoint })
    }
  })
  return items
}
