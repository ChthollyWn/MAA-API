import { afterEach, describe, expect, it } from 'vitest'
import { useComposerStore } from './store'

const fight = { id: 'a', name: 'Fight', params: { stage: '1-7', medicine: 2 } }
const award = { id: 'b', name: 'Award', params: {} }

afterEach(() => {
  useComposerStore.getState().reset()
  localStorage.clear()
})

describe('composer draft store', () => {
  it('reuses one idempotency key until the composition changes', () => {
    const store = useComposerStore.getState()
    store.setDrafts([fight])
    const first = useComposerStore.getState().takeIdempotencyKey()
    expect(first).toBeTruthy()
    expect(useComposerStore.getState().takeIdempotencyKey()).toBe(first)

    useComposerStore.getState().setDrafts([fight, award])
    const second = useComposerStore.getState().takeIdempotencyKey()
    expect(second).not.toBe(first)
    useComposerStore.getState().setTitle('日常')
    expect(useComposerStore.getState().takeIdempotencyKey()).not.toBe(second)
  })

  it('drops the errors of edited fields and removed tasks and keeps the rest', () => {
    useComposerStore.getState().setDrafts([fight, award])
    useComposerStore.getState().setErrors({ a: { stage: '关卡名无效', medicine: '超出范围' }, b: { mail: '无效' } })

    useComposerStore.getState().setDrafts([{ ...fight, params: { stage: '1-8', medicine: 2 } }, award])
    expect(useComposerStore.getState().errors).toEqual({ a: { medicine: '超出范围' }, b: { mail: '无效' } })

    useComposerStore.getState().setDrafts([award, { ...fight, params: { stage: '1-8', medicine: 2 } }])
    expect(useComposerStore.getState().errors).toEqual({ a: { medicine: '超出范围' }, b: { mail: '无效' } })

    useComposerStore.getState().setDrafts([award])
    expect(useComposerStore.getState().errors).toEqual({ b: { mail: '无效' } })
  })

  it('lives in memory only and resets to an empty composition', () => {
    useComposerStore.getState().setTitle('日常')
    useComposerStore.getState().setDrafts([fight])
    expect(localStorage.length).toBe(0)

    useComposerStore.getState().reset()
    expect(useComposerStore.getState()).toMatchObject({ title: '', drafts: [], errors: {}, idempotencyKey: null })
  })
})
