import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, within } from '@testing-library/svelte'
import UiGallery from './UiGallery.svelte'
import { TONES } from '$lib/ui/domain/status'

afterEach(() => cleanup())

const SECTIONS = [
  '令牌', '按钮', '徽标', '表格', '确认对话框', '抽屉', '页内页签', '路由页签', '表单', '提示与浮层', '折叠', '提示条', '骨架', '分隔与卡片', '轻提示',
]

describe('UiGallery', () => {
  it('renders every component section', () => {
    render(UiGallery)
    expect(screen.getByText('开发模式组件画廊（不进生产包）')).toBeInTheDocument()
    for (const name of SECTIONS) {
      expect(screen.getByRole('heading', { name }), name).toBeInTheDocument()
    }
  })

  it('shows every button variant and the busy / disabled states', () => {
    render(UiGallery)
    for (const v of ['primary', 'outline', 'ghost', 'link', 'destructive']) {
      expect(screen.getAllByRole('button', { name: new RegExp(`^${v}`) }).length, v).toBeGreaterThan(0)
    }
    expect(screen.getAllByRole('button', { busy: true }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button').some((b) => (b as HTMLButtonElement).disabled)).toBe(true)
  })

  it('shows tables in plain, grouped, expandable and card form', () => {
    render(UiGallery)
    expect(screen.getAllByRole('table').length).toBeGreaterThanOrEqual(3)
    expect(screen.getAllByRole('button', { name: '展开详情' }).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /已结束/ })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: /显示更多/ })).toBeInTheDocument()
  })

  it('renders domain components and the five data-block states', () => {
    const { container } = render(UiGallery)
    for (const name of ['状态词汇', '领域原语', '数据块五态', '页头与分段', '图标']) {
      expect(screen.getByRole('heading', { name }), name).toBeInTheDocument()
    }
    // every vocabulary domain has a row of pills
    const pills = container.querySelectorAll('[data-slot="badge"][data-domain]')
    const expected = Object.values(TONES).reduce((n, keys) => n + Object.keys(keys).length, 0)
    expect(pills.length).toBeGreaterThanOrEqual(expected)
    expect(container.querySelector('[data-domain="execution"][data-status="adopted"]')).toHaveTextContent('已采纳（10-22 成交 20 股）')
    // the five states of the data block
    const states = [...container.querySelectorAll('[data-slot="data-block"]')].map((e) => e.getAttribute('data-state'))
    for (const state of ['loading', 'empty', 'not_computed', 'degraded', 'error', 'ready']) expect(states, state).toContain(state)
    expect(screen.getByText('今日无触发')).toBeInTheDocument()
    expect(screen.getAllByText('没算').length).toBeGreaterThan(0)
    expect(screen.getByText('概念卡暂不可用：网络中断')).toBeInTheDocument()
    const errorBlock = container.querySelector('[data-slot="data-block"][data-state="error"]') as HTMLElement
    fireEvent.click(within(errorBlock).getByRole('button', { name: '重试' }))
    expect(screen.getByText('重试已点 1 次。')).toBeInTheDocument()
    expect(container.querySelector('[data-slot="data-block"][data-state="error"][class*="min-h-"]')).not.toBeNull()   // the page example
    // domain primitives
    expect(screen.getAllByText('第 3 版').length).toBeGreaterThan(0)
    expect(container.textContent).toContain('入场 $95.10–$97.30 · 止损 $91.40 · T1 $104.70 · 建议 120 股')
    expect(screen.getByText('stooq').closest('[data-slot="badge"]')).toHaveAttribute('data-fallback', 'true')
  })
})
