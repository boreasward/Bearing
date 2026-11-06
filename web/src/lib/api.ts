export async function get<T>(path: string, options: { errorDetails?: boolean } = {}): Promise<T> {
  const r = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' })
  if (!r.ok) {
    let message = `HTTP ${r.status}`
    // With `errorDetails`, the error / message / detail field of the response body becomes the error message.
    if (options.errorDetails) {
      const data = await r.json().catch(() => null)
      const detail = [data?.error, data?.message, data?.detail].find(
        value => typeof value === 'string' && value.trim(),
      )
      if (detail) message = detail
    }
    throw new Error(message)
  }
  return r.json() as Promise<T>
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await r.json()
  if (!r.ok) throw new Error(data?.error ?? `HTTP ${r.status}`)
  return data as T
}

export async function del<T>(path: string): Promise<T> {
  const r = await fetch(path, { method: 'DELETE', headers: { Accept: 'application/json' } })
  const data = await r.json()
  if (!r.ok) throw new Error(data?.error ?? `HTTP ${r.status}`)
  return data as T
}

export async function patch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'PATCH',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await r.json()
  if (!r.ok) throw new Error(data?.error ?? `HTTP ${r.status}`)
  return data as T
}

export async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await r.json()
  if (!r.ok) throw new Error(data?.error ?? `HTTP ${r.status}`)
  return data as T
}
