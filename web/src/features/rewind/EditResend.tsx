import { useId, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { splitReferenceBlock } from '@/features/console/conversation/references'

export interface EditResendProps {
  /** 人类消息原文；预填时去掉末尾的「【引用】」段。 */
  initialText: string
  /** 正在回溯：两个按钮都禁用。 */
  pending?: boolean
  onCancel: () => void
  /** 返回 false 表示没成功，编辑框保持打开。 */
  onSubmit: (text: string) => Promise<boolean> | boolean | void
}

/**
 * 编辑后重发（M80 §4.6）：把人类消息的气泡换成编辑框，预填正文（去掉引用段），
 * 按钮「重发」「取消」。
 */
export function EditResend({ initialText, pending = false, onCancel, onSubmit }: EditResendProps) {
  const id = useId()
  const [text, setText] = useState(() => splitReferenceBlock(initialText).text)

  return (
    <div className="flex justify-end pl-8 sm:pl-16">
      <div className="w-full max-w-[42rem] space-y-2 rounded-2xl rounded-br-md border bg-card p-3">
        <label htmlFor={id} className="sr-only">编辑消息</label>
        <Textarea
          id={id}
          autoFocus
          value={text}
          aria-label="编辑消息"
          onChange={(event) => setText(event.target.value)}
        />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" size="sm" disabled={pending} onClick={onCancel}>
            取消
          </Button>
          <Button
            type="button"
            size="sm"
            loading={pending}
            disabled={!text.trim()}
            onClick={() => void onSubmit(text)}
          >
            重发
          </Button>
        </div>
      </div>
    </div>
  )
}
