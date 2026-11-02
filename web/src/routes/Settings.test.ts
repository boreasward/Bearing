import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/svelte'
import Settings from './Settings.svelte'
import * as api from '../lib/api'

describe('Settings · Watchlist', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders a single table with delete action column', async () => {
    vi.spyOn(api, 'getWatchlist').mockResolvedValue({
      rows: [{ ticker: 'NVDA', status: 'active', thesis: null }],
    } as any)

    render(Settings, { props: { sub: 'watchlist' } })

    await screen.findByText('NVDA')
    // 只应有一个表格，删除按钮在表格行内（不再有独立裸列表）
    expect(document.querySelectorAll('table').length).toBe(1)
    const del = await screen.findByText('删除')
    expect(del.closest('table')).toBeTruthy()
    expect(document.querySelectorAll('ul li').length).toBe(0)
  })
})

describe('Settings · Discord', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('verify button calls testNotifyChannel', async () => {
    vi.spyOn(api, 'getNotifySettings').mockResolvedValue({
      channel: 'discord',
      discord_enabled: true,
      task_channels: { workbench: 'signals' },
      task_enabled: { workbench: true },
      webhooks: { signals: { configured: true } },
      email: { from_addr: '', to_addr: '' },
      degraded: false,
    } as any)
    vi.spyOn(api, 'getEnvStatus').mockResolvedValue({ SMTP_PASSWORD: false } as any)
    const spy = vi.spyOn(api, 'testNotifyChannel').mockResolvedValue({ ok: true })

    render(Settings, { props: { sub: 'discord' } })

    const btn = await screen.findByText('验证 signals')
    await fireEvent.click(btn)

    expect(spy).toHaveBeenCalled()
  })

  it('shows only earnings and signals webhooks', async () => {
    vi.spyOn(api, 'getNotifySettings').mockResolvedValue({
      channel: 'discord',
      discord_enabled: true,
      task_channels: {},
      task_enabled: {},
      webhooks: { earnings: { configured: true }, signals: { configured: false } },
      email: { from_addr: '', to_addr: '' },
      degraded: false,
    } as any)
    vi.spyOn(api, 'getEnvStatus').mockResolvedValue({ SMTP_PASSWORD: false } as any)
    vi.spyOn(api, 'testNotifyChannel').mockResolvedValue({ ok: true })

    render(Settings, { props: { sub: 'discord' } })

    await screen.findByText('验证 earnings')
    expect(screen.getByText('验证 signals')).toBeTruthy()
    expect(screen.queryByText('验证 daily')).toBeNull()
  })

  it('webhook inputs are password type — plaintext never visible', async () => {
    vi.spyOn(api, 'getNotifySettings').mockResolvedValue({
      channel: 'discord',
      discord_enabled: true,
      task_channels: {},
      task_enabled: {},
      webhooks: { earnings: { configured: false }, signals: { configured: false } },
      email: { from_addr: '', to_addr: '' },
      degraded: false,
    } as any)
    vi.spyOn(api, 'getEnvStatus').mockResolvedValue({ SMTP_PASSWORD: false } as any)
    vi.spyOn(api, 'testNotifyChannel').mockResolvedValue({ ok: true })

    render(Settings, { props: { sub: 'discord' } })

    await screen.findByText('验证 signals')

    const inputs = document.querySelectorAll('input[type="password"]')
    expect(inputs.length).toBeGreaterThanOrEqual(2)
  })
})

describe('Settings · 通知渠道（Discord/邮件）', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('switching to 邮件 hides Discord fields and shows the email form', async () => {
    vi.spyOn(api, 'getNotifySettings').mockResolvedValue({
      channel: 'discord',
      discord_enabled: true,
      task_channels: {},
      task_enabled: {},
      webhooks: { earnings: { configured: false }, signals: { configured: false } },
      email: { from_addr: '', to_addr: '' },
      degraded: false,
    } as any)
    vi.spyOn(api, 'getEnvStatus').mockResolvedValue({ SMTP_PASSWORD: true } as any)

    render(Settings, { props: { sub: 'discord' } })

    await screen.findByText('验证 signals')
    const emailRadio = screen.getByLabelText('邮件') as HTMLInputElement
    await fireEvent.click(emailRadio)

    expect(screen.queryByText('验证 signals')).toBeNull()
    await screen.findByPlaceholderText('you@gmail.com')
    await screen.findByText(/SMTP_PASSWORD.*已配置/)
  })

  it('发送测试邮件 calls testNotifyChannel with the email channel', async () => {
    vi.spyOn(api, 'getNotifySettings').mockResolvedValue({
      channel: 'email',
      discord_enabled: true,
      task_channels: {},
      task_enabled: {},
      webhooks: {},
      email: { from_addr: 'a@gmail.com', to_addr: 'b@outlook.com' },
      degraded: false,
    } as any)
    vi.spyOn(api, 'getEnvStatus').mockResolvedValue({ SMTP_PASSWORD: false } as any)
    const spy = vi.spyOn(api, 'testNotifyChannel').mockResolvedValue({ ok: true })

    render(Settings, { props: { sub: 'discord' } })

    const btn = await screen.findByText('发送测试邮件')
    await fireEvent.click(btn)

    expect(spy).toHaveBeenCalledWith({
      channel: 'email',
      email: { from_addr: 'a@gmail.com', to_addr: 'b@outlook.com' },
    })
    await screen.findByText('成功')
  })
})

describe('Settings · 状态与页签', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('test results are run pills (成功 / 失败 with the reason), not emoji lines', async () => {
    vi.spyOn(api, 'getNotifySettings').mockResolvedValue({
      channel: 'discord', discord_enabled: true, task_channels: {}, task_enabled: {},
      webhooks: { earnings: { configured: true }, signals: { configured: false } },
      email: { from_addr: '', to_addr: '' }, degraded: false,
    } as any)
    vi.spyOn(api, 'getEnvStatus').mockResolvedValue({ SMTP_PASSWORD: false } as any)
    vi.spyOn(api, 'testNotifyChannel').mockResolvedValueOnce({ ok: true }).mockResolvedValueOnce({ ok: false, error: 'bad webhook' } as any)
    const { container } = render(Settings, { props: { sub: 'discord' } })
    await fireEvent.click(await screen.findByText('验证 earnings'))
    const ok = (await screen.findByText('成功')).closest('[data-domain]')!
    expect(ok).toHaveAttribute('data-status', 'success')
    expect(ok).toHaveAttribute('data-tone', 'success')
    await fireEvent.click(screen.getByText('验证 signals'))
    const failed = (await screen.findByText('失败')).closest('[data-domain]')!
    expect(failed).toHaveAttribute('data-tone', 'danger')
    expect(await screen.findByText('bad webhook')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/[\u2705\u274C\u2713\u2715]/)
  })

  it('system checks are system pills with their tone', async () => {
    vi.spyOn(api, 'getStatus').mockResolvedValue({ db: { status: 'down' }, queue: { status: 'degraded' } } as any)
    vi.spyOn(api, 'getOperations').mockResolvedValue({ operations: [{ type: 'health_check', run_id: 'abcdef0123456789', status: 'up' }] } as any)
    vi.spyOn(api, 'getAgentConnection').mockResolvedValue({
      status: 'disconnected', email_masked: null, scopes_ok: null, access_expires_at: null, refresh_expires_at: null,
      last_refresh_at: null, last_error: null, model: null, model_display_name: null, provider_setting: 'chatgpt', pending: null,
    })
    render(Settings, { props: { sub: 'system' } })
    await screen.findByRole('heading', { name: '系统' })
    const pills = [...document.querySelectorAll('[data-domain="system"]')].map(el => [el.getAttribute('data-status'), el.getAttribute('data-tone')])
    expect(pills).toEqual([['down', 'danger'], ['degraded', 'warn'], ['up', 'success']])
    expect(screen.getByRole('table', { name: '最近操作' })).toHaveTextContent('abcdef012345')
  })

  it('the sub pages are route tabs (links with aria-current)', async () => {
    vi.spyOn(api, 'getEnvStatus').mockResolvedValue({ WORKBENCH_AGENT_PROVIDER: true, SMTP_PASSWORD: false } as any)
    render(Settings, { props: { sub: 'env' } })
    await screen.findByText('WORKBENCH_AGENT_PROVIDER')
    const tabs = within(screen.getByRole('navigation', { name: '设置' }))
    expect(tabs.getAllByRole('link').map(a => [a.textContent!.trim(), a.getAttribute('href')])).toEqual([
      ['系统', '#settings/system'], ['关注列表', '#settings/watchlist'], ['推送配置', '#settings/discord'],
      ['定时任务', '#settings/jobs'], ['环境变量', '#settings/env'], ['功能开关', '#settings/features']])
    expect(tabs.getByRole('link', { name: '环境变量' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('tablist')).toBeNull()
    // set / unset are words, not ✅ / — marks
    expect(screen.getByRole('table', { name: '环境变量' })).toHaveTextContent('已设置')
    expect(screen.getByRole('table', { name: '环境变量' })).toHaveTextContent('未设置')
  })
})

describe('Settings · Agent 连接', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('Agent 连接 section after 系统', async () => {
    vi.spyOn(api, 'getStatus').mockResolvedValue({ db: { status: 'up' } } as any)
    vi.spyOn(api, 'getOperations').mockResolvedValue({ operations: [] } as any)
    const conn = vi.spyOn(api, 'getAgentConnection').mockResolvedValue({
      status: 'disconnected', email_masked: null, scopes_ok: null, access_expires_at: null, refresh_expires_at: null,
      last_refresh_at: null, last_error: null, model: null, model_display_name: null, provider_setting: 'chatgpt', pending: null,
    })
    render(Settings, { props: { sub: 'system' } })
    const system = await screen.findByRole('heading', { name: '系统' })
    const agent = await screen.findByRole('heading', { name: 'Agent 连接' })
    expect(system.compareDocumentPosition(agent) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(await screen.findByText('未连接 ChatGPT')).toBeTruthy()
    expect(conn).toHaveBeenCalledTimes(1)
  })

  it('manual-only jobs have no time input and no enable toggle on the schedule page', async () => {
    vi.spyOn(api, 'getScheduledJobs').mockResolvedValue({
      jobs: [
        { name: 'daily', time_local: '11:30', weekday_mask: '2-6', timezone: 'Asia/Shanghai', enabled: true, next_run_at: null, last_run_at: null },
        { name: 'sec_backfill', manual_only: true, enabled: false, next_run_at: null, time_local: null, weekday_mask: null, timezone: null, description: null },
      ],
      degraded: false,
    })
    render(Settings, { props: { sub: 'jobs' } })
    const row = (await screen.findByText('sec_backfill')).closest('tr')!
    expect(row.querySelector('input')).toBeNull()
    expect(row).toHaveTextContent('只手动运行，不参与定时调度')
    expect(screen.getByText('daily').closest('tr')!.querySelectorAll('input')).toHaveLength(2)
  })

  it('other settings pages do not load the connection', async () => {
    vi.spyOn(api, 'getEnvStatus').mockResolvedValue({ WORKBENCH_AGENT_PROVIDER: true } as any)
    const conn = vi.spyOn(api, 'getAgentConnection')
    render(Settings, { props: { sub: 'env' } })
    await screen.findByText('WORKBENCH_AGENT_PROVIDER')
    expect(screen.queryByRole('heading', { name: 'Agent 连接' })).toBeNull()
    expect(conn).not.toHaveBeenCalled()
  })
})
