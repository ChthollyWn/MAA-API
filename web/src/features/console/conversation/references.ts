/** 用户消息末尾的引用段标题（M72 §4.5.4）；服务端按 @ 提及拼出的资料快照不是用户原话。 */
export const REFERENCE_HEADER = '【引用】'

const BLOCK_MARKER = `\n\n${REFERENCE_HEADER}\n`

/**
 * 把用户消息拆成正文与引用条目（M72 §4.5.4）。
 *
 * 只在最后一次出现的 `"\n\n【引用】\n"` 处切开：正文里出现的「【引用】」字样
 * （比如用户自己引用这句话）不算分隔符。后半段每行以 `"- "` 开头的才是引用条目，
 * 其余行（空行、说明文字）忽略。找不到引用段时正文原样返回。
 */
export function splitReferenceBlock(content: string): { text: string; references: string[] } {
  const index = content.lastIndexOf(BLOCK_MARKER)
  if (index < 0) return { text: content, references: [] }
  const references = content
    .slice(index + BLOCK_MARKER.length)
    .split(/\r?\n/)
    .flatMap((line) => (line.startsWith('- ') ? [line.slice(2)] : []))
  return { text: content.slice(0, index), references }
}
