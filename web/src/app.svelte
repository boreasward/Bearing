<script lang="ts">
  import { onMount } from 'svelte'
  import { applyTheme } from './lib/theme'
  import { parseHash } from './lib/router'
  import * as Tooltip from './lib/ui/tooltip/index'
  import { Toaster } from './lib/ui/sonner/index'
  import Home from './routes/Home.svelte'

  let route = $state(parseHash(location.hash))

  // Dev-only component gallery at #_ui: with DEV false the whole branch, including the dynamic import,
  // is removed from the production bundle.
  const loadGallery = import.meta.env.DEV ? () => import('./routes/UiGallery.svelte') : null

  onMount(() => {
    applyTheme()
    const onhashchange = () => { route = parseHash(location.hash) }
    window.addEventListener('hashchange', onhashchange)
    return () => window.removeEventListener('hashchange', onhashchange)
  })
</script>

<Tooltip.Provider>
  <main class="mx-auto min-h-screen w-full max-w-[1400px] bg-background p-4 text-foreground">
    {#if loadGallery && route === 'gallery'}
      {#await loadGallery() then gallery}
        <gallery.default />
      {/await}
    {:else}
      <Home />
    {/if}
  </main>
</Tooltip.Provider>
<Toaster />
