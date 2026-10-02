import FullscreenToggle from './FullscreenToggle'
import Icon from './Icon'
import Logo from './Logo'

/**
 * The top bar every post-sign-in screen shares: menu button + screen title on
 * the left, the CT mark centred, and the screen's own actions on the right,
 * ending with the fullscreen control (the same place on every screen).
 * Hardware status deliberately does NOT live here — it is the status bar's job
 * (components/StatusBar.jsx) so it reads the same in every mode.
 */
export default function AppHeader({ title, badge, meta, actions, onMenu }) {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <button type="button" className="icon-btn" aria-label="Open menu" onClick={onMenu}>
          <Icon name="menu" size={28} />
        </button>
        {title ? <h1 className="topbar-title">{title}</h1> : null}
        {badge ? <span className="topbar-badge">{badge}</span> : null}
      </div>
      <Logo width={46} />
      <div className="topbar-right">
        {meta ? <div className="topbar-meta">{meta}</div> : null}
        {actions}
        <FullscreenToggle />
      </div>
    </header>
  )
}
