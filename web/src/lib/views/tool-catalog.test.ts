import { describe, expect, it } from 'vitest'
import { TOOL_NAMES } from './registry'
import { toolsByGroup } from './tool-catalog'
import { isMutatingTool } from './tool-write'

describe('tool catalog', () => {
  it('covers every tool exactly once in five groups', () => {
    const groups = toolsByGroup()
    expect(groups.map(group => group.tools.length)).toEqual([15, 13, 3, 4, 3])
    const names = groups.flatMap(group => group.tools.map(tool => tool.name))
    expect(new Set(names).size).toBe(names.length)
    expect([...names].sort()).toEqual([...TOOL_NAMES].sort())
  })

  it('write kinds agree with the mutating tool set', () => {
    const tools = toolsByGroup().flatMap(group => group.tools)
    expect(new Set(tools.filter(tool => tool.write !== 'none').map(tool => tool.name))).toEqual(new Set(TOOL_NAMES.filter(isMutatingTool)))
    expect(tools.filter(tool => tool.write === 'confirm')).toHaveLength(14)
  })

  it('v3.3 rotation tools: the table is read only, saving and withdrawing confirm first', () => {
    const tools = toolsByGroup().flatMap(group => group.tools)
    const byName = Object.fromEntries(tools.map(tool => [tool.name, tool]))
    expect(byName.get_rotation_table.write).toBe('none')
    expect(byName.save_rotation.write).toBe('confirm')
    expect(byName.withdraw_rotation.write).toBe('confirm')
    expect(byName.get_rotation_table.label).toBe('换仓比较表')
    expect(byName.save_rotation.label).toBe('保存换仓结论')
    expect(byName.withdraw_rotation.label).toBe('撤回换仓指令')
    expect(toolsByGroup().find(group => group.key === 'plan')!.tools.map(tool => tool.name)).toEqual(expect.arrayContaining(['get_rotation_table', 'save_rotation', 'withdraw_rotation']))
    expect(isMutatingTool('save_rotation')).toBe(true)
    expect(isMutatingTool('withdraw_rotation')).toBe(true)
    expect(isMutatingTool('get_rotation_table')).toBe(false)
  })

  it('summaries are user-facing', () => {
    const tools = toolsByGroup().flatMap(group => group.tools)
    for (const tool of tools) {
      expect(tool.summary).toMatch(/。$/)
      expect(tool.summary).not.toMatch(/request_id|confirmed|幂等|宿主/)
    }
  })
})
