import { expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/svelte'
import AccountSwitcher from './AccountSwitcher.svelte'
import { accountFixture, paperFixture } from './fixtures'

it('links each account with its parameters, unopened state, and a create action', async () => {
  const onnew = vi.fn()
  render(AccountSwitcher, { accounts: [{ ...accountFixture, opened: false }, { ...paperFixture, status: 'closed' }], selectedId: accountFixture.id, onnew })
  const nav = screen.getByRole('navigation', { name: '账户切换' })
  expect(within(nav).getByRole('link', { name: /真实账户/ })).toHaveAttribute('href', `#account/${accountFixture.id}`)
  expect(nav).toHaveTextContent('未开账')
  expect(nav).toHaveTextContent('风险 1%')
  expect(nav).toHaveTextContent('已关闭')
  await fireEvent.click(screen.getByRole('button', { name: '新建模拟账户' }))
  expect(onnew).toHaveBeenCalledOnce()
})
