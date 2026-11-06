import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/svelte'
import StatusPill from './StatusPill.svelte'
import { LABELS } from './status'

afterEach(() => cleanup())

describe('StatusPill', () => {
  it('text and title shown', () => {
    render(StatusPill, { domain: 'node', value: 'degraded', title: 'Yahoo 空结果' })
    const pill = screen.getByText('降级').closest('[data-slot="badge"]')!
    expect(pill).toHaveAttribute('title', 'Yahoo 空结果')
    expect(pill).toHaveAttribute('data-domain', 'node')
    expect(pill).toHaveAttribute('data-status', 'degraded')
  })

  it('tone class per tone', () => {
    const cases: [string, string, string, string][] = [
      ['node', 'success', 'success', 'border-success/40'],
      ['node', 'degraded', 'warn', 'border-warn/40'],
      ['node', 'failed', 'danger', 'border-destructive/40'],
      ['chain', 'entry', 'info', 'border-info/40'],
      ['node', 'running', 'running', 'border-running/40'],
      ['node', 'skipped', 'neutral', 'border-neutral/40'],
    ]
    for (const [domain, value, tone, cls] of cases) {
      const { container, unmount } = render(StatusPill, { domain: domain as 'node', value })
      const pill = container.querySelector('[data-slot="badge"]')!
      expect(pill, `${domain}.${value}`).toHaveAttribute('data-tone', tone)
      expect(pill.className, `${domain}.${value}`).toContain(cls)
      unmount()
    }
  })

  it('running pulses', () => {
    const { container } = render(StatusPill, { domain: 'node', value: 'running' })
    expect(container.querySelector('[data-slot="badge"]')!.className).toContain('animate-running')
    cleanup()
    const other = render(StatusPill, { domain: 'node', value: 'success' })
    expect(other.container.querySelector('[data-slot="badge"]')!.className).not.toContain('animate-running')
  })

  it('text override keeps tone', () => {
    const { container } = render(StatusPill, {
      domain: 'execution', value: 'adopted',
      text: LABELS.execution.adopted.replace('{date}', '10-22').replace('{quantity}', '20'),
    })
    expect(screen.getByText('已采纳（10-22 成交 20 股）')).toBeInTheDocument()
    expect(container.querySelector('[data-slot="badge"]')).toHaveAttribute('data-tone', 'success')
  })

  it('the label is always text, not colour alone', () => {
    render(StatusPill, { domain: 'connection', value: 'needs_login' })
    expect(screen.getByText('需要重新登录')).toBeInTheDocument()
  })
})
