<script lang="ts">
  // `第 k 版`; the newest says 「最新」, an older one links to the newest. `ref` (`concept:<id>@<v>` / `know:<id>@<v>`)
  // is the native tooltip and kept as data-ref.
  let { version, latest = null, href, latestHref, ref }:
    { version: number | string; latest?: number | null; href?: string; latestHref?: string; ref?: string } = $props()

  const isLatest = $derived(latest != null && latest === version)
  const isOlder = $derived(typeof version === 'number' && latest != null && latest > version)
  const label = $derived(typeof version === 'number' ? `第 ${version} 版` : version)
</script>

<span class="inline-flex items-baseline gap-1.5" title={ref} data-ref={ref}>
  {#if href}<a {href} class="text-primary underline underline-offset-2">{label}</a>{:else}<span>{label}</span>{/if}
  {#if isLatest}
    <span class="text-xs text-muted-foreground">最新</span>
  {:else if isOlder}
    {#if latestHref}<a href={latestHref} class="text-xs text-primary underline underline-offset-2">最新第 {latest} 版</a>{:else}<span class="text-xs text-muted-foreground">最新第 {latest} 版</span>{/if}
  {/if}
</span>
