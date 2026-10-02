import { useState } from 'react'
import AuthShell, { AccountTabs } from '../components/AuthShell'
import Field from '../components/Field'
import Icon from '../components/Icon'
import { sentenceCase } from '../text'

export default function InstructorLogin({ error, onMenu, onStudent, onSignIn, onDismissError }) {
  const [id, setId] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)

  function change(setter) {
    return (event) => {
      setter(event.target.value)
      if (error) onDismissError?.()
    }
  }

  return (
    <AuthShell tagline="Instructor console" onMenu={onMenu}>
      <form
        className="auth-card"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          onSignIn({ id, password })
        }}
      >
        <AccountTabs active="instructor" onStudent={onStudent} />
        <h2 className="auth-title">Instructor Login</h2>
        <p className="auth-sub">Sign in to review student progress and reports.</p>

        <div className="auth-fields">
          <Field
            large
            id="instructor-id"
            label="Instructor ID"
            icon="user"
            placeholder="Enter instructor ID"
            autoComplete="username"
            value={id}
            onChange={change(setId)}
          />
          <Field
            large
            id="instructor-password"
            label="Password"
            icon="lock"
            type={reveal ? 'text' : 'password'}
            placeholder="Enter password"
            autoComplete="current-password"
            // The prototype accepts any credentials; say so plainly rather
            // than implying accounts are issued or checked.
            hint="Prototype access: any ID and password are accepted, and the session ends on refresh."
            value={password}
            onChange={change(setPassword)}
            end={
              <button
                type="button"
                className="icon-btn is-quiet"
                aria-label={reveal ? 'Hide password' : 'Show password'}
                aria-pressed={reveal}
                onClick={() => setReveal((shown) => !shown)}
              >
                <Icon name={reveal ? 'eye-off' : 'eye'} size={20} />
              </button>
            }
          />
        </div>

        {error ? (
          <div className="notice is-danger" role="alert" style={{ marginTop: 16 }}>
            <Icon name="warning" size={14} />
            {sentenceCase(error)}
          </div>
        ) : null}

        <div className="auth-actions">
          <button type="submit" className="btn btn-primary btn-lg btn-block">
            Sign in
            <Icon name="arrow-right" size={16} />
          </button>
        </div>
      </form>
    </AuthShell>
  )
}
