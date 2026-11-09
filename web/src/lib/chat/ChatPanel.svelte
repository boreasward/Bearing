<script lang="ts">
  import { onDestroy, tick } from 'svelte'
  import { get, post } from '$lib/api'
  import { chats, invalidateAll } from '$lib/stores'
  import { isMutatingTool } from '$lib/views/tool-write'
  import { Button } from '$lib/ui/button/index'
  import Field from '$lib/ui/field/Field.svelte'
  import Textarea from '$lib/ui/textarea/textarea.svelte'
  import { Alert, AlertDescription } from '$lib/ui/alert/index'
  import { errorText, type ChatMessage, type ChatRow } from '$lib/home/types'
  import { formatPrefix, parsePrefix, type ContextChip } from './context'
  import { readEvents } from './sse'
  import type { Block, Turn } from './types'
  import TurnView from './Turn.svelte'
  import ContextChips from './ContextChips.svelte'
  import QuickPrompts from './QuickPrompts.svelte'
  import ChatEmpty from './ChatEmpty.svelte'
  import { notify } from '$lib/ui/toast'
  let { chatId, prefillChips, prefillText, onWrote }: {
    chatId: string | null; prefillChips?: ContextChip[]; prefillText?: { text: string; seq: number }; onWrote(): void
  } = $props()
  type Item = { id: number; role: 'user'; text: string } | { id: number; role: 'assistant'; turn: Turn; history: boolean }
  type Conversation = { chat: ChatRow | null; items: Item[]; loading: boolean; notice: string; error: string; feedback: string[] }
  let conversations = $state<Record<string, Conversation>>({})
  let selected = $state<string | null>(null)
  const current = $derived(selected ? conversations[selected] : undefined)
  let input = $state('')
  let chips = $state<ContextChip[]>([])
  let inputEl = $state<HTMLTextAreaElement | null>(null)
  let focusPending = $state(false)
  let prefillSeq: number | undefined
  let seenChips: ContextChip[] | undefined
  let sequence = 0
  let active = $state<{ chatId: string; controller: AbortController; turn: Turn } | null>(null)
  let cancelPending = $state(false)
  let sendPending = $state(false)
  let creating = $state(false)
  const busy = $derived(sendPending || creating || !!active || cancelPending)
  $effect(() => { selected = chatId; if (chatId && !conversations[chatId]) void load(chatId) })
  $effect(() => {
    for (const row of chats.data?.chats ?? []) {
      const conv = conversations[row.chat_id]
      if (conv?.chat) conv.chat.read_only = row.read_only
    }
  })
  $effect(() => {
    if (prefillChips && prefillChips !== seenChips) {
      seenChips = prefillChips
      chips = [...chips, ...prefillChips]
      void focusInput()
    }
  })
  $effect(() => {
    if (prefillText && prefillText.seq !== prefillSeq) {
      prefillSeq = prefillText.seq; input = prefillText.text; void focusInput()
    }
  })
  onDestroy(() => active?.controller.abort())
  export function appendFeedback(text: string) { if (current) current.feedback.push(text) }
  export async function focusInput() {
    focusPending = true
    await tick()
  }
  $effect(() => {
    if (!focusPending || current?.loading || !inputEl) return
    inputEl.focus(); focusPending = false
  })

  async function load(id: string) {
    conversations[id] = { chat: null, items: [], loading: true, notice: '', error: '', feedback: [] }
    try {
      const data = await get<{ chat: ChatRow; messages: (ChatMessage & { agent_session?: string | null })[] }>(`/api/chats/${encodeURIComponent(id)}`, { errorDetails: true })
      const conv = conversations[id]
      conv.chat = data.chat
      conv.items = data.messages.map(message => {
        if (message.role === 'user') return { id: ++sequence, role: 'user', text: message.content }
        const blocks: Block[] = (message.tool_refs ?? []).filter(ref => /^call:\d+$/.test(ref)).map(ref => ({ kind: 'tool', id: ++sequence, tool: '调用结果', ref, state: 'ready' }))
        blocks.push({ kind: 'text', text: message.content })
        return { id: ++sequence, role: 'assistant', history: true, turn: { text: message.content, blocks, finished: 'done', agentSession: message.agent_session ?? null } }
      })
    } catch (cause) { conversations[id].error = errorText(cause) }
    finally { conversations[id].loading = false }
  }
  function busyText(code: string): string {
    return ({ brief_running: '正在生成简报，请稍后再问', chat_running: '上一轮回答还在生成，请稍后再问', no_run: '还没有运行记录，稍后再问', chat_read_only: '该对话已只读' } as Record<string, string>)[code] ?? errorText(code)
  }
  export async function createChat(): Promise<string | null> {
    if (creating) return null
    creating = true
    try {
      const created = await post<{ chat_id: string }>('/api/chats', {})
      await load(created.chat_id)
      selected = created.chat_id
      location.hash = `#chat/${encodeURIComponent(created.chat_id)}`
      invalidateAll()
      return created.chat_id
    } catch (cause) {
      notify.error(cause instanceof Error && cause.message === 'no_runtime_selected' ? '还没有选定运行时，请先在设置里选择' : errorText(cause))
      return null
    } finally { creating = false }
  }
  async function send(draft = input) {
    if (!draft.trim() || busy || current?.chat?.read_only) return
    sendPending = true
    try {
      const id = selected ?? await createChat()
      if (!id) return
      const conv = conversations[id]
      if (conv.loading || conv.chat?.read_only) return
      const text = formatPrefix(chips) + draft.trim()
      const sentChips = chips
      input = ''; chips = []; conv.notice = ''; conv.error = ''
      const userId = ++sequence; const assistantId = ++sequence
      conv.items.push({ id: userId, role: 'user', text }, { id: assistantId, role: 'assistant', history: false, turn: { text: '', blocks: [], finished: 'running', agentSession: null } })
      const item = conv.items.at(-1)!
      if (item.role !== 'assistant') return
      const turn = item.turn
      const controller = new AbortController()
      active = { chatId: id, controller, turn }
      let wrote = false
      try {
        const response = await fetch(`/api/chats/${encodeURIComponent(id)}/messages`, { method: 'POST', headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' }, body: JSON.stringify({ text }), signal: controller.signal })
        if (!response.ok) {
          const data = await response.json().catch(() => null) as { error?: string } | null
          conv.notice = busyText(data?.error ?? `HTTP ${response.status}`)
          conv.items = conv.items.filter(item => item.id !== userId && item.id !== assistantId)
          input = draft; chips = sentChips
          return
        }
        await readEvents(response, frame => {
          if (frame.event === 'done') {
            if (turn.finished !== 'running') return
            turn.finished = 'done'
            return
          }
          const payload = JSON.parse(frame.data) as { kind: string; text?: string; tool?: string; ref?: string | null; agent_session?: string }
          if (payload.kind === 'start') turn.agentSession = payload.agent_session ?? null
          else if (payload.kind === 'text') {
            const text = payload.text ?? ''; turn.text += text
            const last = turn.blocks.at(-1)
            if (last?.kind === 'text') last.text += text
            else turn.blocks.push({ kind: 'text', text })
          } else if (payload.kind === 'tool_start') turn.blocks.push({ kind: 'tool', id: ++sequence, tool: payload.tool ?? '', ref: null, state: 'calling' })
          else if (payload.kind === 'tool_end') {
            // Claim only the first still-running call of this tool; repeated calls have separate refs.
            const block = turn.blocks.find(block => block.kind === 'tool' && block.tool === payload.tool && block.state === 'calling')
            if (block?.kind === 'tool') { block.ref = payload.ref || null; block.state = block.ref ? 'ready' : 'untraceable'; wrote ||= isMutatingTool(block.tool) }
          } else if (payload.kind === 'error') { turn.finished = 'error'; conv.error = payload.text ? busyText(payload.text) : '这一轮出错了' }
          else if (payload.kind === 'cancelled') { turn.finished = 'cancelled'; conv.notice = '已取消' }
        }, controller.signal)
        if (turn.finished === 'running') { turn.finished = 'error'; conv.error = '回答连接已结束，请重试' }
      } catch (cause) {
        if ((cause as Error)?.name !== 'AbortError') { conv.error = errorText(cause); turn.finished = 'error' }
      } finally {
        if (turn.finished !== 'done') {
          for (const block of turn.blocks) {
            if (block.kind === 'tool' && block.state === 'calling') {
              block.state = 'error'
              block.error = turn.finished === 'cancelled' ? '已停止等待，调用结果未确认' : '调用未完成'
            }
          }
        }
        if (wrote) { invalidateAll(); onWrote() }
        else if (turn.finished === 'done') chats.invalidate()
        active = null
      }
    } finally { sendPending = false }
  }
  async function cancelTurn() {
    if (!active || cancelPending) return
    cancelPending = true
    const { chatId: id, controller, turn } = active
    turn.finished = 'cancelled'; conversations[id].notice = '已取消'; controller.abort()
    try { await post(`/api/chats/${encodeURIComponent(id)}/cancel`, {}) } catch { /* cancel endpoint is best effort */ }
    finally { cancelPending = false }
  }
  async function research() { input = '研究 '; await focusInput() }
</script>
<div class="flex h-full w-full flex-col">
  <div class="min-h-0 flex-1 overflow-y-auto">
  <div class="mx-auto grid w-full max-w-[760px] content-start gap-[14px] p-4">
  {#if !selected}<ChatEmpty disabled={busy} onNewChat={() => { void createChat() }} />{/if}
  {#if current?.chat?.read_only}<Alert variant="info"><AlertDescription>该对话在切换运行时后变为只读，可看不可写。</AlertDescription></Alert>{/if}
  {#each current?.items ?? [] as item (item.id)}
    {#if item.role === 'user'}
      {@const parsed = parsePrefix(item.text)}
      <div class="grid gap-2 justify-self-end rounded-2xl bg-muted p-3 text-[13px] leading-[1.55]">
        <ContextChips chips={parsed.chips} /><p class="whitespace-pre-line">{parsed.rest}</p>
      </div>
    {:else}<TurnView bind:turn={item.turn} chatId={selected ?? ''} history={item.history} />{/if}
  {/each}
  {#each current?.feedback ?? [] as text}<p role="status" class="text-xs text-muted-foreground">{text}</p>{/each}
  {#if current?.notice}<p role="status" class="text-sm">{current.notice}</p>{/if}
  {#if current?.error}<Alert variant="danger"><AlertDescription>{current.error}</AlertDescription></Alert>{/if}
  </div>
  </div>
  <form class="mx-auto grid w-full max-w-[760px] shrink-0 gap-2 bg-card px-4 pt-3 pb-4" onsubmit={event => { event.preventDefault(); void send() }}>
    <ContextChips {chips} onRemove={index => { chips = chips.filter((_, i) => i !== index) }} />
    <QuickPrompts onSend={text => { input = text; void send(text) }} onResearch={research} disabled={busy || current?.loading || current?.chat?.read_only === true} />
    <Field label="提问" id="chat-input">{#snippet children({ id })}<Textarea {id} rows={2} bind:ref={inputEl} bind:value={input} disabled={current?.loading || current?.chat?.read_only === true} placeholder="问点什么…" />{/snippet}</Field>
    <div class="flex gap-2">
      <Button type="submit" variant="primary" disabled={busy || current?.loading || current?.chat?.read_only === true || !input.trim()}>发送</Button>
      {#if active}<Button type="button" variant="outline" disabled={cancelPending} onclick={cancelTurn}>取消</Button>{/if}
    </div>
  </form>
</div>
