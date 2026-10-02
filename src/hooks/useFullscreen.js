import { useCallback, useSyncExternalStore } from 'react'
import { isFullscreen, isFullscreenSupported, toggleFullscreen } from '../fullscreen/fullscreenModel'

// The browser owns the fullscreen state (it can also change by Esc or a system
// gesture), so it is read from the document and re-read on `fullscreenchange`
// rather than mirrored in component state.
function subscribe(onChange) {
  document.addEventListener('fullscreenchange', onChange)
  return () => document.removeEventListener('fullscreenchange', onChange)
}

const read = () => isFullscreen(document)
const readOnServer = () => false

/** { supported, active, toggle } for the browser Fullscreen API. */
export default function useFullscreen() {
  const active = useSyncExternalStore(subscribe, read, readOnServer)
  // A refusal (no user gesture, blocked by policy) leaves the state as it was,
  // which is exactly what the control then keeps showing.
  const toggle = useCallback(() => {
    toggleFullscreen(document).catch(() => {})
  }, [])
  return { supported: isFullscreenSupported(document), active, toggle }
}
