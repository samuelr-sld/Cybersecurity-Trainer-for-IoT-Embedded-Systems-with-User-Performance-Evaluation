import { useState } from 'react'
import RoleSelect from './screens/RoleSelect'
import StudentAccess from './screens/StudentAccess'
import ProfessorAccess from './screens/ProfessorAccess'
import MainMenu from './screens/MainMenu'
import HackMode from './screens/HackMode'
import BuildMode from './screens/BuildMode'
import Dashboard from './screens/Dashboard'
import { STUDENTS } from './data'
import './App.css'

const seedRegistry = [{ name: 'Juan Dela Cruz', id: '2021-04213' }]

export default function App() {
  const [screen, setScreen] = useState('role')
  const [role, setRole] = useState(null)
  const [registry, setRegistry] = useState(seedRegistry)
  const [student, setStudent] = useState(null)
  const [evalStudent, setEvalStudent] = useState(null)
  const [professor, setProfessor] = useState(null)
  const [error, setError] = useState('')
  const [overlay, setOverlay] = useState(null)
  const [menuOpen, setMenuOpen] = useState(false)

  function goRole() {
    setScreen('role')
    setRole(null)
    setStudent(null)
    setProfessor(null)
    setEvalStudent(null)
    setError('')
    setMenuOpen(false)
  }

  function studentRecord(user) {
    return STUDENTS.find((s) => s.id === user.id) || { ...STUDENTS[0], ...user }
  }

  const view = (() => {
    if (screen === 'role') {
      return (
        <RoleSelect
          onMenu={() => setMenuOpen(true)}
          onStudent={() => {
            setRole('student')
            setError('')
            setScreen('student-access')
          }}
          onProfessor={() => {
            setRole('professor')
            setError('')
            setScreen('professor-access')
          }}
        />
      )
    }
    if (screen === 'student-access') {
      return (
        <StudentAccess
          error={error}
          onMenu={() => setMenuOpen(true)}
          onBack={goRole}
          onEnter={({ id, name }) => {
            const found = registry.find(
              (s) => s.id.trim() === id.trim() && s.name.trim().toLowerCase() === name.trim().toLowerCase(),
            )
            if (!found) {
              setError('No matching student. Register first, or try Juan Dela Cruz / 2021-04213.')
              return
            }
            setStudent(found)
            setError('')
            setScreen('menu')
          }}
          onRegister={({ id, name }) => {
            if (!id.trim() || !name.trim()) {
              setError('Full name and student number are required.')
              return
            }
            if (registry.some((s) => s.id.trim() === id.trim())) {
              setError('That student number is already registered.')
              return
            }
            const next = { name: name.trim(), id: id.trim() }
            setRegistry((r) => [...r, next])
            setStudent(next)
            setError('')
            setScreen('menu')
          }}
        />
      )
    }
    if (screen === 'professor-access') {
      return (
        <ProfessorAccess
          signedIn={Boolean(professor)}
          error={error}
          onMenu={() => setMenuOpen(true)}
          onBack={goRole}
          onSignIn={({ id, password }) => {
            if (!id.trim() || !password.trim()) {
              setError('Professor ID and password are required.')
              return
            }
            setProfessor({ id: id.trim() })
            setError('')
          }}
          onView={(row) => {
            if (!professor) {
              setError('Sign in with a Professor ID to view evaluations.')
              return
            }
            setEvalStudent(row)
            setScreen('dashboard')
          }}
        />
      )
    }
    if (screen === 'menu' && student) {
      return (
        <MainMenu
          student={student}
          onMenu={() => setMenuOpen(true)}
          onHack={() => setScreen('hack')}
          onBuild={() => setScreen('build')}
          onEval={() => {
            setEvalStudent(studentRecord(student))
            setScreen('dashboard')
          }}
          onFooter={(key) => {
            if (key === 'power') goRole()
            else setOverlay(key)
          }}
        />
      )
    }
    if (screen === 'hack') {
      return (
        <HackMode
          onMenu={() => setMenuOpen(true)}
          onBack={() => setScreen('menu')}
          onBuild={() => setScreen('build')}
        />
      )
    }
    if (screen === 'build') {
      return <BuildMode onMenu={() => setMenuOpen(true)} onBack={() => setScreen('menu')} />
    }
    if (screen === 'dashboard' && evalStudent) {
      return (
        <Dashboard
          student={evalStudent}
          role={role}
          onMenu={() => setMenuOpen(true)}
          onBack={() => setScreen(role === 'professor' ? 'professor-access' : 'menu')}
          onNext={(next) => setEvalStudent(next)}
        />
      )
    }
    return (
      <RoleSelect
        onMenu={() => setMenuOpen(true)}
        onStudent={() => setScreen('student-access')}
        onProfessor={() => setScreen('professor-access')}
      />
    )
  })()

  return (
    <>
      {view}
      {overlay ? (
        <div className="overlay" role="dialog">
          <div className="overlay-card">
            <h2>
              {overlay === 'modules' && 'Module Selector'}
              {overlay === 'guide' && 'Activity Guide'}
              {overlay === 'settings' && 'Settings'}
            </h2>
            {overlay === 'modules' && (
              <ul>
                <li>Weak MQTT Auth (current)</li>
                <li>GPIO Control Abuse</li>
                <li>TLS Misconfiguration</li>
                <li>Firmware OTA Abuse</li>
                <li>Hardcoded Secrets</li>
              </ul>
            )}
            {overlay === 'guide' && (
              <ol>
                <li>Discover the isolated broker from Hack Mode.</li>
                <li>Document the missing authentication finding.</li>
                <li>Remediate firmware in Build Mode, then validate.</li>
                <li>Review metrics on Evaluate Student.</li>
              </ol>
            )}
            {overlay === 'settings' && <p>Sandbox network: Isolated · Host: Pi5 · Broker: Mosquitto</p>}
            <button type="button" className="btn-solid" onClick={() => setOverlay(null)}>
              Close
            </button>
          </div>
        </div>
      ) : null}
      {menuOpen ? (
        <div className="overlay" role="dialog">
          <div className="overlay-card">
            <h2>Menu</h2>
            <button type="button" className="btn-outline" onClick={goRole}>
              Role Selection
            </button>
            {student ? (
              <button type="button" className="btn-outline" onClick={() => { setMenuOpen(false); setScreen('menu') }}>
                Main Menu
              </button>
            ) : null}
            <button type="button" className="btn-solid" onClick={() => setMenuOpen(false)}>
              Close
            </button>
          </div>
        </div>
      ) : null}
    </>
  )
}
