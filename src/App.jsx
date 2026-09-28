import { useState } from 'react'
import RoleSelect from './screens/RoleSelect'
import StudentAccess from './screens/StudentAccess'
import ProfessorAccess from './screens/ProfessorAccess'
import MainMenu from './screens/MainMenu'
import HackMode from './screens/HackMode'
import BuildMode from './screens/BuildMode'
import ModePreparation from './screens/ModePreparation'
import Dashboard from './screens/Dashboard'
import { registerParticipant, signInParticipant } from './api/trainerApi'
import './App.css'

// Students are registered participants held by the backend
// (backend/app/participants.py). The signed-in student's number is passed to
// Hack/Build Mode so every recorded session is attributed to them, which is
// what the Evaluation page reads back.
function toStudent(participant) {
  return { id: participant.participant_id, name: participant.full_name }
}

export default function App() {
  const [screen, setScreen] = useState('role')
  const [role, setRole] = useState(null)
  const [student, setStudent] = useState(null)
  const [evalStudent, setEvalStudent] = useState(null)
  // The professor's list the current evaluation was opened from, for NEXT.
  const [evalList, setEvalList] = useState([])
  const [professor, setProfessor] = useState(null)
  const [error, setError] = useState('')
  const [overlay, setOverlay] = useState(null)
  const [menuOpen, setMenuOpen] = useState(false)
  // Which mode is being prepared, and which attempt this is. The attempt is
  // the preparation screen's `key`: RETRY remounts it, so every attempt gets
  // a fresh `/ws/prepare` connection and a fresh checklist.
  const [preparation, setPreparation] = useState(null)

  function goRole() {
    setScreen('role')
    setRole(null)
    setStudent(null)
    setProfessor(null)
    setEvalStudent(null)
    setError('')
    setMenuOpen(false)
  }

  // Every entry into Hack Mode or Build Mode goes through the preparation
  // screen, which restores the panel's vulnerable baseline firmware
  // (backend/app/mode_preparation.py). The 'hack' / 'build' screens are only
  // ever set by its `onReady`, so a failed preparation cannot open a mode.
  function prepareMode(mode) {
    setPreparation((current) => ({ mode, attempt: (current?.attempt ?? 0) + 1 }))
    setScreen('prepare')
  }

  function enterAsStudent(participant) {
    setStudent(toStudent(participant))
    setError('')
    setScreen('menu')
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
            if (!id.trim() || !name.trim()) {
              setError('Student number and full name are required.')
              return
            }
            signInParticipant({ id, name })
              .then(enterAsStudent)
              .catch((e) =>
                setError(e.status === 404 && !e.endpointMissing ? 'No matching student. Register first.' : e.message),
              )
          }}
          onRegister={({ id, name }) => {
            if (!id.trim() || !name.trim()) {
              setError('Full name and student number are required.')
              return
            }
            registerParticipant({ id, name })
              .then(enterAsStudent)
              .catch((e) => setError(e.message))
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
          onView={(row, list) => {
            if (!professor) {
              setError('Sign in with a Professor ID to view evaluations.')
              return
            }
            setEvalList(list)
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
          onHack={() => prepareMode('hack')}
          onBuild={() => prepareMode('build')}
          onEval={() => {
            setEvalList([])
            setEvalStudent(student)
            setScreen('dashboard')
          }}
          onFooter={(key) => {
            if (key === 'power') goRole()
            else setOverlay(key)
          }}
        />
      )
    }
    if (screen === 'prepare' && preparation) {
      return (
        <ModePreparation
          key={preparation.attempt}
          mode={preparation.mode}
          onMenu={() => setMenuOpen(true)}
          onReady={() => setScreen(preparation.mode)}
          onRetry={() => prepareMode(preparation.mode)}
          onBack={() => setScreen('menu')}
        />
      )
    }
    if (screen === 'hack') {
      return (
        <HackMode
          participantId={student?.id}
          onMenu={() => setMenuOpen(true)}
          onBack={() => setScreen('menu')}
          onBuild={() => prepareMode('build')}
        />
      )
    }
    if (screen === 'build') {
      return (
        <BuildMode
          participantId={student?.id}
          onMenu={() => setMenuOpen(true)}
          onBack={() => setScreen('menu')}
        />
      )
    }
    if (screen === 'dashboard' && evalStudent) {
      const idx = evalList.findIndex((s) => s.id === evalStudent.id)
      return (
        <Dashboard
          key={evalStudent.id}
          student={evalStudent}
          nextStudent={idx >= 0 ? evalList[idx + 1] ?? null : null}
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
