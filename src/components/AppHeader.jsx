export default function AppHeader({ title, right, onMenu }) {
  return (
    <header className="app-header">
      <div className="app-header-left">
        <button type="button" className="icon-btn" aria-label="Menu" onClick={onMenu}>
          ☰
        </button>
        <span className="brand-mark" aria-hidden="true">
          ⌖
        </span>
        <h1>{title}</h1>
      </div>
      <div className="status-chip">{right}</div>
    </header>
  )
}
