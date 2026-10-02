import { APP_VERSION } from '../appInfo'

/**
 * The footer every screen shares: the screen's status readout on the left
 * (the PANEL | USB | CONNECTED hardware line in the modes, the hub status on
 * the instructor screens) and the release label on the right.
 */
export default function StatusBar({ children }) {
  return (
    <footer className="statusbar">
      <div className="statusbar-left">{children}</div>
      <span className="version">V {APP_VERSION}</span>
    </footer>
  )
}
