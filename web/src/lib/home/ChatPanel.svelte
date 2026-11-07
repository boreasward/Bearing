<script lang="ts">
  import * as Select from '$lib/ui/select/index'
  import * as Collapsible from '$lib/ui/collapsible/index'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { Button } from '$lib/ui/button/index'
  import Field from '$lib/ui/field/Field.svelte'
  import Textarea from '$lib/ui/textarea/textarea.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import { get, post } from '$lib/api'
  import { notify } from '$lib/ui/toast'
  import { readEvents } from './sse'
  import { errorText, type ChatListItem, type ChatMessage, type ChatRow, type RuntimeEventPayload } from './types'

  // 对话（spec §12 / v3 设计 §15.2）：输入框在简报下方，回答流式出现（sse.ts 读 SSE），
  // 工具调用折叠可展开；可新开对话、回看历史；一轮可取消；简报运行中提问得到固定提示。
  let { version = 0 }: { version?: number } = $props()

  interface ToolCall {
    id: number
    name: string
    ref: string
    done: boolean
  }
  interface Turn {
    text: string
    tools: ToolCall[]
    finished: 'running' | 'done' | 'error' | 'cancelled'
  }

  let chats = $state<ChatListItem[]>([])
  let chatId = $state('')
  let chat = $state<ChatRow | null>(null)
  let messages = $state<ChatMessage[]>([])
  let input = $state('')
  let turn = $state<Turn | null>(null)
  let turnNotice = $state('')
  let turnError = $state('')
  let running = $state(false)
  let aborting: AbortController | null = null
  let toolSeq = 0
  let openTools = $state<Record<number, boolean>>({})

  function busyText(code: string): string {
    if (code === 'brief_running') return '正在生成简报，请稍后再问'
    if (code === 'chat_running') return '上一轮回答还在生成，请稍后再问'
    if (code === 'no_run') return '还没有运行记录，稍后再问'
    if (code === 'chat_read_only') return '该对话已只读'
    return errorText(code)
  }

  async function loadChats(preferred = '') {
    try {
      const data = await get<{ chats: ChatListItem[] }>('/api/chats?limit=30')
      chats = data.chats
      await selectChat(preferred || chats[0]?.chat_id || '')
    } catch {
      chats = []
    }
  }

  async function selectChat(id: string) {
    if (!id) {
      chatId = ''
      chat = null
      messages = []
      return
    }
    chatId = id
    turn = null
    turnNotice = ''
    turnError = ''
    try {
      const data = await get<{ chat: ChatRow; messages: ChatMessage[] }>(`/api/chats/${id}`, { errorDetails: true })
      if (chatId !== id) return
      chat = data.chat
      messages = data.messages
    } catch (cause) {
      chat = null
      messages = []
      turnError = errorText(cause)
    }
  }

  $effect(() => {
    void version
    loadChats()
  })

  async function newChat() {
    try {
      const created = await post<{ chat_id: string }>('/api/chats', {})
      await loadChats(created.chat_id)
    } catch (cause) {
      const code = cause instanceof Error ? cause.message : String(cause)
      notify.error(code === 'no_runtime_selected' ? '还没有选定运行时，请先在设置里选择' : errorText(cause))
    }
  }

  async function send() {
    const text = input.trim()
    if (!chatId || !text || running || chat?.read_only) return
    input = ''
    turnNotice = ''
    turnError = ''
    // the previous finished turn folds into the history; the server already persisted it
    if (turn) {
      messages = [
        ...messages,
        {
          seq: messages.length + 1,
          role: 'assistant',
          content: turn.text,
          status: null,
          session: null,
          tool_refs: turn.tools.map((tool) => tool.ref || tool.name).filter(Boolean),
          usage: null,
          created_at: new Date().toISOString(),
        },
      ]
    }
    running = true
    turn = { text: '', tools: [], finished: 'running' }
    messages = [...messages, { seq: messages.length + 1, role: 'user', content: text, status: null, session: null, tool_refs: [], usage: null, created_at: new Date().toISOString() }]

    aborting = new AbortController()
    try {
      const response = await fetch(`/api/chats/${chatId}/messages`, {
        method: 'POST',
        headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
        signal: aborting.signal,
      })
      if (!response.ok) {
        const data = (await response.json().catch(() => null)) as { error?: string } | null
        turnNotice = busyText(data?.error ?? `HTTP ${response.status}`)
        // the server never saw this question: roll the optimistic user message back and keep the draft
        messages = messages.slice(0, -1)
        input = text
        turn = null
        return
      }
      await readEvents(
        response,
        (frame) => {
          if (frame.event === 'done') {
            turn = turn && { ...turn, finished: 'done' }
            return
          }
          const payload = JSON.parse(frame.data) as RuntimeEventPayload
          if (payload.kind === 'text') turn = turn && { ...turn, text: turn.text + (payload.text ?? '') }
          else if (payload.kind === 'tool_start') {
            toolSeq += 1
            turn = turn && { ...turn, tools: [...turn.tools, { id: toolSeq, name: payload.tool ?? '', ref: '', done: false }] }
          } else if (payload.kind === 'tool_end') {
            turn = turn && {
              ...turn,
              tools: turn.tools.map((tool) =>
                tool.name === (payload.tool ?? '') && !tool.done ? { ...tool, ref: payload.ref ?? '', done: true } : tool,
              ),
            }
          } else if (payload.kind === 'error') {
            turn = turn && { ...turn, finished: 'error' }
            turnError = payload.text ? busyText(payload.text) : '这一轮出错了'
          } else if (payload.kind === 'cancelled') {
            turn = turn && { ...turn, finished: 'cancelled' }
          }
        },
        aborting.signal,
      )
    } catch (cause) {
      if ((cause as Error)?.name !== 'AbortError') {
        turnError = errorText(cause)
        turn = turn && { ...turn, finished: 'error' }
      }
    } finally {
      running = false
      aborting = null
    }
  }

  async function cancelTurn() {
    if (!aborting) return
    turnNotice = '已取消'
    turn = turn && { ...turn, finished: 'cancelled' }
    aborting.abort()
    try {
      await post(`/api/chats/${chatId}/cancel`, {})
    } catch {
      // the stream already ends locally; the server cancel is best effort
    }
  }
</script>

<Section title="对话" level={2}>
  {#snippet actions()}
    {#if chats.length}
      <Select.Root type="single" bind:value={chatId} onValueChange={(value) => value && selectChat(value)}>
        <Select.Trigger aria-label="选择对话" size="sm">
          {chats.find((row) => row.chat_id === chatId)?.title ?? chatId}
        </Select.Trigger>
        <Select.Content>
          {#each chats as row (row.chat_id)}
            <Select.Item value={row.chat_id} label={row.read_only ? `${row.title}（只读）` : row.title} />
          {/each}
        </Select.Content>
      </Select.Root>
    {/if}
    <Button size="sm" variant="outline" onclick={newChat}>新开对话</Button>
  {/snippet}

  <div class="grid gap-3">
    {#if chat?.read_only}
      <Alert variant="info"><AlertDescription>该对话在切换运行时后变为只读，可看不可写。</AlertDescription></Alert>
    {/if}
    {#if !chats.length}
      <p class="text-sm text-muted-foreground">暂无对话，点「新开对话」开始。</p>
    {/if}

    <ul class="grid gap-2">
      {#each messages as message (message.seq)}
        <li class="grid gap-0.5">
          <span class="text-xs text-muted-foreground">{message.role === 'user' ? '我' : '助手'}</span>
          <p class="whitespace-pre-line text-sm">{message.content}</p>
          {#if message.tool_refs?.length}
            <p class="text-xs text-muted-foreground">调用 {message.tool_refs.join('、')}</p>
          {/if}
        </li>
      {/each}
    </ul>

    {#if turn}
      <div class="grid gap-1 rounded-md border border-border bg-card p-3">
        <div class="flex items-center gap-2">
          <span class="text-xs text-muted-foreground">助手</span>
          {#if turn.finished === 'running'}<span class="text-xs text-muted-foreground">回答中…</span>{/if}
        </div>
        {#if turn.text}<p class="whitespace-pre-line text-sm">{turn.text}</p>{/if}
        {#each turn.tools as tool (tool.id)}
          <Collapsible.Root
            open={openTools[tool.id] ?? false}
            onOpenChange={(value) => (openTools = { ...openTools, [tool.id]: value })}
          >
            <Collapsible.Trigger class="text-xs text-muted-foreground underline-offset-2 hover:underline">
              {tool.name}{tool.done ? '' : '（调用中…）'}
            </Collapsible.Trigger>
            {#if openTools[tool.id]}
              <Collapsible.Content forceMount class="px-2 text-xs text-muted-foreground">
                {tool.ref || '暂无调用编号'}
              </Collapsible.Content>
            {/if}
          </Collapsible.Root>
        {/each}
      </div>
    {/if}

    {#if turnNotice}<p class="text-sm" role="status">{turnNotice}</p>{/if}
    {#if turnError}
      <Alert variant="danger"><AlertDescription>{turnError}</AlertDescription></Alert>
    {/if}

    <form
      class="grid gap-2"
      onsubmit={(event) => {
        event.preventDefault()
        send()
      }}
    >
      <Field label="提问" id="chat-input">
        {#snippet children({ id })}
          <Textarea {id} rows={2} bind:value={input} disabled={chat?.read_only ?? false} placeholder={chats.length ? '问点什么…' : '先新开一段对话'} />
        {/snippet}
      </Field>
      <div class="flex gap-2">
        <Button type="submit" variant="primary" disabled={running || !chatId || chat?.read_only === true || input.trim() === ''}>发送</Button>
        {#if running}
          <Button type="button" variant="outline" onclick={cancelTurn}>取消</Button>
        {/if}
      </div>
    </form>
  </div>
</Section>
