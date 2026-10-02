import { APP_VERSION } from '../appInfo'
import BackendStatusPill from './BackendStatusPill'
import Icon from './Icon'
import Logo from './Logo'

/**
 * The frame the sign-in, registration and instructor sign-in screens share:
 * menu button, the CT mark with a one-line tagline, the screen's card, and the
 * backend-status pill + release label underneath.
 */
export default function AuthShell({ tagline, onMenu, children }) {
  return (
    <div className="screen auth-screen">
      <button type="button" className="icon-btn auth-menu" aria-label="Open menu" onClick={onMenu}>
        <Icon name="menu" size={32} />
      </button>
      <header className="auth-brand">
        <Logo width={132} />
        <p className="auth-tagline">{tagline}</p>
      </header>
      {children}
      <footer className="auth-footer">
        <BackendStatusPill />
        <span className="version">V {APP_VERSION}</span>
      </footer>
    </div>
  )
}

/**
 * The Student | Instructor switch at the top of a sign-in card. The tab for the
 * screen you are on needs no handler (it is already selected).
 */
export function AccountTabs({ active, onStudent, onInstructor }) {
  return (
    <div className="seg-group" role="group" aria-label="Account type">
      <button
        type="button"
        className={`seg${active === 'student' ? ' is-on' : ''}`}
        aria-pressed={active === 'student'}
        onClick={onStudent}
      >
        <Icon name="user" size={16} />
        Student
      </button>
      <button
        type="button"
        className={`seg${active === 'instructor' ? ' is-on' : ''}`}
        aria-pressed={active === 'instructor'}
        onClick={onInstructor}
      >
        <Icon name="chalkboard-user" size={18} />
        Instructor
      </button>
    </div>
  )
}
