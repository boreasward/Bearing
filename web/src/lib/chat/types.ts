export type Block = { kind: 'text'; text: string } | {
  kind: 'tool'; id: number; tool: string; ref: string | null
  state: 'calling' | 'ready' | 'untraceable' | 'error'; data?: unknown; error?: string
}
export type Turn = { text: string; blocks: Block[]; finished: 'running' | 'done' | 'error' | 'cancelled'; agentSession: string | null }
