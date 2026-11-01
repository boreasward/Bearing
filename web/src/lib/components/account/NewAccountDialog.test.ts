import { afterEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/svelte'
import NewAccountDialog from './NewAccountDialog.svelte'
import { paperFixture } from './fixtures'

afterEach(() => vi.unstubAllGlobals())
it.each(['success', 'error'])('ignores a late create %s after the dialog is closed and reopened', async outcome => {
  let release!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { release = resolve })))
  const oncreated = vi.fn()
  const view = render(NewAccountDialog, { open: true, oncreated })
  await screen.findByRole('dialog')
  await fireEvent.input(screen.getByLabelText('账户名称'), { target: { value: '旧名称' } })
  await fireEvent.click(screen.getByRole('button', { name: '创建模拟账户' }))
  await waitFor(() => expect(release).toBeTypeOf('function'))
  await view.rerender({ open: false, oncreated })
  await view.rerender({ open: true, oncreated })
  await fireEvent.input(screen.getByLabelText('账户名称'), { target: { value: '新名称' } })
  await act(async () => { release(new Response(JSON.stringify(outcome === 'success' ? { status: 'created', account: paperFixture } : { error: 'old_create_failed' }), { status: outcome === 'success' ? 200 : 400 })); await new Promise(resolve => setTimeout(resolve, 0)) })
  expect(screen.getByRole('dialog', { name: '新建模拟账户' })).toBeInTheDocument()
  expect((screen.getByLabelText('账户名称') as HTMLInputElement).value).toBe('新名称')
  expect(screen.getByRole('button', { name: '创建模拟账户' })).toBeEnabled()
  expect(oncreated).not.toHaveBeenCalled()
  expect(screen.queryByText(/old_create_failed/)).toBeNull()
})
it('creates a paper account, omits blank config parameters and disables duplicate submission', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'created', account: paperFixture })))
  vi.stubGlobal('fetch', fetcher)
  const oncreated = vi.fn()
  render(NewAccountDialog, { open: true, oncreated })
  await screen.findByRole('dialog', { name: '新建模拟账户' })
  await fireEvent.input(screen.getByLabelText('账户名称'), { target: { value: '我的模拟' } })
  await fireEvent.click(screen.getByRole('button', { name: '创建模拟账户' }))
  await waitFor(() => expect(oncreated).toHaveBeenCalledWith(paperFixture))
  const body = JSON.parse(fetcher.mock.calls[0][1].body)
  expect(body).toMatchObject({ name: '我的模拟', strategy: 'agent_managed', opening_cash: '100000' })
  expect(body).not.toHaveProperty('params')
  expect(body).not.toHaveProperty('opened_on')
})
