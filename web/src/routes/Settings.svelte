<script lang="ts">
  import { Button } from '$lib/ui/button/index'
  import * as Card from '$lib/ui/card/index'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import type { Column } from '$lib/ui/data-table/types'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Icon from '$lib/ui/domain/Icon.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import { Input } from '$lib/ui/input/index'
  import TabNav from '$lib/ui/tab-nav/TabNav.svelte'
  import { notify as toast } from '$lib/ui/toast'
  import * as api from '../lib/api'
  import Watchlist from '../lib/components/Watchlist.svelte'
  import AgentConnection from '../lib/components/AgentConnection.svelte'
  import FeatureFlags from '../lib/components/settings/FeatureFlags.svelte'

  let { sub = 'system' }: { sub?: string } = $props()
  let loading = $state(true)

  // system
  let status = $state<any>(null)
  let ops = $state<any[]>([])

  // notify（Discord / 邮件）
  let notify = $state<any>({
    channel: 'discord',
    webhooks: {},
    task_channels: {},
    task_enabled: {},
    discord_enabled: true,
    email: { from_addr: '', to_addr: '' },
  })
  let webhookInput = $state<Record<string, string>>({ earnings: '', signals: '' })
  // result of a "验证" / "发送测试邮件" call: the pill says 成功 / 失败, `detail` is the reason of a failure
  let testResult = $state<Record<string, { ok: boolean; detail: string }>>({})
  let saving = $state(false)

  // jobs
  let jobs = $state<any[]>([])

  // env
  let env = $state<Record<string, boolean>>({})

  async function load() {
    loading = true
    try {
      if (sub === 'system') {
        ;[status, ops] = await Promise.all([
          api.getStatus(),
          api.getOperations().then((r: any) => r.operations ?? []),
        ])
      } else if (sub === 'watchlist' || sub === 'features') {
        // Shared editor owns its read-back lifecycle.
      } else if (sub === 'discord') {
        ;[notify, env] = await Promise.all([api.getNotifySettings(), api.getEnvStatus()])
        if (!notify.email) notify.email = { from_addr: '', to_addr: '' }
        webhookInput = { earnings: '', signals: '' }
        testResult = {}
      } else if (sub === 'jobs') {
        jobs = (await api.getScheduledJobs()).jobs
      } else {
        env = await api.getEnvStatus()
      }
    } finally {
      loading = false
    }
  }

  // Discord actions
  async function verify(ch: string) {
    const url = webhookInput[ch]?.trim() || undefined
    const r = await api.testNotifyChannel({ channel: ch, url })
    testResult = { ...testResult, [ch]: { ok: !!r.ok, detail: r.ok ? '' : String((r as any).error ?? '失败') } }
  }

  async function verifyEmail() {
    const r = await api.testNotifyChannel({ channel: 'email', email: notify.email })
    testResult = { ...testResult, email: { ok: !!r.ok, detail: r.ok ? '' : String((r as any).error ?? '失败') } }
  }

  async function saveNotify() {
    if (saving) return
    saving = true
    try {
      await persistNotify()
      toast.success('已保存')
    } finally {
      saving = false
    }
  }

  async function persistNotify() {
    const webhooks: Record<string, string> = {}
    for (const ch of ['earnings', 'signals']) {
      if (webhookInput[ch]?.trim()) webhooks[ch] = webhookInput[ch]
    }
    await api.patchNotifySettings({
      channel: notify.channel,
      discord_enabled: notify.discord_enabled,
      webhooks,
      email: notify.email,
      task_enabled: notify.task_enabled,
      task_channels: notify.task_channels,
    })
    webhookInput = { earnings: '', signals: '' }
    await load()
  }

  // Jobs actions
  async function toggleJob(name: string, enabled: boolean) {
    await api.patchScheduledJob(name, { enabled })
    await load()
  }

  async function saveTime(name: string, time_local: string) {
    await api.patchScheduledJob(name, { time_local })
    await load()
  }

  $effect(() => { sub; load() })

  const SETTINGS_TABS = [
    { id: 'system', label: '系统' }, { id: 'watchlist', label: '关注列表' }, { id: 'discord', label: '推送配置' },
    { id: 'jobs', label: '定时任务' }, { id: 'env', label: '环境变量' },
    { id: 'features', label: '功能开关' },
  ]
  const tabs = $derived(SETTINGS_TABS.map(t => ({ href: `#settings/${t.id}`, label: t.label, current: sub === t.id || (t.id === 'env' && !SETTINGS_TABS.some(x => x.id === sub)) })))

  type Op = { i: number; type?: string; run_id?: string; status?: string }
  const opRows = $derived<Op[]>(ops.slice(0, 20).map((o, i) => ({ ...o, i })))
  const opColumns: Column<Op>[] = [
    { key: 'type', header: '操作' }, { key: 'run_id', header: '运行', value: o => o.run_id?.slice(0, 12) ?? '无' },
    { key: 'status', header: '状态', cell: opStatus },
  ]
  const jobColumns: Column<any>[] = [
    { key: 'name', header: '任务' }, { key: 'time', header: '时间', cell: jobTime }, { key: 'enabled', header: '启用', cell: jobEnabled },
  ]
  type EnvRow = { key: string; set: boolean }
  const envColumns: Column<EnvRow>[] = [
    { key: 'key', header: '变量', cell: envKey }, { key: 'set', header: '状态', cell: envState },
  ]
  const envRows = $derived(Object.entries(env).map(([key, set]) => ({ key, set })))
</script>

{#snippet opStatus(op: Op)}<StatusPill domain="system" value={op.status ?? 'unknown'} />{/snippet}
{#snippet jobTime(j: any)}
  {#if j.manual_only}
    <!-- 只能手动运行的任务不在 scheduled_jobs 里：没有时间，也没有启用开关 -->
    <span class="text-xs text-muted-foreground">只手动运行，不参与定时调度</span>
  {:else}
    <span class="inline-flex items-center gap-2">
      <Input value={j.time_local} aria-label={`${j.name} 运行时间`} class="w-24"
             onchange={(e) => saveTime(j.name, (e.target as HTMLInputElement).value)} />
      <span class="text-xs text-muted-foreground">{j.timezone === 'Asia/Shanghai' ? '北京' : j.timezone}</span>
    </span>
  {/if}
{/snippet}
{#snippet jobEnabled(j: any)}
  {#if j.manual_only}
    <span class="text-xs text-muted-foreground">不适用</span>
  {:else}
    <input type="checkbox" checked={j.enabled} aria-label={`${j.name} 启用`}
           onchange={(e) => toggleJob(j.name, (e.target as HTMLInputElement).checked)} />
  {/if}
{/snippet}
{#snippet envKey(row: EnvRow)}<span class="font-mono text-xs">{row.key}</span>{/snippet}
{#snippet envState(row: EnvRow)}
  {#if row.set}<span class="inline-flex items-center gap-1 text-success"><Icon name="check" />已设置</span>
  {:else}<span class="text-muted-foreground">未设置</span>{/if}
{/snippet}

{#snippet testBadge(result: { ok: boolean; detail: string })}
  <span class="inline-flex items-center gap-2 text-xs">
    <StatusPill domain="run" value={result.ok ? 'success' : 'failed'} text={result.ok ? '成功' : '失败'} />
    {#if !result.ok && result.detail && result.detail !== '失败'}<span class="text-muted-foreground">{result.detail}</span>{/if}
  </span>
{/snippet}

<div class="grid gap-4">
<TabNav label="设置" items={tabs} />
{#if loading}
  <DataBlock state="loading" skeleton="table" />
{:else if sub === 'system'}
  <div class="grid gap-4">
    <Section title="系统" level={2}>
      {#if status}
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
          {#each Object.entries(status as any) as [k, v]}
            {#if typeof v === 'object' && v !== null && 'status' in v}
              <Card.Root size="sm">
                <Card.Content class="grid gap-1">
                  <div class="text-xs text-muted-foreground">{k}</div>
                  <div><StatusPill domain="system" value={(v as any).status ?? 'unknown'} /></div>
                </Card.Content>
              </Card.Root>
            {/if}
          {/each}
        </div>
      {/if}
      {#if ops.length > 0}
        <Section title="最近操作" level={3}>
          <DataTable columns={opColumns} rows={opRows} rowKey={o => o.i} ariaLabel="最近操作" />
        </Section>
      {/if}
    </Section>
    <!-- v2.8 §8.9：Agent 连接区块放在「系统」之后 -->
    <div class="mt-4">
      <AgentConnection />
    </div>
  </div>
{:else if sub === 'watchlist'}
  <Watchlist />
{:else if sub === 'features'}
  <FeatureFlags />
{:else if sub === 'discord'}
  <div class="grid max-w-lg gap-4">
    <h2 class="text-lg font-medium">通知</h2>
    <div class="flex gap-4 text-sm">
      <label class="flex items-center gap-1">
        <input type="radio" bind:group={notify.channel} value="discord" /> Discord
      </label>
      <label class="flex items-center gap-1">
        <input type="radio" bind:group={notify.channel} value="email" /> 邮件
      </label>
    </div>
    {#if notify.channel === 'email'}
      <div class="grid gap-3">
        <Field label="发件地址（Gmail）" id="notify-email-from">
          {#snippet children({ id })}<Input {id} bind:value={notify.email.from_addr} placeholder="you@gmail.com" />{/snippet}
        </Field>
        <Field label="收件地址" id="notify-email-to">
          {#snippet children({ id })}<Input {id} bind:value={notify.email.to_addr} placeholder="you@outlook.com" />{/snippet}
        </Field>
        <div class="flex items-center gap-1 text-xs {env.SMTP_PASSWORD ? 'text-success' : 'text-destructive'}">
          {#if env.SMTP_PASSWORD}<Icon name="check" />{:else}<Icon name="x" />{/if}
          <span>SMTP_PASSWORD {env.SMTP_PASSWORD ? '已配置' : '未配置——需以环境变量注入 Gmail 应用专用密码'}</span>
        </div>
        <div class="flex items-center gap-3">
          <Button size="sm" onclick={verifyEmail}>发送测试邮件</Button>
          {#if testResult.email}{@render testBadge(testResult.email)}{/if}
        </div>
      </div>
    {:else}
      <label class="flex items-center gap-2 text-sm">
        <input type="checkbox" bind:checked={notify.discord_enabled} />
        启用 Discord 推送
      </label>
      {#each ['earnings', 'signals'] as ch}
        <div class="grid gap-1">
          <div class="flex items-end gap-2">
            <div class="min-w-0 flex-1">
              <Field label={ch} id={`notify-webhook-${ch}`} hint={notify.webhooks?.[ch]?.configured ? '已配置' : '未配置'}>
                {#snippet children({ id, describedby })}
                  <Input {id} type="password" bind:value={webhookInput[ch]} aria-describedby={describedby} placeholder="留空则不修改" />
                {/snippet}
              </Field>
            </div>
            <Button size="sm" onclick={() => verify(ch)}>验证 {ch}</Button>
          </div>
          {#if testResult[ch]}{@render testBadge(testResult[ch])}{/if}
        </div>
      {/each}
    {/if}
    <div class="pt-2">
      <Button variant="primary" busy={saving} onclick={saveNotify}>保存</Button>
    </div>
    <p class="text-xs text-muted-foreground">webhook 明文不回显；留空字段不会覆盖已存值。SMTP 密码只能通过环境变量配置。</p>
  </div>
{:else if sub === 'jobs'}
  <Section title="定时任务" level={2}>
    <DataTable columns={jobColumns} rows={jobs} rowKey={j => j.name} ariaLabel="定时任务" />
  </Section>
{:else}
  <Section title="环境变量" level={2}>
    <DataTable columns={envColumns} rows={envRows} rowKey={r => r.key} ariaLabel="环境变量" />
  </Section>
{/if}
</div>
