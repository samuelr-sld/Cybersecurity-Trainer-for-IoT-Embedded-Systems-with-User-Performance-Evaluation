import { useEffect, useRef, useState } from 'react'
import AppHeader from '../components/AppHeader'
import BlocklyWorkspace from '../components/BlocklyWorkspace'
import useBuildSocket, { CONNECTION_STATUS } from '../hooks/useBuildSocket'

// Backend `BuildEventType` values (backend/app/build/events.py) -> Activity
// Log copy. A plain lookup, not a switch, so an event type this map doesn't
// know about (the closed set changed) falls back to the raw value below
// rather than silently dropping the row — the same pattern HackMode.jsx
// uses for ScenarioEventType. The session/edit five (Phase 3A), the three
// compile events (Phase 3B) and the three flash events (Phase 3C) are all
// really emitted; the rest are reserved vocabulary for later phases
// (validation, security/functional testing).
const EVENT_LABELS = {
  build_session_started: 'Build session started',
  workspace_loaded: 'Workspace loaded',
  // Fires for edits to whichever region `BuildProject.security_region_id`
  // names — a plain "primary" region on the current LED Blink project, a
  // real security region on the eventual five-panel projects. Labeled
  // neutrally here so the Activity Log doesn't imply a security framing
  // that this project doesn't have.
  security_region_edited: 'Target region edited',
  code_edited: 'Code edited',
  build_session_ended: 'Build session ended',
  compile_started: 'Compile started',
  compile_failed: 'Compile failed',
  compile_succeeded: 'Compile succeeded',
  flash_started: 'Flash started',
  flash_failed: 'Flash failed',
  flash_succeeded: 'Flash succeeded',
  validation_started: 'Validation started',
  validation_failed: 'Validation failed',
  validation_succeeded: 'Validation succeeded',
  security_test_started: 'Security test started',
  security_test_failed: 'Security test failed',
  security_test_succeeded: 'Security test succeeded',
  functional_test_started: 'Functional test started',
  functional_test_failed: 'Functional test failed',
  functional_test_succeeded: 'Functional test succeeded',
  build_completed: 'Build completed',
}

// When the `/ws/build` socket itself isn't open, nothing about the
// `hardware` block can be trusted (it's whatever was last pushed, possibly
// never) — so the header's LINK falls back to this instead of the hardware
// status. Once the socket is CONNECTED, LINK reflects real ESP32 presence
// (see HARDWARE_LINK_LABEL) rather than the transport.
const SOCKET_LINK_LABEL = {
  [CONNECTION_STATUS.CONNECTING]: 'SOCKET CONNECTING…',
  [CONNECTION_STATUS.DISCONNECTED]: 'SOCKET DISCONNECTED',
  [CONNECTION_STATUS.ERROR]: 'SOCKET ERROR',
}

// Backend `HardwareStatus` values (backend/app/build/models.py) -> header
// copy. Drives the Build Mode header's BOARD/PORT/LINK from the backend's
// own real `arduino-cli board list` discovery (see
// backend/app/build/flasher.py) — never hardcoded, never assumed CONNECTED.
const HARDWARE_LINK_LABEL = {
  not_checked: 'CHECKING…',
  detecting: 'CHECKING…',
  connected: 'CONNECTED',
  disconnected: 'DISCONNECTED',
  ambiguous: 'AMBIGUOUS',
  error: 'ERROR',
}

// How often the frontend asks the backend to re-run device discovery while
// Build Mode stays open — the only way an unplug is ever reflected in the
// header, since nothing pushes an OS-level USB event to this socket. Kept
// deliberately infrequent (see CLAUDE.md: "avoid aggressive polling") next
// to a real `arduino-cli board list` invocation, which is cheap but not
// free.
const HARDWARE_POLL_INTERVAL_MS = 10000

// Backend `CompileStatus`/`FlashStatus`/`ValidationStatus` values (backend/
// app/build/models.py) -> display copy. `detecting` and `no_device` are
// FlashStatus-only: flashing has a physical failure domain compilation does
// not, and "nothing is plugged in" gets its own state rather than being
// folded into FAILED.
const BUILD_STATUS_LABEL = {
  not_started: 'NOT STARTED',
  detecting: 'DETECTING…',
  running: 'RUNNING…',
  no_device: 'NO DEVICE',
  succeeded: 'SUCCEEDED',
  failed: 'FAILED',
}

// Validation/security-test controls are wired to nothing yet — Phase 3C
// implements real flashing, and deliberately stops there: uploading firmware
// is not the same as showing that it works or that it is secure (see
// CLAUDE.md). These stay visibly disabled placeholders rather than claiming
// a result nothing measured. COMPILE and FLASH are both real controls now —
// see the console actions below — so neither is in this list.
const FUTURE_CONTROLS = [
  { key: 'validate', label: 'RUN VALIDATION TEST' },
  { key: 'security_test', label: 'SECURITY TEST' },
]

// Backend `FlashStatus` -> banner copy. NO ESP32 DEVICE DETECTED is
// deliberately worded as a device fact, not a build or toolchain problem.
// FLASH SUCCESS claims exactly what the backend verified: `arduino-cli
// upload` exited 0. It does not claim the firmware runs, works, or is
// secure — nothing in this phase checks that.
const FLASH_BANNER = {
  detecting: 'DETECTING DEVICE…',
  running: 'FLASHING…',
  no_device: '⚠ NO ESP32 DEVICE DETECTED',
  succeeded: '✓ FLASH SUCCESS',
  failed: '✗ FLASH FAILED',
}

// Backend `FlashFailureCategory` -> one plain sentence about what went
// wrong, so a student is not left reading raw esptool output to work out
// which of several very different problems they have.
const FLASH_FAILURE_NOTE = {
  no_device: 'Connect the ESP32 over USB, then flash again.',
  ambiguous_device:
    'More than one candidate serial device is connected. Disconnect all but the intended ESP32, then flash again.',
  device_disconnected: 'The device stopped responding during the upload. Reconnect it and retry.',
  upload_error: 'arduino-cli upload ran and reported an error.',
  timeout: 'The upload exceeded its time limit and was stopped.',
  toolchain_unavailable: 'The Arduino CLI toolchain is not available on the backend.',
  internal_error: 'The backend could not run the upload.',
}

// Activity Log timestamps are SESSION ELAPSED TIME (HH:MM:SS since this
// Build Mode session's own `session` frame arrived — see `sessionStartRef`
// below), never a wall-clock time-of-day. Nothing server-side currently
// stamps a `BuildEvent` with a real timestamp (`backend/app/build/events.py`
// has none), so there is no backend timestamp this discards — this is the
// smallest frontend-only state needed to give each entry a stable, already-
// elapsed display value once logged, per CLAUDE.md's Activity Log
// requirements. Should the backend ever start stamping events for future
// evaluation metrics, this only needs to read that value instead of
// `Date.now()` at push-time; the elapsed-since-session-start display and the
// per-session reset stay exactly as they are.
function formatElapsedSince(startMs) {
  // `startMs` is null only in the instant before `onSession` has set it —
  // see `sessionStartRef` — which no logged event can ever observe (the
  // `session` frame, and therefore `onSession`, always precedes any `event`
  // frame). Falls back to "just started" rather than a garbage huge value.
  const totalSeconds = Math.max(0, Math.floor((Date.now() - (startMs ?? Date.now())) / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
}

// How long a successful-compile toast stays up before it auto-dismisses. A
// failed one is deliberately NOT on a timer — it stays until the user
// dismisses it or a new compile starts, since a syntax error is exactly the
// moment a student needs the notification to still be there.
const COMPILE_SUCCESS_TOAST_MS = 4000

// Backend `CompileStatus` -> the small bottom-right toast's icon/title. The
// subtitle line is computed separately (the primary file name, or "Check
// compiler errors" on failure) since it isn't a fixed string.
const COMPILE_TOAST_CONTENT = {
  running: { icon: '⚙', title: 'Compiling…' },
  succeeded: { icon: '✓', title: 'Compilation successful' },
  failed: { icon: '✕', title: 'Compilation failed' },
}

// "Near enough to the bottom that a new Activity Log entry should still
// auto-scroll into view" — see the activityLogRef/onScroll wiring below.
const ACTIVITY_LOG_AUTOSCROLL_THRESHOLD_PX = 32

export default function BuildMode({ onBack, onMenu }) {
  const [state, setState] = useState(null)
  const [events, setEvents] = useState([])
  const [activeFile, setActiveFile] = useState(null)
  const [protocolError, setProtocolError] = useState('')

  // Blockly Phase 1 POC (see CLAUDE.md). BLOCKS is the default view — "user
  // sees a large blank canvas" is the very first thing Build Mode should
  // show — CODE shows the same source as plain text via the existing
  // full-editor textarea. Both panels stay mounted once entered (toggled
  // via the `hidden` attribute, not conditional rendering) so switching
  // away from BLOCKS never disposes the live Blockly workspace and loses
  // whatever the student built.
  const [editorMode, setEditorMode] = useState('blocks')
  // Collapsing this hands its grid column's width to the editor/Blockly
  // canvas (see `.ide-grid.left-collapsed` in App.css) — no new sidebar,
  // just the existing left column shrinking to a thin toggle strip. The
  // Blockly workspace's own ResizeObserver (see BlocklyWorkspace.jsx)
  // picks up the resulting size change on its own; nothing here has to
  // tell it to.
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false)

  // The one editable region's unsent local edit, or null when the editor
  // should show the backend's own saved text. A single value (rather than
  // per-file/per-region state) is correct for this project — the LED Blink
  // pipeline-proof project has exactly one editable region across all of its
  // files (see backend/app/build/blink.py) — and would need to become a map
  // keyed by region id only if a future project ever had more than one.
  const [pendingEdit, setPendingEdit] = useState(null)
  const draftDirty = pendingEdit !== null

  // True from the moment a flash is requested until the backend answers.
  // The backend sends one batch of frames when the whole action finishes,
  // so its intermediate DETECTING/RUNNING statuses are never observable
  // here — this local flag is what keeps the button disabled and the banner
  // honest ("detecting or uploading, we don't know which yet") for the ~15s
  // a real upload takes, instead of pretending to know the sub-step.
  const [flashPending, setFlashPending] = useState(false)

  // True from the moment COMPILE is clicked with an unsent draft still in
  // the editor until the backend has confirmed that draft reached the
  // BuildWorkspace — see `compile()` below. Kept separate from `isCompiling`
  // (which only reflects `state.compile_status`) because this covers the
  // brief edit_region round-trip that happens *before* a `compile` request
  // is even sent.
  const [pendingCompileSync, setPendingCompileSync] = useState(false)
  // Set together with `pendingCompileSync`; cleared (and `sendCompile`
  // fired) the moment the matching `code_edited` event streams in — see the
  // `onEvent` handler below. A ref, not state, because it must be read
  // synchronously inside that handler without waiting for a re-render.
  const compileAfterSyncRef = useRef(false)
  // `sendCompile` itself only exists once `useBuildSocket` below has
  // returned, but the `onEvent` handler passed *into* that call needs to
  // invoke it — this ref is populated by the effect right after the hook
  // call and lets the handler read the current `sendCompile` without a
  // circular reference.
  const sendCompileRef = useRef(() => {})

  // The moment THIS Build Mode session started, for Activity Log elapsed
  // timestamps (see `formatElapsedSince`) — set in `onSession` below (never
  // during render: `Date.now()` is impure), so a fresh `/ws/build`
  // connection (a new session, per backend/app/build_websocket.py) always
  // restarts the display at 00:00:00, never carries over a previous
  // session's clock.
  const sessionStartRef = useRef(null)

  // Drives the small bottom-right compile toast (see COMPILE_TOAST_CONTENT).
  // `showSuccessToast` is on its own timer so a success notification
  // auto-dismisses; a failure has no timer and instead can be dismissed by
  // the user (`failureToastDismissed`) or superseded by the next compile.
  const [showSuccessToast, setShowSuccessToast] = useState(false)
  const [failureToastDismissed, setFailureToastDismissed] = useState(false)
  const successToastTimeoutRef = useRef(null)

  const { status, sendEditRegion, sendCompile, sendFlash, sendHardwareStatus } = useBuildSocket({
    onSession: () => {
      setEvents([])
      setProtocolError('')
      setFlashPending(false)
      sessionStartRef.current = Date.now()
      setShowSuccessToast(false)
      setFailureToastDismissed(false)
      if (successToastTimeoutRef.current) {
        clearTimeout(successToastTimeoutRef.current)
        successToastTimeoutRef.current = null
      }
      compileAfterSyncRef.current = false
      setPendingCompileSync(false)
    },
    onState: (data) => {
      setState(data)
      setFlashPending(false)
    },
    onEvent: (message) => {
      setEvents((evts) => [
        ...evts,
        { event: message.event, data: message.data, at: formatElapsedSince(sessionStartRef.current) },
      ])
      // EDIT -> COMPILE without SAVE: `code_edited` is the one event only a
      // successful `edit_region` produces (never `hardware_status`'s
      // periodic poll, which emits no events at all — see
      // backend/app/build/service.py::detect_hardware), so this is an
      // unambiguous confirmation that the draft just sent by `compile()`
      // below has landed in the backend's BuildWorkspace. Only then is it
      // safe to actually issue `compile`.
      if (compileAfterSyncRef.current && message.event === 'code_edited') {
        compileAfterSyncRef.current = false
        setPendingCompileSync(false)
        sendCompileRef.current()
      }
      // Drives the compile toast directly off the real backend events that
      // start/end a compile, rather than diffing `state.compile_status` in
      // an effect — this is the actual moment each transition happens, and
      // it keeps every state update here a plain response to something that
      // occurred, not synchronized derived state.
      if (message.event === 'compile_started') {
        setFailureToastDismissed(false)
        setShowSuccessToast(false)
        if (successToastTimeoutRef.current) {
          clearTimeout(successToastTimeoutRef.current)
          successToastTimeoutRef.current = null
        }
      } else if (message.event === 'compile_succeeded') {
        setShowSuccessToast(true)
        if (successToastTimeoutRef.current) clearTimeout(successToastTimeoutRef.current)
        successToastTimeoutRef.current = setTimeout(
          () => setShowSuccessToast(false),
          COMPILE_SUCCESS_TOAST_MS,
        )
      } else if (message.event === 'compile_failed') {
        setShowSuccessToast(false)
        if (successToastTimeoutRef.current) {
          clearTimeout(successToastTimeoutRef.current)
          successToastTimeoutRef.current = null
        }
      }
    },
    onError: (message) => {
      setProtocolError(message)
      setFlashPending(false)
      // The sync edit itself was rejected (e.g. an unknown/locked region) —
      // abort rather than compiling stale source.
      if (compileAfterSyncRef.current) {
        compileAfterSyncRef.current = false
        setPendingCompileSync(false)
      }
    },
  })

  useEffect(() => {
    sendCompileRef.current = sendCompile
  }, [sendCompile])

  const files = state?.files || {}
  const fileNames = Object.keys(files)
  const resolvedActiveFile = activeFile && files[activeFile] ? activeFile : fileNames[0] || null
  const activeSegments = files[resolvedActiveFile]?.segments || []
  const editableSegment = activeSegments.find((s) => s.kind === 'editable') || null
  const draft = pendingEdit !== null ? pendingEdit : editableSegment?.text || ''

  // TEMPORARY PHASE 1 POC: a file represented as exactly one EDITABLE
  // segment (no LOCKED segments at all — see backend/app/build/blink.py) is
  // rendered as a plain, unrestricted code editor rather than the
  // locked/editable region view below. Driven entirely by the shape of the
  // data the backend sent, not by checking a project id, so a project that
  // brings back a real locked/editable split (see
  // backend/app/build/environmental.py) renders with the region view again
  // automatically, with nothing here to change back.
  const isFullEditorFile = activeSegments.length === 1 && activeSegments[0].kind === 'editable'
  const isFullEditorProject =
    fileNames.length > 0 &&
    fileNames.every((name) => {
      const segments = files[name]?.segments || []
      return segments.length === 1 && segments[0]?.kind === 'editable'
    })

  const compileStatus = state?.compile_status || 'not_started'
  const isCompiling = compileStatus === 'running'
  // Compile always targets the whole project, not just the active tab (see
  // backend/app/build/service.py::compile_workspace), so the toast names
  // the sketch's entry file — the same `.ino` file
  // `BuildWorkspace.materialize` picks — rather than whatever tab happens
  // to be open.
  const primaryFileName = fileNames.find((name) => name.endsWith('.ino')) || fileNames[0] || ''

  // Unmount-only cleanup: the transitions that actually drive
  // `showSuccessToast`/`failureToastDismissed` (and schedule/clear this
  // timeout) live in the `onEvent` handler above, right where the real
  // `compile_started`/`compile_succeeded`/`compile_failed` events land —
  // not here — so this effect has exactly one job.
  useEffect(
    () => () => {
      if (successToastTimeoutRef.current) clearTimeout(successToastTimeoutRef.current)
    },
    [],
  )

  // `running` covers both the brief unsaved-edit sync and the real compile,
  // since from the student's point of view both are "compiling" — see
  // `compile()`. `succeeded`/`failed` reflect the real backend
  // `compile_status`; nothing here is simulated.
  const compileToastKind = pendingCompileSync || isCompiling
    ? 'running'
    : compileStatus === 'failed' && !failureToastDismissed
      ? 'failed'
      : compileStatus === 'succeeded' && showSuccessToast
        ? 'succeeded'
        : null

  const flashStatus = state?.flash_status || 'not_started'
  const flashOutput = state?.flash_output || null
  // The backend's own answer to "would a flash be accepted right now?" —
  // true only while the last compile succeeded AND the workspace still
  // matches what it built (backend/app/build_sessions.py: flash_ready). The
  // button follows that rather than second-guessing it here, so it can
  // never offer an action the backend would refuse.
  const flashReady = Boolean(state?.flash_ready)
  // `!draftDirty` closes the one gap `flash_ready` alone can't: it reflects
  // the *backend* workspace's fingerprint, which a local unsent edit never
  // touches, so it can still read `true` from an earlier compile while the
  // editor shows different code than what was built. Compiling always syncs
  // first (see `compile()`), so this never blocks a legitimate flash for
  // more than the moment it takes to hit COMPILE again.
  const canFlash = flashReady && !flashPending && !isCompiling && !pendingCompileSync && !draftDirty
  const flashBannerStatus = flashPending ? 'running' : flashStatus

  // Backend `hardware` block (backend/app/build_sessions.py: `snapshot`) —
  // real device-detection state, distinct from `flash_status` (which only
  // reflects discovery run as part of an actual flash attempt, or never, if
  // one hasn't happened yet) and distinct from `status` (the WebSocket
  // transport, not the physical board).
  const hardware = state?.hardware || { status: 'not_checked', board_name: null, port: null }
  const socketLinked = status === CONNECTION_STATUS.CONNECTED
  const headerLink = socketLinked
    ? HARDWARE_LINK_LABEL[hardware.status] || hardware.status
    : SOCKET_LINK_LABEL[status]
  const headerBoard = socketLinked && hardware.status === 'connected'
    ? hardware.board_name || '—'
    : socketLinked && hardware.status === 'ambiguous'
      ? 'MULTIPLE'
      : '—'
  const headerPort = socketLinked && hardware.status === 'connected' ? hardware.port || '—' : '—'

  // Keeps a truthful LINK as boards are plugged/unplugged while Build Mode
  // stays open, rather than only ever showing whatever was detected at
  // connect time — see CLAUDE.md requirement 7. Skips a poll while a compile
  // or flash is in flight so this never competes with either for the
  // toolchain, and re-checks immediately once the socket (re)connects.
  const pollGuardRef = useRef({ isCompiling, flashPending, pendingCompileSync })
  useEffect(() => {
    pollGuardRef.current = { isCompiling, flashPending, pendingCompileSync }
  })
  useEffect(() => {
    if (!socketLinked) return undefined
    const poll = () => {
      if (pollGuardRef.current.isCompiling || pollGuardRef.current.flashPending || pollGuardRef.current.pendingCompileSync) return
      sendHardwareStatus()
    }
    poll()
    const id = setInterval(poll, HARDWARE_POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [socketLinked, sendHardwareStatus])

  // The Flash Terminal under the code editor — real Arduino CLI/esptool
  // output (`flashOutput.stdout`/`stderr`), never simulated text. Built the
  // same way the old `.flash-result` block was (same banner/device/failure-
  // note copy), just rendered as one scrollable terminal block instead of a
  // separate colored banner plus a nested `<pre>`.
  const flashTerminalText = flashPending
    ? 'Detecting device / uploading…'
    : flashStatus === 'not_started'
      ? 'Flash output will appear here once you flash the firmware.'
      : [
          FLASH_BANNER[flashStatus],
          flashOutput?.port ? `DEVICE: ${flashOutput.port}` : null,
          flashOutput && !flashOutput.success
            ? FLASH_FAILURE_NOTE[flashOutput.category] || flashOutput.category
            : null,
          flashStatus === 'succeeded'
            ? 'Firmware uploaded. This does not verify that it runs correctly or is secure.'
            : null,
          flashOutput?.stdout,
          flashOutput?.stderr,
        ]
          .filter(Boolean)
          .join('\n')

  // Smart auto-scroll for the Activity Log: a new entry scrolls into view
  // only if the reader was already at/near the bottom. `isNearBottomRef` is
  // kept current by the list's own onScroll handler (so it reflects where
  // the reader actually is right before a new entry lands) and read — not
  // set — by the effect below, which runs after `events` grows.
  const activityLogRef = useRef(null)
  const isNearBottomRef = useRef(true)

  function handleActivityLogScroll(e) {
    const el = e.currentTarget
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    isNearBottomRef.current = distanceFromBottom < ACTIVITY_LOG_AUTOSCROLL_THRESHOLD_PX
  }

  useEffect(() => {
    const el = activityLogRef.current
    if (el && isNearBottomRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [events])

  // Blockly Phase 1 POC (see CLAUDE.md). The generated C++ is fed into the
  // exact same `pendingEdit` draft state the plain-text CODE textarea's own
  // `onChange` already writes to — not a second, parallel source of truth.
  // That single reuse is the entire integration: SAVE/COMPILE/FLASH, the
  // unsaved-edit sync-before-compile behaviour, and the compile-to-flash
  // fingerprint check all already operate on `draft`/`pendingEdit`, so
  // Blockly-authored firmware goes through every one of them completely
  // unchanged, whether or not SAVE was ever clicked.
  function handleBlocklyCodeChange(code) {
    setPendingEdit(code)
  }

  function saveRegion() {
    if (!editableSegment || !resolvedActiveFile) return
    sendEditRegion(resolvedActiveFile, editableSegment.region_id, draft)
    setPendingEdit(null)
  }

  function compile() {
    if (isCompiling || pendingCompileSync) return
    if (draftDirty && editableSegment && resolvedActiveFile) {
      // EDIT -> COMPILE without SAVE. Synchronize the current in-memory
      // draft into the backend BuildWorkspace through the existing
      // edit_region mechanism first — reused rather than bypassed, exactly
      // as a manual SAVE would — and only send `compile` once the backend
      // has confirmed that edit (see the `onEvent` handler above). This is
      // not "auto-clicking SAVE": SAVE and COMPILE remain distinct actions
      // a user can each trigger independently; COMPILE just also carries
      // whatever draft the editor currently holds, because a compile
      // against text the editor isn't showing would be wrong regardless of
      // whether the user happened to click SAVE first.
      compileAfterSyncRef.current = true
      setPendingCompileSync(true)
      sendEditRegion(resolvedActiveFile, editableSegment.region_id, draft)
      setPendingEdit(null)
    } else {
      sendCompile()
    }
  }

  function flash() {
    if (!canFlash) return
    setFlashPending(true)
    sendFlash()
  }

  function dismissCompileToast() {
    setShowSuccessToast(false)
    setFailureToastDismissed(true)
  }

  function flashHint() {
    if (canFlash) return 'Upload the compiled firmware to the connected ESP32'
    if (isCompiling || pendingCompileSync) return 'Wait for the current compilation to finish'
    if (flashPending) return 'A flash is already in progress'
    if (draftDirty) return 'Compile your latest edits before flashing'
    if (compileStatus === 'succeeded') {
      return isFullEditorFile
        ? 'The source changed after the last build — compile again before flashing'
        : 'The security region changed after the last build — compile again before flashing'
    }
    return 'Compile the firmware successfully before flashing'
  }

  return (
    <div className="page">
      <AppHeader
        title="ESP32 WORKSTATION IDE — BUILD MODE"
        right={
          <>
            {state ? state.project.firmware_name : 'LOADING PROJECT…'} | BOARD: {headerBoard} |
            PORT: {headerPort} | LINK: {headerLink}
          </>
        }
        onMenu={onMenu}
      />
      <nav className="ide-menu" aria-label="IDE menu">
        <span>File</span>
        <span>Edit</span>
        <span>Sketch</span>
        <span>Board: {state?.project.board.name || '—'}</span>
        <span>Help</span>
      </nav>
      <main className={`page-body ide-grid ${leftPanelCollapsed ? 'left-collapsed' : ''}`}>
        <aside className={`side-col ${leftPanelCollapsed ? 'collapsed' : ''}`}>
          {/* Collapsible left column (see CLAUDE.md) — not a second
              sidebar: collapsing this one hands its width to the Blockly/
              code workspace via `.ide-grid.left-collapsed` in App.css. The
              Blockly canvas's own ResizeObserver (BlocklyWorkspace.jsx)
              reacts to the resulting size change on its own. */}
          <button
            type="button"
            className="side-col-toggle"
            onClick={() => setLeftPanelCollapsed((collapsed) => !collapsed)}
            aria-label={leftPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
            title={leftPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
          >
            {leftPanelCollapsed ? '▶' : '◀'}
          </button>
          {!leftPanelCollapsed && (
            <>
              <section className="panel">
                <h3>VULNERABILITY SCENARIO</h3>
                <p className="file-active">{state?.project.scenario_id || 'connecting…'}</p>
                <p className="muted-note">{state?.project.module_id}</p>
              </section>
              <section className="panel">
                <h3>PROJECT FILES</h3>
                <ul className="file-list">
                  {fileNames.map((name) => (
                    <li key={name}>
                      <button
                        type="button"
                        className={name === resolvedActiveFile ? 'active' : ''}
                        onClick={() => setActiveFile(name)}
                      >
                        {name}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
              <section className="panel">
                <h3>BUILD STATUS</h3>
                <ul className="checks">
                  <li>
                    Compile{' '}
                    <span className="status-pill">{BUILD_STATUS_LABEL[state?.compile_status] || '—'}</span>
                  </li>
                  <li>
                    Flash <span className="status-pill">{BUILD_STATUS_LABEL[state?.flash_status] || '—'}</span>
                  </li>
                  <li>
                    Validation{' '}
                    <span className="status-pill">{BUILD_STATUS_LABEL[state?.validation_status] || '—'}</span>
                  </li>
                </ul>
              </section>
              {/* All five Build/Development action controls live together
                  here, in the existing left-hand information column — not
                  in a new sidebar or toolbar. Only their position moved;
                  every button below still calls the exact same handler it
                  always did. */}
              <section className="panel">
                <h3>BUILD ACTIONS</h3>
                <div className="build-actions-list">
                  <button
                    type="button"
                    className="btn-solid"
                    disabled={!editableSegment || !draftDirty}
                    onClick={saveRegion}
                  >
                    {isFullEditorFile ? 'SAVE' : 'SAVE SECURITY REGION'}
                  </button>
                  <button
                    type="button"
                    className="btn-outline"
                    disabled={isCompiling || pendingCompileSync || flashPending}
                    onClick={compile}
                  >
                    {pendingCompileSync ? '… SYNCING' : isCompiling ? '… COMPILING' : '▶ COMPILE'}
                  </button>
                  <button
                    type="button"
                    className="btn-outline"
                    disabled={!canFlash}
                    onClick={flash}
                    title={flashHint()}
                  >
                    {flashPending ? '… FLASHING' : '▲ FLASH'}
                  </button>
                  {FUTURE_CONTROLS.map(({ key, label }) => (
                    <span className="future-control" key={key}>
                      <button type="button" className="btn-outline" disabled>
                        {label}
                      </button>
                      <em>COMING IN PHASE 3B</em>
                    </span>
                  ))}
                </div>
              </section>
            </>
          )}
        </aside>
        <section className="editor">
          <div className="tabs">
            {fileNames.map((name) => (
              <button
                type="button"
                key={name}
                className={name === resolvedActiveFile ? 'active' : ''}
                onClick={() => setActiveFile(name)}
              >
                {name}
              </button>
            ))}
          </div>
          <div className="code-view">
            {activeSegments.length === 0 ? (
              <p className="muted-note">Loading firmware…</p>
            ) : isFullEditorFile ? (
              // TEMPORARY PHASE 1 POC — see the isFullEditorFile comment
              // above: the whole file is one editable region. Blockly is
              // one more way to author that region's text, alongside the
              // plain CODE textarea — see `handleBlocklyCodeChange` above:
              // both write into the same `pendingEdit` draft, so nothing
              // downstream (Save/Compile/Flash) needs to know which one the
              // student used. Both panels stay mounted (toggled via
              // `hidden`) so switching tabs never loses the Blockly
              // workspace's blocks.
              <div className="code-workspace">
                <div className="editor-mode-tabs" role="tablist" aria-label="Programming view">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={editorMode === 'blocks'}
                    className={editorMode === 'blocks' ? 'active' : ''}
                    onClick={() => setEditorMode('blocks')}
                  >
                    BLOCKS
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={editorMode === 'code'}
                    className={editorMode === 'code' ? 'active' : ''}
                    onClick={() => setEditorMode('code')}
                  >
                    CODE
                  </button>
                </div>
                <div className="blockly-panel" hidden={editorMode !== 'blocks'}>
                  <BlocklyWorkspace onCodeChange={handleBlocklyCodeChange} />
                </div>
                <textarea
                  className="code-editor-full"
                  hidden={editorMode !== 'code'}
                  spellCheck={false}
                  value={draft}
                  onChange={(e) => setPendingEdit(e.target.value)}
                />
              </div>
            ) : (
              activeSegments.map((segment) =>
                segment.kind === 'locked' ? (
                  <pre key={segment.region_id} className="code-segment locked" spellCheck={false}>
                    {segment.text}
                  </pre>
                ) : (
                  <div key={segment.region_id} className="code-segment editable">
                    <div className="region-banner">STUDENT SECURITY REGION — {segment.region_id}</div>
                    <textarea
                      spellCheck={false}
                      value={draft}
                      onChange={(e) => setPendingEdit(e.target.value)}
                    />
                  </div>
                ),
              )
            )}
          </div>
          {/* Every action button (SAVE/COMPILE/FLASH/validation/security
              test) now lives in the left-hand BUILD ACTIONS panel — see
              above. This bar is just the workspace's save-state readout. */}
          <div className="console">
            <div className={`console-out ${protocolError ? 'warn' : ''}`}>
              {protocolError
                ? `✗ ${protocolError}`
                : draftDirty
                  ? '● unsaved edits — not yet sent to the backend'
                  : state?.dirty
                    ? isFullEditorFile
                      ? '● source saved — differs from the original firmware'
                      : '● security region saved — differs from the original firmware'
                    : isFullEditorFile
                      ? 'Source matches the original firmware.'
                      : 'Security region matches the original firmware.'}
            </div>
          </div>
          {/* Flash output stays inline (real Arduino CLI/esptool upload
              text matters to a student watching hardware upload) — see
              CLAUDE.md: it lives in a compact, bounded, scrollable terminal
              under the editor rather than the old unbounded console block.
              Compile feedback moved out entirely to the bottom-right toast
              below; `state.compile_output` (stdout/stderr) is untouched and
              still fully populated for a future detailed view — it just
              isn't rendered inline any more. */}
          <div className={`flash-terminal ${flashBannerStatus}`}>
            <div className="flash-terminal-header">
              <span>FLASH TERMINAL</span>
              <span className="status-pill">
                {flashPending ? 'UPLOADING…' : BUILD_STATUS_LABEL[flashStatus] || flashStatus}
              </span>
            </div>
            <pre className="flash-terminal-body">{flashTerminalText}</pre>
          </div>
        </section>
        <aside className="side-col">
          {/* TEMPORARY PHASE 1 POC: nothing here is locked (see
              isFullEditorProject above), so there is no editing restriction
              left for a region map to usefully show — it is omitted rather
              than displayed as an all-editable no-op. Reappears on its own
              once a project brings back a real locked/editable split. */}
          {!isFullEditorProject && (
            <section className="panel dashed">
              <h3>REGION MAP</h3>
              {fileNames.length === 0 ? (
                <p className="muted-note">No workspace loaded yet.</p>
              ) : (
                fileNames.map((name) => (
                  <div key={name} className="region-map-file">
                    <p className="file-active">{name}</p>
                    <ul className="region-map-list">
                      {files[name].segments.map((segment) => (
                        <li key={segment.region_id}>
                          <span className={`region-badge ${segment.kind}`}>{segment.kind}</span>
                          {segment.region_id}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))
              )}
            </section>
          )}
          <section className="panel">
            <h3>ACTIVITY LOG</h3>
            {/* Bounded + independently scrollable (see .activity-log-scroll
                in App.css) — the shared `.plain-log` class itself is left
                untouched since Hack Mode's own Activity Log also uses it.
                Timestamps are session-elapsed HH:MM:SS (formatElapsedSince),
                not time-of-day; see the module-level comment above. */}
            <div
              className="activity-log-scroll"
              ref={activityLogRef}
              onScroll={handleActivityLogScroll}
            >
              <ul className="plain-log">
                {events.length === 0 ? (
                  <li className="muted">No activity yet.</li>
                ) : (
                  events.map((entry, i) => (
                    <li key={i}>
                      {entry.at} — {EVENT_LABELS[entry.event] || entry.event}
                    </li>
                  ))
                )}
              </ul>
            </div>
          </section>
        </aside>
      </main>
      <footer className="link-footer">
        <button type="button" onClick={onBack}>
          ← BACK TO MENU
        </button>
        <span>BUILD MODE — WRITE, COMPILE, AND FLASH FIRMWARE.</span>
        <span />
      </footer>
      {/* Small bottom-right compile notification — see CLAUDE.md. Floats
          above the main layout (position: fixed in App.css) rather than
          taking up workspace; the large inline compile terminal it replaces
          is gone from the normal layout entirely. */}
      {compileToastKind && (
        <div className={`compile-toast ${compileToastKind}`} role="status">
          <button
            type="button"
            className="compile-toast-dismiss"
            onClick={dismissCompileToast}
            aria-label="Dismiss notification"
          >
            ×
          </button>
          <strong>
            {COMPILE_TOAST_CONTENT[compileToastKind].icon} {COMPILE_TOAST_CONTENT[compileToastKind].title}
          </strong>
          <span>{compileToastKind === 'failed' ? 'Check compiler errors' : primaryFileName}</span>
        </div>
      )}
    </div>
  )
}
