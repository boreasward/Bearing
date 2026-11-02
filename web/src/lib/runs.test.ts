import { describe, expect, it } from 'vitest'
import {
  LOGIN_POLL_MS, NODE_STATUS_TEXT, RESUME_ERROR_TEXT, RESUME_POLL_MAX_MS, RESUME_POLL_MS, RETRO_PUSH_TEXT, RETRO_STATUS_TEXT, RUN_ID_RE,
  durationText, failedTargetsFirst, maskEmail, nyDay, resumeErrorText, runHref,
} from './runs'

describe('runs helpers', () => {
  it('runHref links to the detail page', () => {
    expect(runHref('daily-20261030033000-1a2b3c4d')).toBe('#tools/runs/daily-20261030033000-1a2b3c4d')
  })

  it('failedTargetsFirst keeps order within groups', () => {
    const t = (target: string, ok: boolean) => ({ target, ok })
    const out = failedTargetsFirst([t('A', true), t('B', false), t('C', true), t('D', false), t('E', true)])
    expect(out.map(x => x.target)).toEqual(['B', 'D', 'A', 'C', 'E'])
    expect(failedTargetsFirst([])).toEqual([])
  })

  it('RUN_ID_RE matches job report ids only', () => {
    expect(RUN_ID_RE.source).toBe('^[a-z_]+-\\d{14}-[0-9a-f]{8}$')
    for (const id of ['daily-20261030033000-1a2b3c4d', 'daily_resume-20261030050102-9f8e7d6c', 'workbench-20261027063000-00ff00ff'])
      expect(RUN_ID_RE.test(id)).toBe(true)
    for (const id of ['', 'daily', 'daily-2026-1a2b3c4d', 'Daily-20261030033000-1a2b3c4d', 'daily-20261030033000-1A2B3C4D',
      'daily-20261030033000-1a2b3c4', 'daily-20261030033000-1a2b3c4d/x', 'x-20261030033000-1a2b3c4d\n'])
      expect(RUN_ID_RE.test(id)).toBe(false)
  })

  it('constants and texts', () => {
    expect([RESUME_POLL_MS, RESUME_POLL_MAX_MS, LOGIN_POLL_MS]).toEqual([5000, 3_600_000, 2000])
    expect(NODE_STATUS_TEXT).toEqual({
      success: '成功', degraded: '降级', partial: '部分成功', failed: '失败', skipped: '跳过', pending: '未开始', running: '运行中',
    })
    expect(RESUME_ERROR_TEXT).toEqual({
      not_failed: '这次运行没有失败的节点',
      node_mismatch: '只能从失败的节点 <next> 继续',
      resume_window_closed: '已过 <下一交易日> 纽约 17:00，只能整次重新运行',
      graph_version_changed: '这次运行由旧版流程图产生，不能续跑，只能整次重新运行',
      daily_running: '每日工作流正在运行',
      interrupted: '这次运行中途中断，只能整次重新运行',
      stage_failed: '这一步记了失败但链已跑完，不能续跑；需要时整次重新运行',
      checkpoint_unavailable: '状态库未就绪，暂时无法续跑',
    })
    expect(RETRO_STATUS_TEXT.to_review).toBe('待审定')
    expect(RETRO_PUSH_TEXT.manual_run).toBe('手动运行不推送')
  })

  it('resumeErrorText fills the placeholders', () => {
    expect(resumeErrorText('node_mismatch', { next: '数据采集' })).toBe('只能从失败的节点 数据采集 继续')
    expect(resumeErrorText('resume_window_closed', { day: '2026-10-30' })).toBe('已过 2026-10-30 纽约 17:00，只能整次重新运行')
    expect(resumeErrorText('not_failed')).toBe('这次运行没有失败的节点')
    expect(resumeErrorText('nope')).toBeNull()
  })

  it('maskEmail, durationText and nyDay', () => {
    expect(maskEmail('alice@example.com')).toBe('a***@example.com')
    expect(durationText(null)).toBe('')
    expect(durationText(812)).toBe('812 毫秒')
    expect(durationText(1234)).toBe('1.2 秒')
    expect(durationText(312000)).toBe('5 分 12 秒')
    // 2026-10-30 21:00Z is 17:00 in New York (EDT)
    expect(nyDay('2026-10-30T21:00:00Z')).toBe('2026-10-30')
    expect(nyDay(null)).toBeNull()
  })
})
