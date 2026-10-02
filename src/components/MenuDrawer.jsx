import { useEffect, useRef } from 'react'
import Icon from './Icon'

/**
 * The hamburger menu. Everything it offers is a real navigation: the main menu
 * (for a signed-in student), signing out, or — before anyone is signed in — the
 * two sign-in screens. Closes on Escape, on the backdrop and on its own close
 * button; focus starts on the close button.
 */
export default function MenuDrawer({ student, professor, onClose, onMain, onSignOut, onInstructor }) {
  const closeRef = useRef(null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const identity = student
    ? { name: student.name, detail: `Student ${student.id}` }
    : professor
      ? { name: 'Instructor', detail: professor.id }
      : null

  return (
    <div
      className="overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <nav className="drawer" role="dialog" aria-modal="true" aria-label="Menu">
        <div className="drawer-head">
          <h2>Menu</h2>
          <button ref={closeRef} type="button" className="icon-btn" aria-label="Close menu" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </div>
        {student ? (
          <button type="button" className="nav-item" onClick={onMain}>
            <Icon name="nav-overview" size={18} />
            Main menu
          </button>
        ) : null}
        {student || professor ? (
          <button type="button" className="nav-item" onClick={onSignOut}>
            <Icon name="arrow-left" size={16} />
            Sign out
          </button>
        ) : (
          <>
            <button type="button" className="nav-item" onClick={onSignOut}>
              <Icon name="user" size={16} />
              Student sign in
            </button>
            <button type="button" className="nav-item" onClick={onInstructor}>
              <Icon name="chalkboard-user" size={18} />
              Instructor sign in
            </button>
          </>
        )}
        {identity ? (
          <p className="drawer-foot">
            Signed in as <strong>{identity.name}</strong>
            <br />
            <span className="mono">{identity.detail}</span>
          </p>
        ) : null}
      </nav>
    </div>
  )
}
