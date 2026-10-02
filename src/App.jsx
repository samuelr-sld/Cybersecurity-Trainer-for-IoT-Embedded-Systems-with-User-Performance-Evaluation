import { useEffect, useRef, useState } from 'react'
import StudentAccess from './screens/StudentAccess'
import ProfessorAccess from './screens/ProfessorAccess'
import MainMenu from './screens/MainMenu'
import HackMode from './screens/HackMode'
import BuildMode from './screens/BuildMode'
import ModePreparation from './screens/ModePreparation'
import Dashboard from './screens/Dashboard'
import MenuDrawer from './components/MenuDrawer'
import Logo from './components/Logo'
import { fetchSessionLive, registerParticipant, signInParticipant } from './api/trainerApi'
import {
  forgetActiveMode,
  forgetSession,
  recallActiveMode,
  recallSession,
  rememberActiveMode,
} from './session/activeSession'
import { createModeLifecycle, endRememberedSession } from './session/modeLifecycle'
import './App.css'

// Students are registered participants held by the backend
// (backend/app/participants.py). The signed-in student's number is passed to
// Hack/Build Mode so every recorded session is attributed to them, which is
// what the Evaluation page reads back.
function toStudent(participant) {
  return { id: participant.participant_id, name: participant.full_name }
}

// A reload destroys every component but not the backend's Hack/Build session.
// If this tab was inside a mode, it comes back through the 'resuming' screen,
// which asks the backend whether that session is still running before opening
// the mode again (see the effects in App below).
function restoredMode() {
  return recallActiveMode()
}

export default function App() {
  const [restored] = useState(restoredMode)
  // 'student-access' is the sign-in screen: the Student / Instructor tabs on it
  // switch between the two access screens.
  const [screen, setScreen] = useState(restored ? 'resuming' : 'student-access')
  const [role, setRole] = useState('student')
  const [student, setStudent] = useState(restored ? restored.student : null)
  const [evalStudent, setEvalStudent] = useState(null)
  // The professor's list the current evaluation was opened from, for NEXT.
  const [evalList, setEvalList] = useState([])
  const [professor, setProfessor] = useState(null)
  const [error, setError] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  // Which mode is being prepared, and which attempt this is. The attempt is
  // the preparation screen's `key`: RETRY remounts it, so every attempt gets
  // a fresh `/ws/prepare` connection and a fresh checklist.
  const [preparation, setPreparation] = useState(null)

  // Resume after a reload: only if the backend still has the session. Anything
  // else (no remembered id, session expired or ended, backend unreachable)
  // lands on the student's menu, never on a mode that would silently start a
  // new session without its baseline preparation.
  useEffect(() => {
    if (!restored) return undefined
    let cancelled = false
    const sessionId = recallSession(restored.mode)
    const live = sessionId
      ? fetchSessionLive(restored.mode, sessionId).catch(() => false)
      : Promise.resolve(false)
    live.then((isLive) => {
      if (cancelled) return
      if (isLive) {
        setScreen(restored.mode)
      } else {
        forgetSession(restored.mode)
        setScreen('menu')
      }
    })
    return () => {
      cancelled = true
    }
  }, [restored])

  // Every entry into Hack Mode or Build Mode goes through the preparation
  // screen, which restores the panel's vulnerable baseline firmware
  // (backend/app/mode_preparation.py). The 'hack' / 'build' screens are only
  // ever set by its `onReady`, so a failed preparation cannot open a mode.
  function prepareMode(mode) {
    setPreparation((current) => ({ mode, attempt: (current?.attempt ?? 0) + 1 }))
    setScreen('prepare')
  }

  // QUIT and RESET (src/session/modeLifecycle.js). Both end the CURRENT
  // backend session first; QUIT then shows the menu, RESET enters the same
  // mode again through `prepareMode` — so the new session starts, as every one
  // does, from the restored baseline, on a freshly mounted mode screen that
  // inherits nothing of the old one. A reload is neither: it ends nothing and
  // resumes. The screen effect below reports every screen change to it, which
  // is how it knows the student has already left by another route.
  const [lifecycle] = useState(() =>
    createModeLifecycle({
      endMode: endRememberedSession,
      toMenu: () => setScreen('menu'),
      restartMode: prepareMode,
      screen,
    }),
  )

  // Keep the reload pointer in step with the screen, and end the backend
  // session when the student leaves a mode on purpose (any screen change away
  // from hack/build: QUIT, RESET, the menu drawer, the Hack -> Build link,
  // sign-out). Closing the socket never ends a session — a reload must be able
  // to come back — so leaving is this explicit request. A reload runs none of
  // this: the page is simply gone, which is exactly why it does not end the
  // session. Ending is idempotent (`endRememberedSession` forgets the pointer
  // first), so QUIT/RESET having already ended it makes this a no-op.
  const previousScreen = useRef(screen)
  useEffect(() => {
    const previous = previousScreen.current
    previousScreen.current = screen
    lifecycle.screenChanged(screen)
    if (previous !== screen && (previous === 'hack' || previous === 'build')) {
      void endRememberedSession(previous)
    }
    if ((screen === 'hack' || screen === 'build') && student) {
      rememberActiveMode(screen, student)
    } else if (screen !== 'resuming') {
      forgetActiveMode()
    }
  }, [screen, student, lifecycle])

  // Back to the sign-in screen with nobody signed in.
  function goSignIn() {
    setScreen('student-access')
    setRole('student')
    setStudent(null)
    setProfessor(null)
    setEvalStudent(null)
    setError('')
    setMenuOpen(false)
  }

  function enterAsStudent(participant) {
    setStudent(toStudent(participant))
    setError('')
    setScreen('menu')
  }

  const view = (() => {
    if (screen === 'resuming') {
      return (
        <div className="screen screen-dotted resume-screen">
          <Logo width={96} />
          <p aria-live="polite">Restoring your session…</p>
        </div>
      )
    }
    if (screen === 'professor-access') {
      return (
        <ProfessorAccess
          signedIn={Boolean(professor)}
          professorId={professor?.id}
          error={error}
          onMenu={() => setMenuOpen(true)}
          onDismissError={() => setError('')}
          onStudent={() => {
            setRole('student')
            setError('')
            setScreen('student-access')
          }}
          onSignOut={goSignIn}
          onSignIn={({ id, password }) => {
            if (!id.trim() || !password.trim()) {
              setError('Instructor ID and password are required.')
              return
            }
            setProfessor({ id: id.trim() })
            setError('')
          }}
          onView={(row, list) => {
            if (!professor) {
              setError('Sign in with an Instructor ID to view evaluations.')
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
          onQuit={() => lifecycle.quit('hack')}
          onReset={() => lifecycle.reset('hack')}
          onBuild={() => prepareMode('build')}
        />
      )
    }
    if (screen === 'build') {
      return (
        <BuildMode
          participantId={student?.id}
          onMenu={() => setMenuOpen(true)}
          onQuit={() => lifecycle.quit('build')}
          onReset={() => lifecycle.reset('build')}
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
      <StudentAccess
        error={error}
        onMenu={() => setMenuOpen(true)}
        onDismissError={() => setError('')}
        onInstructor={() => {
          setRole('professor')
          setError('')
          setScreen('professor-access')
        }}
        onEnter={({ id }) => {
          if (!id.trim()) {
            setError('Student number is required.')
            return
          }
          signInParticipant({ id })
            .then(enterAsStudent)
            .catch((e) =>
              setError(
                e.status === 404 && !e.endpointMissing
                  ? 'No student is registered with that number. Register first.'
                  : e.message,
              ),
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
  })()

  return (
    <>
      {view}
      {menuOpen ? (
        <MenuDrawer
          student={student}
          professor={professor}
          onClose={() => setMenuOpen(false)}
          onMain={() => {
            setMenuOpen(false)
            setScreen('menu')
          }}
          onSignOut={goSignIn}
          onInstructor={() => {
            setMenuOpen(false)
            setRole('professor')
            setError('')
            setScreen('professor-access')
          }}
        />
      ) : null}
    </>
  )
}
