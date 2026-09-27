import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// index.css 的 @theme 里自定义的字号、阴影与动画。不注册的话，tailwind-merge 会把
// `text-footnote` 当成文字颜色，和 `text-muted-foreground` 合并时把字号吞掉。
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ['footnote', 'body', 'headline', 'title'],
      shadow: ['card', 'float'],
      animate: ['fade-in', 'fade-out', 'sheet-in', 'sheet-out', 'panel-in', 'panel-out', 'dialog-in', 'dialog-out'],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
