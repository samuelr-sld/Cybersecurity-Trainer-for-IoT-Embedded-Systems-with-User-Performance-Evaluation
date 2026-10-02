import Icon from './Icon'
import useFullscreen from '../hooks/useFullscreen'
import { fullscreenLabel } from '../fullscreen/fullscreenModel'

/**
 * Enter / exit fullscreen with the browser Fullscreen API. Always an explicit
 * tap: nothing in the app enters fullscreen by itself. The icon and label
 * follow the browser's own state (it also changes by Esc), and the control is
 * simply absent where the browser cannot do fullscreen.
 */
export default function FullscreenToggle({ className }) {
  const { supported, active, toggle } = useFullscreen()
  if (!supported) return null
  const label = fullscreenLabel(active)
  return (
    <button
      type="button"
      className={className ? `icon-btn fullscreen-toggle ${className}` : 'icon-btn fullscreen-toggle'}
      aria-label={label}
      title={label}
      onClick={toggle}
    >
      <Icon name={active ? 'fullscreen-exit' : 'fullscreen-enter'} size={22} />
    </button>
  )
}
