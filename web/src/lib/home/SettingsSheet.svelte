<script lang="ts">
  import * as Sheet from '$lib/ui/sheet/index'
  import * as Select from '$lib/ui/select/index'
  import { Button } from '$lib/ui/button/index'
  import DataBlock from '$lib/ui/domain/DataBlock.svelte'
  import Field from '$lib/ui/field/Field.svelte'
  import Input from '$lib/ui/input/input.svelte'
  import { get, put } from '$lib/api'
  import { notify } from '$lib/ui/toast'
  import { errorText, type Settings, type SettingsOptions } from './types'

  // 设置面板（spec §12 / v3 设计 §15.4）：运行时、提供方、模型；选项来自
  // GET /api/settings/options，每项可在下拉与自由输入间切换；保存 PUT /api/settings。
  let { open = $bindable(false), onSaved }: { open?: boolean; onSaved?: () => void } = $props()

  let loaded = $state(false)
  let loadError = $state('')
  let draft = $state({ runtime: '', provider: '', model: '' })
  let options = $state<SettingsOptions | null>(null)
  let freeInput = $state({ runtime: false, provider: false, model: false })
  let saving = $state(false)

  async function load() {
    loadError = ''
    try {
      const [current, opts] = await Promise.all([
        get<Settings>('/api/settings'),
        get<SettingsOptions>('/api/settings/options'),
      ])
      draft = { runtime: current.runtime ?? '', provider: current.provider ?? '', model: current.model ?? '' }
      options = opts
      loaded = true
    } catch (cause) {
      loadError = errorText(cause)
    }
  }

  $effect(() => {
    if (open && !loaded && !loadError) load()
  })

  async function save() {
    if (saving) return
    saving = true
    try {
      await put('/api/settings', { ...draft })
      notify.success('设置已保存')
      open = false
      onSaved?.()
    } catch (cause) {
      notify.error(errorText(cause))
    } finally {
      saving = false
    }
  }

  const modelOptions = $derived(options?.models[draft.provider] ?? [])
  const ready = $derived(draft.runtime.trim() !== '' && draft.provider.trim() !== '' && draft.model.trim() !== '')
  const FIELDS: Array<keyof typeof draft> = ['runtime', 'provider', 'model']
</script>

<Sheet.Root bind:open>
  <Sheet.Content>
    <Sheet.Header>
      <Sheet.Title>运行时与模型</Sheet.Title>
      <Sheet.Description>保存后新开的对话与之后的简报用新设置；已有对话变为只读。</Sheet.Description>
    </Sheet.Header>
    <div class="grid gap-4 px-4 pb-4">
      {#if loadError}
        <DataBlock state="error" message={loadError} onRetry={load} />
      {:else if !options}
        <DataBlock state="loading" skeleton="row" />
      {:else}
        {#each FIELDS as key (key)}
          {@const label = key === 'runtime' ? '运行时' : key === 'provider' ? '提供方' : '模型'}
          {@const items = key === 'runtime' ? options.runtimes : key === 'provider' ? options.providers : modelOptions}
          <Field label={label} id={`settings-${key}`} required>
            {#snippet children({ id })}
              <div class="flex items-center gap-2">
                {#if freeInput[key] || !items.length}
                  <Input {id} class="w-full" bind:value={draft[key]} />
                {:else}
                  <Select.Root type="single" bind:value={draft[key]}>
                    <Select.Trigger {id} class="w-full">{draft[key] || `选择${label}`}</Select.Trigger>
                    <Select.Content>
                      {#each items as item (item)}
                        <Select.Item value={item} label={item} />
                      {/each}
                    </Select.Content>
                  </Select.Root>
                {/if}
                {#if items.length}
                  <Button
                    size="sm"
                    variant="link"
                    onclick={() => (freeInput[key] = !freeInput[key])}>{freeInput[key] ? '从列表选择' : '直接输入'}</Button
                  >
                {/if}
              </div>
            {/snippet}
          </Field>
        {/each}
        <Button variant="primary" busy={saving} disabled={!ready} onclick={save}>保存</Button>
      {/if}
    </div>
  </Sheet.Content>
</Sheet.Root>
