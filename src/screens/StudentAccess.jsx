import { useState } from 'react'
import AppHeader from '../components/AppHeader'
import InfoBanner from '../components/InfoBanner'

export default function StudentAccess({ onBack, onEnter, onRegister, error, onMenu }) {
  const [signIn, setSignIn] = useState({ id: '', name: '' })
  const [reg, setReg] = useState({ id: '', name: '' })

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
      <div className="subnav">
        <button type="button" className="text-link" onClick={onBack}>
          ← BACK TO ROLE SELECTION
        </button>
        <h2>STUDENT ACCESS</h2>
        <span />
      </div>
      <main className="page-body">
        {error ? <p className="form-error">{error}</p> : null}
        <div className="split-auth">
          <form
            className="auth-card"
            onSubmit={(e) => {
              e.preventDefault()
              onEnter(signIn)
            }}
          >
            <div className="auth-tabs">
              <span className="tab active">SIGN IN</span>
            </div>
            <label>
              STUDENT NUMBER
              <input
                value={signIn.id}
                placeholder="Enter student number"
                onChange={(e) => setSignIn({ ...signIn, id: e.target.value })}
              />
            </label>
            <label>
              FULL NAME
              <input
                value={signIn.name}
                placeholder="Enter your full name"
                onChange={(e) => setSignIn({ ...signIn, name: e.target.value })}
              />
            </label>
            <button type="submit" className="btn-solid">
              ENTER SANDBOX
            </button>
          </form>
          <div className="or-badge">OR</div>
          <form
            className="auth-card"
            onSubmit={(e) => {
              e.preventDefault()
              onRegister(reg)
            }}
          >
            <div className="auth-tabs">
              <span className="tab active">REGISTER STUDENT</span>
            </div>
            <label>
              FULL NAME
              <input
                value={reg.name}
                placeholder="Enter your full name"
                onChange={(e) => setReg({ ...reg, name: e.target.value })}
              />
            </label>
            <label>
              STUDENT NUMBER
              <input
                value={reg.id}
                placeholder="Enter student number"
                onChange={(e) => setReg({ ...reg, id: e.target.value })}
              />
            </label>
            <button type="submit" className="btn-solid">
              REGISTER STUDENT
            </button>
          </form>
        </div>
      </main>
      <InfoBanner>
        For this prototype, sign-in and registration are simulated and stored locally in your
        browser. Data will reset on page refresh.
      </InfoBanner>
    </div>
  )
}
