import { ImageCard } from './MediaCards'
import type { ImageCardData, ToolAttachment } from './media'

/**
 * 工具调用卡里的截图附件：`get_screenshot` 归档后的结果。
 *
 * 特意复用图片卡（懒加载缩略图、按自然尺寸显示、点开看原图、走 token 取 blob），
 * 所以观感与会话级图片卡一致；区别只在于它挂在工具条 `<details>` 的展开区里——
 * 用户裁决：只有明确的 `send_*` 命令才把图片发到会话中。
 */
export function ToolAttachmentView({ attachment }: { attachment: ToolAttachment }) {
  return (
    <div className="space-y-1" data-testid="tool-attachment">
      <p className="text-xs font-medium text-muted-foreground">附件</p>
      <ImageCard card={cardOf(attachment)} />
    </div>
  )
}

/** 附件比图片卡少一半字段：补齐图片卡需要、附件用不到的那些。 */
function cardOf(attachment: ToolAttachment): ImageCardData {
  return {
    type: 'image',
    screenshotId: attachment.screenshotId,
    // 内容哈希只是解析来源时的中间量，来源已经落在 thumb / full 里。
    sha256: null,
    thumb: attachment.thumb,
    full: attachment.full,
    alt: attachment.alt,
    caption: '',
    width: null,
    height: null,
  }
}
