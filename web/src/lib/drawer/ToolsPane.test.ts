import { describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach } from 'vitest'
import ToolsPane from './ToolsPane.svelte'

afterEach(cleanup)

describe('ToolsPane', () => {
  it('lists five collapsible groups with counts and every tool', () => {
    render(ToolsPane)
    expect(screen.getByText('助手在对话里能调用的 35 个工具')).toBeVisible()
    for (const title of ['看数据 · 15', '计划与持仓 · 10', '研究 · 3', '账本 · 4', '关注与数据 · 3']) {
      expect(screen.getByRole('button', { name: title })).toBeVisible()
    }
    expect(document.querySelectorAll('code')).toHaveLength(35)
    expect(screen.getByText('get_market')).toBeVisible()
    expect(screen.getByText('watchlist_remove')).toBeVisible()
  })

  it('each row shows label, name, summary and a write mark', () => {
    render(ToolsPane)
    const savePlan = screen.getByText('save_plan').closest('li')!
    expect(within(savePlan).getByText('保存计划')).toBeVisible()
    expect(within(savePlan).getByText('给已确认的股票保存交易计划；股数由系统计算。')).toBeVisible()
    expect(within(savePlan).getByText('写入')).toBeVisible()
    const raiseStop = screen.getByText('raise_stop').closest('li')!
    expect(within(raiseStop).getByText('写入 · 先确认')).toBeVisible()
    const market = screen.getByText('get_market').closest('li')!
    expect(within(market).queryByText('写入')).not.toBeInTheDocument()
  })

  it('rows are not interactive and groups collapse', async () => {
    render(ToolsPane)
    expect(screen.getAllByRole('button')).toHaveLength(5)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '研究 · 3' }))
    expect(screen.queryByText('start_research')).not.toBeInTheDocument()
    expect(screen.getByText('save_plan')).toBeVisible()
  })
})
