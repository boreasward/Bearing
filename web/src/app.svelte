<script lang="ts">
  import { onMount } from 'svelte'
  import { applyTheme } from './lib/theme'
  import * as Tooltip from './lib/ui/tooltip/index'
  import { Toaster } from './lib/ui/sonner/index'
  import AppShell from './lib/components/AppShell.svelte'
  import Tools from './routes/Tools.svelte'
  import Settings from './routes/Settings.svelte'
  import Today from './routes/Today.svelte'
  import Instrument from './routes/Instrument.svelte'
  import Review from './routes/Review.svelte'
  import Account from './routes/Account.svelte'
  import Data from './routes/Data.svelte'
  import Knowledge from './routes/Knowledge.svelte'
  import Plan from './routes/Plan.svelte'
  import Discussion from './lib/components/Discussion.svelte'
  import { guardHashNavigation } from './lib/navigation-guard'
  import { parseHash, type Zone } from './lib/router'

  function redirectLegacyReview() {
    if (!/^#review\/(trades|account)(?:\?|$)/.test(location.hash)) return
    const route = parseHash(location.hash)
    history.replaceState(null, '', `#${route.zone}/${route.sub}`)
  }
  redirectLegacyReview()
  let { zone, sub } = $state(parseHash(location.hash))
  let hash = $state(location.hash)

  // Dev-only component gallery at #_ui. It is not a route (lib/router.ts is untouched); with DEV false the
  // whole branch, including the dynamic import, is removed from the production bundle.
  const loadGallery = import.meta.env.DEV ? () => import('./routes/UiGallery.svelte') : null

  onMount(() => {
    applyTheme()
    let previousHash = location.hash
    const onhashchange = (event: HashChangeEvent) => {
      if (!guardHashNavigation(event, previousHash)) return
      redirectLegacyReview()
      previousHash = location.hash
      hash = location.hash
      const parsed = parseHash(location.hash)
      zone = parsed.zone
      sub = parsed.sub
    }
    window.addEventListener('hashchange', onhashchange)
    return () => window.removeEventListener('hashchange', onhashchange)
  })
</script>

<Tooltip.Provider>
  <AppShell route={zone} {sub}>
    {#if loadGallery && hash === '#_ui'}
      {#await loadGallery() then gallery}
        <gallery.default />
      {/await}
    {:else if zone === 'today'}
      <Today />
    {:else if zone === 'instrument'}
      <Instrument {sub} />
    {:else if zone === 'review'}
      <Review {sub} />
    {:else if zone === 'account'}
      <Account {sub} />
    {:else if zone === 'data'}
      <Data {sub} />
    {:else if zone === 'knowledge'}
      <Knowledge {sub} />
    {:else if zone === 'tools'}
      <Tools {sub} />
    {:else if zone === 'settings'}
      <Settings {sub} />
    {:else if zone === 'plan'}
      <Plan sub={sub!} />
    {:else if zone === 'discuss'}
      {#key sub}<Discussion id={Number(sub)} />{/key}
    {/if}
  </AppShell>
</Tooltip.Provider>
<Toaster />
