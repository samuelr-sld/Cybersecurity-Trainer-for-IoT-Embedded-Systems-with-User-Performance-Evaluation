import AppHeader from '../components/AppHeader'
import InfoBanner from '../components/InfoBanner'

export default function RoleSelect({ onStudent, onProfessor, onMenu }) {
  return (
    <div className="page">
      <AppHeader
        title="IoT CYBERSECURITY SANDBOX"
        right={
          <>
            <span className="dot ok" /> PI5 HOST ONLINE <span className="pipe">|</span> MQTT
            BROKER: RUNNING
          </>
        }
        onMenu={onMenu}
      />
      <main className="page-body welcome">
        <h2 className="welcome-title">WELCOME</h2>
        <p className="welcome-sub">Select your account type to continue.</p>
        <div className="role-grid">
          <article className="role-card">
            <div className="role-icon" aria-hidden="true">
              <svg viewBox="0 0 64 64" width="72" height="72">
                <circle cx="32" cy="22" r="10" fill="none" stroke="currentColor" strokeWidth="2" />
                <path d="M16 54c2-14 12-18 16-18s14 4 16 18" fill="none" stroke="currentColor" strokeWidth="2" />
                <path d="M20 18h24l-4-8H24z" fill="none" stroke="currentColor" strokeWidth="2" />
              </svg>
            </div>
            <h3>STUDENT</h3>
            <p>Learn, hack, and build. Track your own performance.</p>
            <button type="button" className="btn-solid" onClick={onStudent}>
              CONTINUE AS STUDENT
            </button>
          </article>
          <article className="role-card">
            <div className="role-icon" aria-hidden="true">
              <svg viewBox="0 0 64 64" width="72" height="72">
                <circle cx="32" cy="20" r="10" fill="none" stroke="currentColor" strokeWidth="2" />
                <path d="M14 56c3-16 12-20 18-20s15 4 18 20" fill="none" stroke="currentColor" strokeWidth="2" />
                <rect x="24" y="32" width="16" height="10" rx="1" fill="none" stroke="currentColor" strokeWidth="2" />
              </svg>
            </div>
            <h3>PROFESSOR</h3>
            <p>Review and evaluate student performance.</p>
            <button type="button" className="btn-solid" onClick={onProfessor}>
              CONTINUE AS PROFESSOR
            </button>
          </article>
        </div>
      </main>
      <InfoBanner>
        This system is for educational use in a sandboxed environment only. All network activity is
        isolated and monitored.
      </InfoBanner>
    </div>
  )
}
