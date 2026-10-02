// The browser Fullscreen API, reduced to the three questions the control asks.
// Pure over a `document`-shaped object so Node's test runner can drive it. The
// whole page (documentElement) goes fullscreen, never a single panel, so a
// screen change inside the app does not leave fullscreen.

/** Can this browser enter fullscreen at all (API present and not disabled)? */
export function isFullscreenSupported(doc) {
  return Boolean(doc && doc.fullscreenEnabled && doc.documentElement?.requestFullscreen)
}

/** Is the page fullscreen right now, as the browser reports it? */
export function isFullscreen(doc) {
  return Boolean(doc && doc.fullscreenElement)
}

/**
 * Enter fullscreen, or leave it if already there. Must run from a user
 * gesture (the browser refuses otherwise). Resolves once the browser has
 * switched; rejects if it refuses, in which case nothing changed.
 */
export function toggleFullscreen(doc) {
  if (isFullscreen(doc)) return Promise.resolve(doc.exitFullscreen())
  return Promise.resolve(doc.documentElement.requestFullscreen({ navigationUI: 'hide' }))
}

/** The label a control should carry for the current state. */
export function fullscreenLabel(active) {
  return active ? 'Exit fullscreen' : 'Enter fullscreen'
}
