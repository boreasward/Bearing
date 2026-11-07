// Markdown parsing for the brief panel: produces block structures only, never HTML strings.
// Markdown.svelte renders them with {#each}; the design guards ban {@html}.
// Supported: `#`/`##`/`###` headings, paragraphs, `- ` lists, `|` tables and **bold** runs —
// the subset the brief writer emits (facts/brief.py). Anything else is a paragraph.

export interface Run {
  text: string
  bold: boolean
}

export type Block =
  | { type: 'heading'; level: 1 | 2 | 3; runs: Run[] }
  | { type: 'paragraph'; runs: Run[] }
  | { type: 'list'; items: Run[][] }
  | { type: 'table'; header: string[]; rows: string[][] }

/** Split one line of text into plain and `**bold**` runs. */
export function parseRuns(text: string): Run[] {
  const parts = text.split(/\*\*(.+?)\*\*/g)
  const runs: Run[] = []
  for (let i = 0; i < parts.length; i += 1) {
    if (parts[i] === '') continue
    runs.push({ text: parts[i], bold: i % 2 === 1 })
  }
  return runs.length ? runs : []
}

function tableCells(line: string): string[] {
  return line
    .split('|')
    .map((cell) => cell.trim())
    .filter((cell, index, cells) => !(cell === '' && (index === 0 || index === cells.length - 1)))
}

function isTableSeparator(cells: string[]): boolean {
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell))
}

/** Parse a markdown source into blocks; consecutive plain lines join into one paragraph. */
export function parseMarkdown(source: string): Block[] {
  const blocks: Block[] = []
  let paragraph: string[] = []
  const flushParagraph = () => {
    if (paragraph.length) {
      const runs = parseRuns(paragraph.join('\n'))
      if (runs.length) blocks.push({ type: 'paragraph', runs })
      paragraph = []
    }
  }

  const lines = source.split('\n')
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) {
      flushParagraph()
      i += 1
      continue
    }

    const heading = /^(#{1,3})\s+(.*)$/.exec(line)
    if (heading) {
      flushParagraph()
      blocks.push({ type: 'heading', level: heading[1].length as 1 | 2 | 3, runs: parseRuns(heading[2].trim()) })
      i += 1
      continue
    }

    if (trimmed.startsWith('|') && trimmed.includes('|', 1)) {
      flushParagraph()
      const tableLines: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i].trim())
        i += 1
      }
      const rows = tableLines.map(tableCells).filter((cells) => !isTableSeparator(cells))
      if (rows.length) blocks.push({ type: 'table', header: rows[0], rows: rows.slice(1) })
      continue
    }

    if (trimmed.startsWith('- ')) {
      flushParagraph()
      const items: Run[][] = []
      while (i < lines.length && lines[i].trim().startsWith('- ')) {
        items.push(parseRuns(lines[i].trim().slice(2)))
        i += 1
      }
      blocks.push({ type: 'list', items })
      continue
    }

    paragraph.push(trimmed)
    i += 1
  }
  flushParagraph()
  return blocks
}
