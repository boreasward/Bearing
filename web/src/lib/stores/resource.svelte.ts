import { untrack } from 'svelte'
import { createSubscriber } from 'svelte/reactivity'
import { get } from '$lib/api'

export interface Resource<T> {
  readonly data: T | null
  readonly state: 'loading' | 'ready' | 'error' | 'empty'
  readonly error: string
  load(): Promise<void>
  invalidate(): void
}

function errorMessage(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : String(cause)
  const chinese = message.match(/^[a-z_]+:\s*(.*[\u3400-\u9fff].*)$/s)
  if (chinese) return chinese[1]
  if (message === 'no_brief') return '今天还没有简报'
  if (message === 'no_run') return '还没有运行记录'
  if (message === 'unknown_call') return '找不到这次调用记录'
  if (message === 'HTTP 401') return '请先登录后重试'
  if (cause instanceof TypeError) return '网络连接失败，请稍后重试'
  return '服务暂时不可用，请稍后重试'
}

/** Read getters in a component/effect to auto-load and subscribe; load() also works outside a component. */
export function createResource<T>(path: string | (() => string | null), opts: { errorDetails?: boolean } = {}): Resource<T> {
  const resolve = () => typeof path === 'function' ? path() : path
  let data = $state.raw<T | null>(null)
  let state = $state<Resource<T>['state']>(untrack(resolve) === null ? 'empty' : 'loading')
  let error = $state('')
  let dirty = true
  let active = false
  let request = 0
  let loadedPath: string | null | undefined

  async function load(): Promise<void> {
    const target = resolve()
    const current = ++request
    dirty = false
    loadedPath = target
    error = ''
    if (target === null) {
      data = null
      state = 'empty'
      return
    }
    state = 'loading'
    try {
      const result = await get<T>(target, { errorDetails: opts.errorDetails ?? true })
      if (current !== request || target !== resolve()) return
      data = result
      state = result === null ? 'empty' : 'ready'
    } catch (cause) {
      if (current !== request || target !== resolve()) return
      error = errorMessage(cause)
      state = 'error'
    }
  }

  const subscribe = createSubscriber(() => {
    active = true
    const stop = $effect.root(() => {
      $effect(() => {
        const target = resolve()
        untrack(() => { if (dirty || loadedPath !== target) void load() })
      })
    })
    return () => { active = false; stop() }
  })

  return {
    get data() { subscribe(); return data },
    get state() { subscribe(); return state },
    get error() { subscribe(); return error },
    load,
    invalidate() {
      dirty = true
      ++request
      if (active) void untrack(load)
    },
  }
}
