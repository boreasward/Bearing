<script lang="ts">
  import { onMount } from 'svelte'
  import { FEATURE_NAMES, getFeatures, setFeature, type FeatureState, type FeatureChange } from '$lib/features'
  import { describeError, newRequestId } from '$lib/workbench'
  import PageHeader from '$lib/ui/domain/PageHeader.svelte'
  import Section from '$lib/ui/domain/Section.svelte'
  import StatusPill from '$lib/ui/domain/StatusPill.svelte'
  import CutoffTime from '$lib/ui/domain/CutoffTime.svelte'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import DataTable from '$lib/ui/data-table/DataTable.svelte'
  import type { Column } from '$lib/ui/data-table/types'
  import ConfirmDialog from '$lib/ui/confirm-dialog/ConfirmDialog.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import { Textarea } from '$lib/ui/textarea/index'
  import { Button } from '$lib/ui/button/index'
  let history = $state<FeatureChange[]>([])
  let flags = $state<FeatureState[]>([]), loading = $state(true), error = $state('')
  let target = $state<FeatureState | null>(null), open = $state(false), reason = $state(''), busy = $state(false), saveError = $state(''), saved = $state(false)
  let requestId = ''
  async function load() {
    loading = true; error = ''
    try { const next = await getFeatures(); flags = next.flags; history = next.history } catch (e) { error = describeError(e) } finally { loading = false }
  }
  onMount(load)
  function prepare(row: FeatureState) { target = row; reason = ''; saveError = ''; requestId = newRequestId(); open = true }
  async function save() {
    if (busy || !target || !reason.trim()) return
    busy = true; saveError = ''
    try { await setFeature(target.flag, { enabled: !target.enabled, reason: reason.trim(), request_id: requestId }); open = false; saved = true; await load() }
    catch (e) { saveError = describeError(e) } finally { busy = false }
  }
  const columns: Column<FeatureState>[] = [
    { key: 'flag', header: '功能', value: r => FEATURE_NAMES[r.flag] }, { key: 'enabled', header: '当前状态', cell: state },
    { key: 'changed_at', header: '最近变更', cell: changed }, { key: 'reason', header: '理由', value: r => r.reason ?? '默认开启' },
    { key: 'history_count', header: '历史条数', align: 'num' }, { key: 'actions', header: '操作', cell: action },
  ]
  const historyColumns: Column<FeatureChange>[] = [
    { key: 'flag', header: '功能', value: r => FEATURE_NAMES[r.flag] }, { key: 'change', header: '变更', cell: change },
    { key: 'reason', header: '理由' }, { key: 'created_at', header: '时间', cell: historyTime },
  ]
</script>
{#snippet state(r: FeatureState)}<StatusPill domain="feature_flag" value={r.enabled ? 'enabled' : 'disabled'} />{/snippet}
{#snippet changed(r: FeatureState)}{#if r.changed_at}<CutoffTime at={r.changed_at} label="变更于" />{:else}<span>默认值</span>{/if}{/snippet}
{#snippet action(r: FeatureState)}<Button variant="outline" size="sm" aria-label={`${r.enabled ? '关闭' : '开启'}${FEATURE_NAMES[r.flag]}`} onclick={() => prepare(r)}>{r.enabled ? '关闭' : '开启'}</Button>{/snippet}
{#snippet change(r: FeatureChange)}<span>{r.enabled_before == null ? '默认' : r.enabled_before ? '已开启' : '已关闭'} → {r.enabled_after ? '已开启' : '已关闭'}</span>{/snippet}
{#snippet historyTime(r: FeatureChange)}<CutoffTime at={r.created_at} label="变更于" />{/snippet}
<div class="grid gap-4">
  <PageHeader title="功能开关">{#snippet meta()}<span>下次 daily 生效；本次运行与续跑沿用冻结值</span>{/snippet}</PageHeader>
  {#if saved}<p role="status" class="text-sm">变更已保存，下次运行生效</p>{/if}
  <DataBlock state={loading ? 'loading' : error ? 'error' : flags.length ? 'ready' : 'empty'} message={error || '接口未返回功能开关'} onRetry={load}>
    <DataTable {columns} rows={flags} rowKey={r => r.flag} ariaLabel="功能开关" />
    <Section title="变更历史" level={3}>
      <DataBlock state={history.length ? 'ready' : 'empty'} message="尚无变更记录">
        <DataTable columns={historyColumns} rows={history} rowKey={r => r.id} ariaLabel="开关变更历史" />
      </DataBlock>
    </Section>
  </DataBlock>
</div>
<ConfirmDialog bind:open title={`${target?.enabled ? '关闭' : '开启'}${target ? FEATURE_NAMES[target.flag] : '功能'}`} consequence="开关变更会写入历史，从下次 daily 生效。" actionLabel={target?.enabled ? '关闭功能' : '开启功能'} destructive={target?.enabled} {busy} confirmDisabled={!reason.trim()} onConfirm={save}>
  <Field label="变更理由" id="feature-reason" hint="开启和关闭都需填写理由，最多 300 字">{#snippet children({ id, describedby })}<Textarea {id} bind:value={reason} maxlength={300} aria-describedby={describedby} />{/snippet}</Field>
  {#if saveError}<DataBlock state="error" message={saveError} />{/if}
</ConfirmDialog>
