import { describe, expect, it } from 'vitest'
import { TOOL_NAMES } from './registry'
import { toolsByGroup } from './tool-catalog'
import { isMutatingTool } from './tool-write'

describe('tool catalog', () => {
  it('covers every tool exactly once in five groups', () => {
    const groups = toolsByGroup()
    expect(groups.map(group => group.tools.length)).toEqual([15, 10, 3, 4, 3])
    const names = groups.flatMap(group => group.tools.map(tool => tool.name))
    expect(new Set(names).size).toBe(names.length)
    expect([...names].sort()).toEqual([...TOOL_NAMES].sort())
  })

  it('write kinds agree with the mutating tool set', () => {
    const tools = toolsByGroup().flatMap(group => group.tools)
    expect(new Set(tools.filter(tool => tool.write !== 'none').map(tool => tool.name))).toEqual(new Set(TOOL_NAMES.filter(isMutatingTool)))
    expect(tools.filter(tool => tool.write === 'confirm')).toHaveLength(12)
  })

  it('summaries are user-facing', () => {
    const tools = toolsByGroup().flatMap(group => group.tools)
    for (const tool of tools) {
      expect(tool.summary).toMatch(/。$/)
      expect(tool.summary).not.toMatch(/request_id|confirmed|幂等|宿主/)
    }
  })
})
