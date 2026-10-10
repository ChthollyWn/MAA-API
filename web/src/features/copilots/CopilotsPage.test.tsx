import { afterEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import {
  AI_DRAFT_V1,
  AI_DRAFT_V2,
  LEGACY_UPLOAD,
  SITE_COPY,
  installCopilotsApi,
  renderCopilots,
} from './test-utils'

describe('M29 作业页', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('列出全部作业：来源、关卡、标题、创建时间与是否保留', async () => {
    installCopilotsApi()
    renderCopilots()

    expect(await screen.findByText('1-7 三人速刷 v2')).toBeInTheDocument()
    const aiRow = screen.getByTestId(`copilot-${AI_DRAFT_V2.id}`)
    expect(within(aiRow).getByTestId(`copilot-source-${AI_DRAFT_V2.id}`)).toHaveTextContent('AI 生成')
    expect(aiRow).toHaveTextContent('关卡 1-7')
    expect(aiRow).toHaveTextContent('创建于')
    expect(aiRow).toHaveTextContent('跑过 2 次')

    // 作业站来源 + 已保留
    const siteRow = screen.getByTestId(`copilot-${SITE_COPY.id}`)
    expect(within(siteRow).getByTestId(`copilot-source-${SITE_COPY.id}`)).toHaveTextContent('作业站')
    expect(within(siteRow).getByTestId(`copilot-retain-${SITE_COPY.id}`)).toHaveTextContent('已保留')
    expect(siteRow).toHaveTextContent('作业站原帖')

    // M29 之前上传的老资源没有 meta.source：按用户上传显示，关卡退回 stage_name
    const legacyRow = screen.getByTestId(`copilot-${LEGACY_UPLOAD.id}`)
    expect(within(legacyRow).getByTestId(`copilot-source-${LEGACY_UPLOAD.id}`)).toHaveTextContent('用户上传')
    expect(legacyRow).toHaveTextContent('关卡 CE-5')
    expect(legacyRow).toHaveTextContent('还没有执行过')
  })

  it('每次尝试显示结果与判定方，模型判定单独标出', async () => {
    installCopilotsApi()
    renderCopilots()

    await screen.findByText('1-7 三人速刷 v2')
    const attempts = screen.getByLabelText('1-7 三人速刷 v2 的尝试结果')
    expect(within(attempts).getByTestId(`copilot-attempt-${AI_DRAFT_V2.id}-0`)).toHaveTextContent('行动失败')
    expect(within(attempts).getByTestId(`copilot-attempt-${AI_DRAFT_V2.id}-0`)).toHaveTextContent('内核判定')

    const modelAttempt = within(attempts).getByTestId(`copilot-attempt-${AI_DRAFT_V2.id}-1`)
    expect(modelAttempt).toHaveTextContent('无法判定')
    expect(modelAttempt).toHaveTextContent('模型判定')
    expect(modelAttempt.querySelector('.font-semibold')).toHaveTextContent('模型判定')

    // 作业站那份是内核判定的三星
    const siteAttempts = screen.getByLabelText('1-7 作业站 三人 的尝试结果')
    expect(within(siteAttempts).getByText('三星')).toBeInTheDocument()
  })

  it('沿父版本显示版本链，父版本不在已取回的列表里时给占位', async () => {
    installCopilotsApi()
    const { unmount } = renderCopilots()

    await screen.findByText('1-7 三人速刷 v2')
    const chain = screen.getByTestId(`copilot-chain-${AI_DRAFT_V2.id}`)
    expect(chain).toHaveTextContent('版本链')
    expect(chain).toHaveTextContent('1-7 三人速刷')
    expect(chain).toHaveTextContent('1-7 三人速刷 v2（当前）')
    // 第一版自己也要显示链（只有自己一版）
    expect(screen.getByTestId(`copilot-chain-${AI_DRAFT_V1.id}`)).toHaveTextContent('1-7 三人速刷（当前）')

    unmount()
    cleanup()

    // 只取回第二版：父版本显示占位，而不是假装它是第一版
    installCopilotsApi({ items: [AI_DRAFT_V2] })
    renderCopilots()
    await screen.findByText('1-7 三人速刷 v2')
    const partial = screen.getByTestId(`copilot-chain-${AI_DRAFT_V2.id}`)
    expect(partial).toHaveTextContent('不在本页')
  })

  it('「保留」写 meta.retain，刷新后那一行变成已保留；再点一次取消', async () => {
    const { find } = installCopilotsApi()
    renderCopilots()
    await screen.findByText('1-7 三人速刷 v2')

    const row = screen.getByTestId(`copilot-${AI_DRAFT_V2.id}`)
    fireEvent.click(within(row).getByRole('button', { name: '保留' }))

    await waitFor(() => expect(find('POST', `/api/resources/copilots/${AI_DRAFT_V2.id}/retain`)).toHaveLength(1))
    expect(find('POST', `/api/resources/copilots/${AI_DRAFT_V2.id}/retain`)[0].body).toEqual({ retain: true })
    await waitFor(() => expect(
      within(screen.getByTestId(`copilot-${AI_DRAFT_V2.id}`)).getByTestId(`copilot-retain-${AI_DRAFT_V2.id}`),
    ).toHaveTextContent('已保留'))

    fireEvent.click(within(screen.getByTestId(`copilot-${AI_DRAFT_V2.id}`)).getByRole('button', { name: '取消保留' }))
    await waitFor(() => expect(find('POST', `/api/resources/copilots/${AI_DRAFT_V2.id}/retain`)).toHaveLength(2))
    expect(find('POST', `/api/resources/copilots/${AI_DRAFT_V2.id}/retain`)[1].body).toEqual({ retain: false })
  })

  it('删除要二次确认，确认后发 DELETE 并从列表里消失', async () => {
    const { find } = installCopilotsApi()
    renderCopilots()
    await screen.findByText('1-7 作业站 三人')

    const row = screen.getByTestId(`copilot-${SITE_COPY.id}`)
    fireEvent.click(within(row).getByRole('button', { name: '删除' }))
    expect(await screen.findByText('删除「1-7 作业站 三人」？')).toBeInTheDocument()
    expect(find('DELETE', `/api/resources/copilots/${SITE_COPY.id}`)).toHaveLength(0)

    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '删除' }))
    await waitFor(() => expect(find('DELETE', `/api/resources/copilots/${SITE_COPY.id}`)).toHaveLength(1))
    await waitFor(() => expect(screen.queryByTestId(`copilot-${SITE_COPY.id}`)).not.toBeInTheDocument())
  })

  it('查看 JSON 打开面板并按需取详情，下载用详情内容存成文件', async () => {
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:copilot')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const { find } = installCopilotsApi({ items: [LEGACY_UPLOAD] })
    renderCopilots()

    await screen.findByText('钱本 4 人')
    expect(find('GET', `/api/resources/copilots/${LEGACY_UPLOAD.id}`)).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '查看 钱本 4 人 的 JSON' }))
    await waitFor(() => expect(find('GET', `/api/resources/copilots/${LEGACY_UPLOAD.id}`)).toHaveLength(1))
    expect(await screen.findByLabelText('作业 JSON')).toHaveTextContent('"stage_name": "CE-5"')

    fireEvent.click(screen.getByRole('button', { name: '下载 JSON' }))
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1))
    expect(click).toHaveBeenCalledTimes(1)
  })

  it('来源筛选在已取回的列表里过滤，搜索走列表端点的 q 参数', async () => {
    const { find } = installCopilotsApi()
    renderCopilots()
    await screen.findByText('1-7 三人速刷 v2')

    fireEvent.change(screen.getByLabelText('来源'), { target: { value: 'site' } })
    expect(screen.queryByTestId(`copilot-${AI_DRAFT_V2.id}`)).not.toBeInTheDocument()
    expect(screen.getByTestId(`copilot-${SITE_COPY.id}`)).toBeInTheDocument()
    expect(screen.getByText(/本页筛出 1 份/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('来源'), { target: { value: '' } })
    fireEvent.change(screen.getByPlaceholderText('搜索标题、关卡或说明'), { target: { value: 'CE-5' } })
    await waitFor(() => expect(find('GET', '/api/resources/copilots').at(-1)?.url.searchParams.get('q')).toBe('CE-5'))
    await waitFor(() => expect(screen.queryByTestId(`copilot-${AI_DRAFT_V2.id}`)).not.toBeInTheDocument())
    expect(screen.getByTestId(`copilot-${LEGACY_UPLOAD.id}`)).toBeInTheDocument()
  })

  it('列表读取失败时给出错误态与重试', async () => {
    installCopilotsApi({ listStatus: 503 })
    renderCopilots()

    expect(await screen.findByText('作业列表读取失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('一份作业都没有时给空状态', async () => {
    installCopilotsApi({ items: [] })
    renderCopilots()

    expect(await screen.findByText('还没有作业')).toBeInTheDocument()
  })
})
