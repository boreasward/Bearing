<script lang="ts">
  // `口径 review-8`: the calculation version in code style.
  let { version }: { version: string } = $props()
</script>

<span class="text-xs text-muted-foreground">口径
  <code class="rounded-sm bg-muted px-0.5 font-mono">{version}</code></span>
