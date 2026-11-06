export type Route = 'home' | 'gallery'

/** `#_ui` is the component gallery; every other hash (including none) is the home page. */
export function parseHash(hash: string): Route {
  return hash.replace(/^#/, '').split('?')[0] === '_ui' ? 'gallery' : 'home'
}
