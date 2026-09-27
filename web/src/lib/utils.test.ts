import { describe, expect, it } from 'vitest'
import { cn } from '@/lib/utils'

describe('cn', () => {
  it('keeps custom font sizes next to text colors', () => {
    expect(cn('text-footnote text-muted-foreground')).toBe('text-footnote text-muted-foreground')
    expect(cn('text-headline font-semibold', 'text-foreground')).toBe('text-headline font-semibold text-foreground')
  })

  it('still resolves conflicts inside the font size and color groups', () => {
    expect(cn('text-footnote', 'text-sm')).toBe('text-sm')
    expect(cn('text-muted-foreground', 'text-danger')).toBe('text-danger')
  })

  it('treats the custom shadows as shadow sizes', () => {
    expect(cn('shadow-card', 'shadow-float')).toBe('shadow-float')
    expect(cn('shadow-card text-primary-text')).toBe('shadow-card text-primary-text')
  })
})
