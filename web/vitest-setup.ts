import '@testing-library/jest-dom'

// jsdom lacks ResizeObserver
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// jsdom lacks these Element methods; Bits UI (focus scope, select, dialog) calls them.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {}
}
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = function hasPointerCapture() { return false }
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = function releasePointerCapture() {}
}

// Node >= 25 ships an experimental Web Storage global that is `undefined` unless --localstorage-file is given, and it
// shadows jsdom's. Fall back to the jsdom window's storage (same on any Node version).
for (const name of ['localStorage', 'sessionStorage'] as const) {
  if (typeof (globalThis as { [k: string]: unknown })[name] !== 'undefined') continue
  const storage = (globalThis as unknown as { jsdom?: { window: Window } }).jsdom?.window[name]
  if (storage) Object.defineProperty(globalThis, name, { value: storage, configurable: true, writable: true })
}
