import { afterEach, describe, expect, it } from 'vitest'
import { cleanup } from '@testing-library/svelte'
import { parseMarkdown, parseRuns } from './markdown'

afterEach(() => cleanup())

describe('parseRuns', () => {
  it('splits **bold** segments and keeps plain text', () => {
    expect(parseRuns('前 **粗体** 后')).toEqual([
      { text: '前 ', bold: false },
      { text: '粗体', bold: true },
      { text: ' 后', bold: false },
    ])
  })

  it('plain text without markers is one non-bold run', () => {
    expect(parseRuns('止损 91.40')).toEqual([{ text: '止损 91.40', bold: false }])
  })
})

describe('parseMarkdown', () => {
  it('parses #, ## and ### into heading levels', () => {
    const blocks = parseMarkdown('# 简报\n\n## 持仓\n\n### 小节')
    expect(blocks).toEqual([
      { type: 'heading', level: 1, runs: [{ text: '简报', bold: false }] },
      { type: 'heading', level: 2, runs: [{ text: '持仓', bold: false }] },
      { type: 'heading', level: 3, runs: [{ text: '小节', bold: false }] },
    ])
  })

  it('parses plain lines into paragraphs, unknown lines included', () => {
    const blocks = parseMarkdown('和昨天比变了什么\n没有变化\n\nfoo bar：不是 markdown 的行')
    expect(blocks).toEqual([
      { type: 'paragraph', runs: [{ text: '和昨天比变了什么\n没有变化', bold: false }] },
      { type: 'paragraph', runs: [{ text: 'foo bar：不是 markdown 的行', bold: false }] },
    ])
  })

  it('parses - lines into one list block', () => {
    const blocks = parseMarkdown('- MU：止损已触及，未见卖出记录\n- NVDA：下次开盘卖出')
    expect(blocks).toEqual([
      {
        type: 'list',
        items: [
          [{ text: 'MU：止损已触及，未见卖出记录', bold: false }],
          [{ text: 'NVDA：下次开盘卖出', bold: false }],
        ],
      },
    ])
  })

  it('parses | lines into a table with header and rows, separator skipped', () => {
    const blocks = parseMarkdown('| 股票 | 结论 |\n| --- | --- |\n| MU | 延续 |\n| NVDA | 新上榜 |')
    expect(blocks).toEqual([
      { type: 'table', header: ['股票', '结论'], rows: [['MU', '延续'], ['NVDA', '新上榜']] },
    ])
  })

  it('keeps **bold** runs inside paragraphs, headings and list items', () => {
    const blocks = parseMarkdown('段落有 **加粗**\n\n## 标题 **也加粗**\n\n- 列表 **加粗**')
    expect(blocks[0]).toEqual({
      type: 'paragraph',
      runs: [
        { text: '段落有 ', bold: false },
        { text: '加粗', bold: true },
      ],
    })
    expect(blocks[1]).toEqual({
      type: 'heading',
      level: 2,
      runs: [
        { text: '标题 ', bold: false },
        { text: '也加粗', bold: true },
      ],
    })
    expect(blocks[2]).toEqual({
      type: 'list',
      items: [[{ text: '列表 ', bold: false }, { text: '加粗', bold: true }]],
    })
  })

  it('produces block structures only, never an HTML string', () => {
    const blocks = parseMarkdown('# 标题\n\n段落 **粗体** 与 [链接](http://example.com)\n\n- 列表')
    const serialized = JSON.stringify(blocks)
    expect(serialized).not.toContain('<')
    expect(serialized).not.toContain('</')
    for (const block of blocks) expect(typeof (block as { type: string }).type).toBe('string')
  })

  it('blank input parses to no blocks', () => {
    expect(parseMarkdown('')).toEqual([])
    expect(parseMarkdown('\n\n\n')).toEqual([])
  })
})
