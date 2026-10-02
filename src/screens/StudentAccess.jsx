import { useState } from 'react'
import AuthShell, { AccountTabs } from '../components/AuthShell'
import Field from '../components/Field'
import Icon from '../components/Icon'
import { sentenceCase } from '../text'

// A student is a registered participant: a full name plus a student number
// (backend/app/participants.py). There is no password — the number is the
// identity, and the backend validates both.
const NUMBER_HINT = 'Letters, digits and hyphens, up to 32 characters.'

export default function StudentAccess({ error, onMenu, onInstructor, onEnter, onRegister, onDismissError }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ id: '', name: '' })
  const registering = mode === 'register'

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
    if (error) onDismissError?.()
  }

  function switchMode(next) {
    setMode(next)
    onDismissError?.()
  }

  function submit(event) {
    event.preventDefault()
    if (registering) onRegister(form)
    else onEnter(form)
  }

  return (
    <AuthShell
      tagline={registering ? 'Create your training account' : 'Hands-on IoT security training'}
      onMenu={onMenu}
    >
      <form className="auth-card" onSubmit={submit} noValidate>
        {registering ? null : <AccountTabs active="student" onInstructor={onInstructor} />}
        <h2 className="auth-title">{registering ? 'Register' : 'Login'}</h2>
        <p className="auth-sub">
          {registering
            ? 'Your progress is saved to this account.'
            : 'Welcome back. Sign in to resume your training.'}
        </p>

        <div className="auth-fields">
          <Field
            large
            id="student-name"
            label="Full name"
            icon="user"
            placeholder="Enter your full name"
            autoComplete="name"
            value={form.name}
            onChange={(event) => update('name', event.target.value)}
          />
          <Field
            large
            id="student-number"
            label="Student number"
            icon="id-card"
            placeholder="e.g. 2023-123456"
            autoComplete="off"
            hint={NUMBER_HINT}
            value={form.id}
            onChange={(event) => update('id', event.target.value)}
          />
        </div>

        {error ? (
          <div className="notice is-danger" role="alert" style={{ marginTop: 16 }}>
            <Icon name="warning" size={14} />
            {sentenceCase(error)}
          </div>
        ) : null}

        <div className="auth-actions">
          {registering ? (
            <>
              <button type="submit" className="btn btn-primary btn-lg btn-block">
                Register
              </button>
              <button type="button" className="text-link auth-back" onClick={() => switchMode('login')}>
                <Icon name="arrow-left" size={14} />
                Back to login
              </button>
            </>
          ) : (
            <>
              <button type="submit" className="btn btn-primary btn-lg btn-block">
                Sign in
                <Icon name="arrow-right" size={16} />
              </button>
              <button type="button" className="btn btn-dark btn-lg btn-block" onClick={() => switchMode('register')}>
                Register
              </button>
            </>
          )}
        </div>
        {registering ? null : (
          <p className="auth-note">Your sessions are recorded under your student number for evaluation.</p>
        )}
      </form>
    </AuthShell>
  )
}
