import { useEffect, useRef, useState } from 'react'
import AppHeader from '../components/AppHeader'
import BlocklyWorkspace from '../components/BlocklyWorkspace'
import CppCode from '../components/CppCode'
import Icon from '../components/Icon'
import StatusBar from '../components/StatusBar'
import { restoreFromSessionFrame } from '../build/sessionRestore'
import { numberSegments } from '../build/sectionLines'
import useBuildSocket, { CONNECTION_STATUS } from '../hooks/useBuildSocket'
// Shared with Hack Mode rather than kept private here: both screens render
// the SAME backend device state (one `device_monitor` per process, see
// backend/app/hardware/) through the SAME readout component, so the two
// cannot drift. The mode name in the top bar is the only difference between
// this screen's status bar and Hack Mode's.
import HardwareHeaderStatus from '../components/HardwareHeaderStatus'
import { HARDWARE_POLL_INTERVAL_MS, UNKNOWN_HARDWARE } from '../hardware/deviceState'
import {
  CHAIN_INTENT,
  CHAIN_NOTICE,
  CHAIN_PHASE,
  INITIAL_CHAIN,
  chainBusy,
  createChainDriver,
  transmitVia,
} from '../build/compileChain'

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
  security_region_edited: 'Security region edited',
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

// The General Terminal's header suffix for whichever operation it is
// currently showing (see `currentOp` below) — GENERAL TERMINAL alone when
// nothing has run yet.
const GENERAL_TERMINAL_LABEL = { compile: 'COMPILE', flash: 'FLASH', validation: 'VALIDATION' }

// Backend `CompileStatus` -> the General Terminal's banner copy while it is
// showing a compile, mirroring FLASH_BANNER's shape below.
const COMPILE_BANNER = {
  running: 'COMPILING…',
  succeeded: '✓ COMPILE SUCCESS',
  failed: '✗ COMPILE FAILED',
}

// Backend `CompileFailureCategory` (backend/app/build/compiler.py) -> one
// plain sentence, the same role FLASH_FAILURE_NOTE plays for a failed flash.
const COMPILE_FAILURE_NOTE = {
  compiler_error: 'The compiler reported an error — see the output below.',
  timeout: 'The compile exceeded its time limit and was stopped.',
  toolchain_unavailable: 'The Arduino CLI toolchain is not available on the backend.',
  internal_error: 'The backend could not run the compile.',
}

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

// Backend `ValidationStatus` -> the Validation Terminal's banner copy,
// reusing exactly the same `.flash-terminal` visual language (Phase B7).
// `ValidationStatus` has no `detecting`/`no_device` members — a validation
// never touches device discovery, it judges firmware already on the board —
// so this map only ever needs the three states below.
const VALIDATION_BANNER = {
  running: 'VALIDATING…',
  succeeded: '✓ VALIDATION SUCCESS',
  failed: '✗ VALIDATION FAILED',
}

// Backend `ValidationOutcome` (`validation_output.outcome`) -> one plain
// sentence. `validation_status` alone collapses FAILURE/ERROR/an unexpected
// NOT_RUN into the same FAILED status (see app/build/service.py
// `_finish_validation`), so this is what lets a student tell "the fix does
// not work" apart from "the check itself could not run".
const VALIDATION_OUTCOME_NOTE = {
  failure: 'The check ran and the remediation requirement was not met.',
  error: 'The validation check itself did not complete — this is not a verdict on your firmware.',
  not_run: 'No check ran for this firmware.',
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
// The Activity Log's session clock. Only ever called from the `session`
// socket handler, never during render — kept as a module-level function so
// that stays obvious to the React Compiler's purity check, which cannot see
// that `useBuildSocket` invokes its handlers from socket callbacks.
function sessionClockNow() {
  return Date.now()
}

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
  running: { icon: 'status-partial', title: 'Compiling…' },
  succeeded: { icon: 'status-done', title: 'Compilation successful' },
  failed: { icon: 'status-failed', title: 'Compilation failed' },
}

// How each build step (compile / flash / validation) and the General Terminal's
// status chip look for each backend status. The status WORD is always shown
// next to the icon, so colour is never the only cue.
const STATUS_VISUAL = {
  not_started: { icon: 'status-pending', tone: 'is-muted' },
  detecting: { icon: 'status-partial', tone: 'is-warning' },
  running: { icon: 'status-partial', tone: 'is-warning' },
  succeeded: { icon: 'status-done', tone: 'is-success' },
  failed: { icon: 'status-failed', tone: 'is-danger' },
  no_device: { icon: 'status-failed', tone: 'is-danger' },
}

// The Blockly workspace is reached through the side-panel rail's three real
// panels; the rail only chooses which one the collapsible left column shows.
const SIDE_PANELS = [
  { id: 'project', icon: 'rail-files', label: 'Project' },
  { id: 'brief', icon: 'rail-checklist', label: 'Remediation brief' },
  { id: 'activity', icon: 'rail-timeline', label: 'Activity log' },
]

// Section interaction policy (backend `InteractionPolicy`) -> how it is labelled.
// LOCKED and EXPLORE are both read-only; only EDITABLE opens a Blockly canvas.
const POLICY_LABEL = { locked: 'Locked', explore: 'Explore', editable: 'Editable' }
const POLICY_ICON = { locked: 'lock', explore: 'eye', editable: 'rail-blocks' }

function fileIcon(name) {
  return name.endsWith('.ino') ? 'file-sketch' : 'file'
}

// One stage of the Compile -> Flash -> Verify strip.
function PipelineStep({ status, title, detail }) {
  const visual = STATUS_VISUAL[status] || STATUS_VISUAL.not_started
  return (
    <li className={`pipeline-step ${visual.tone}`}>
      <Icon name={visual.icon} size={28} />
      <div>
        <p className="pipeline-title">{title}</p>
        <p className="pipeline-detail">{detail}</p>
      </div>
    </li>
  )
}

// Why an EDIT -> COMPILE -> FLASH chain stopped short (src/build/
// compileChain.js). REQUEST_REFUSED has no copy of its own: the backend's
// `error` frame already says why, in the line above this one.
const CHAIN_NOTICE_TEXT = {
  [CHAIN_NOTICE.EDIT_REJECTED]:
    'Compile stopped — the backend rejected this edit, so nothing was saved, compiled or flashed. Your blocks are kept as they are: fix them and compile again.',
  [CHAIN_NOTICE.COMPILE_FAILED_NO_FLASH]: 'Flash cancelled — compilation failed, so nothing was uploaded.',
  [CHAIN_NOTICE.EDITED_DURING_CHAIN_NO_FLASH]:
    'Flash cancelled — the blocks changed while compiling, so that build is already out of date. Flash again to build and upload the current blocks.',
}

// Shown when a student tries to leave a section that still holds edits
// COMPILE has not submitted. There is no separate save step any more, so
// switching away would otherwise silently throw those blocks away.
const SWITCH_BLOCKED_NOTICE =
  'This section has edits that are not compiled yet. Press COMPILE to submit them before opening another section or file.'

// "Near enough to the bottom that a new Activity Log entry should still
// auto-scroll into view" — see the activityLogRef/onScroll wiring below.
const ACTIVITY_LOG_AUTOSCROLL_THRESHOLD_PX = 32

export default function BuildMode({ onQuit, onReset, onMenu, participantId }) {
  const [state, setState] = useState(null)
  const [events, setEvents] = useState([])
  const [activeFile, setActiveFile] = useState(null)
  const [protocolError, setProtocolError] = useState('')

  // SECTION-BASED EDITING (Phase B8 correction). `selectedSectionId` is the
  // one section currently open in the editor pane — a student clicks its
  // header directly in the code view to open it, whatever its policy.
  // `sectionData` is the backend's answer to `section_blockly` for an
  // EDITABLE section only
  // (`{path, sectionId, representable, workspace, preserved}` — see
  // backend/app/build/section_blockly.py); LOCKED/EXPLORE sections need no
  // round trip, since their current text is already in `state.files`.
  const [selectedSectionId, setSelectedSectionId] = useState(null)
  const [sectionData, setSectionData] = useState(null)
  const [sectionLoading, setSectionLoading] = useState(false)
  // BLOCKLY WORKSPACE TABS — presentation only, layered on top of the state
  // above. `selectedSectionId` still names the one EDITABLE section actually
  // open (unchanged); this just remembers which editable sections have been
  // opened during the current file so their tabs stay in the strip (closable
  // via ×) after the student switches back to CODE or to a different
  // section. Reset whenever the active file changes or a new session starts,
  // since a tab's region id only means something within its own file.
  const [openWorkspaceTabs, setOpenWorkspaceTabs] = useState([])
  // THE LOCAL DRAFT AND THE EDIT -> COMPILE -> FLASH CHAIN. There is no
  // student-facing save: a section's edits live in the chain driver
  // (src/build/compileChain.js `createChainDriver`) until COMPILE or FLASH
  // submits them. The driver keeps the draft's CONTENT and a version number
  // that changes on EVERY local edit; the backend's acknowledgement clears
  // the draft only if the version it answers is still the current one, so an
  // edit made while a request was in flight is never mistaken for submitted.
  //
  // The driver is one stable, mutable object (created once, never replaced)
  // because socket handlers must advance it synchronously, the instant a
  // frame arrives. Render code never reads it:
  // `draftVersion` and `chain` below are the state mirrors it renders from,
  // refreshed by `syncChain` after every driver call. They replace the old
  // `sectionPendingWorkspace`/`pendingCompileSync`/`flashPending` flags —
  // "submitting", "compiling before flash" and "flashing" are all phases of
  // this one chain.
  const [driver] = useState(createChainDriver)
  // The open canvas's `{flush()}` (src/components/BlocklyWorkspace.jsx), so
  // COMPILE/FLASH can take the canvas exactly as it is at the click rather
  // than as of the last asynchronously-delivered Blockly change event.
  const blocklyApiRef = useRef(null)
  const [draftVersion, setDraftVersion] = useState(null)
  const [chain, setChain] = useState(INITIAL_CHAIN)
  // Why the last chain stopped short, or a refused section switch — plain
  // copy, shown under the backend's own error line.
  const [chainNotice, setChainNotice] = useState('')
  // The preserved-fragment list to resubmit alongside a Blockly edit — see
  // `BlocklySection.records`/`app/build/blockly_bridge/workspace_state.py`.
  // Starts as exactly what `section_blockly` answered; CLEAR empties it
  // outright, which is the honest "the student deleted this" edit the
  // no-restore-original requirement calls for — never something this
  // frontend invents on its own, and never done automatically to get a
  // security-region edit past the backend's ownership rule.
  const [, setSectionPreserved] = useState([])
  const [legacyTextOpen, setLegacyTextOpen] = useState(false)
  const [legacyDraft, setLegacyDraft] = useState(null)

  // Collapsing this hands its grid column's width to the editor/Blockly
  // canvas (see `.ide-grid.left-collapsed` in App.css) — no new sidebar,
  // just the existing left column shrinking to a thin toggle strip. The
  // Blockly workspace's own ResizeObserver (see BlocklyWorkspace.jsx)
  // picks up the resulting size change on its own; nothing here has to
  // tell it to.
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false)
  // Which of the rail's panels (SIDE_PANELS) the left column is showing.
  const [sidePanel, setSidePanel] = useState('project')

  // True from the moment validation is requested until the backend answers
  // with a fresh `state` (or rejects the request with an `error` frame). The
  // backend's own `validation_started`/succeeded/failed events already stream
  // through `onEvent` into the Activity Log below; this local flag is only
  // what keeps the button disabled and the terminal honest while the check
  // runs.
  const [validationPending, setValidationPending] = useState(false)

  // Which of compile/flash/validation the General Terminal below is
  // currently showing — null until the first one runs. Set from the real
  // backend `*_started` events in `onEvent` below, and kept as the last one
  // that ran once everything is idle, so the terminal's result stays on
  // screen until a different operation starts (`currentOp` below folds this
  // together with the three live "is running" flags).
  const [activeOp, setActiveOp] = useState(null)
  // The General Terminal is collapsed to its header bar until clicked.
  const [terminalOpen, setTerminalOpen] = useState(false)

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

  // Mirror the driver into render state after every call, and surface why a
  // chain stopped short, if it did.
  function syncChain(result) {
    setChain(driver.chain)
    setDraftVersion(driver.draftVersion)
    if (result?.notice && CHAIN_NOTICE_TEXT[result.notice]) {
      setChainNotice(CHAIN_NOTICE_TEXT[result.notice])
    }
    if (result?.sendFailed) {
      // Nothing was sent, so nothing will ever answer; the driver has
      // already ended the chain rather than leave COMPILE/FLASH disabled.
      setProtocolError('Not connected to the Build Mode backend — nothing was sent.')
    }
  }

  const {
    status,
    sendEditRegion,
    sendSectionBlockly,
    sendEditSectionBlocks,
    sendCompile,
    sendFlash,
    sendHardwareStatus,
    sendValidate,
  } = useBuildSocket({
    participantId,
    onSession: (message) => {
      // A resumed session brings its own log and age; a new one brings
      // neither, so it restarts at 00:00:00 exactly as before.
      const restored = restoreFromSessionFrame(message, sessionClockNow())
      setEvents(restored.events)
      setProtocolError('')
      setChainNotice('')
      setValidationPending(false)
      setActiveOp(restored.activeOp)
      sessionStartRef.current = restored.startMs
      setShowSuccessToast(false)
      setFailureToastDismissed(false)
      if (successToastTimeoutRef.current) {
        clearTimeout(successToastTimeoutRef.current)
        successToastTimeoutRef.current = null
      }
      syncChain(driver.reset())
      setSelectedSectionId(null)
      setSectionData(null)
      setSectionLoading(false)
      setSectionPreserved([])
      setLegacyTextOpen(false)
      setLegacyDraft(null)
      setOpenWorkspaceTabs([])
    },
    onState: (data) => {
      setState(data)
      setValidationPending(false)
    },
    onSection: (data) => {
      setSectionLoading(false)
      setSectionData(data)
      // The draft baseline is exactly what the backend just answered — the
      // workspace the canvas is about to load and its preserved list — so a
      // CLEAR with no block changes still submits a complete edit.
      syncChain(
        driver.loadBaseline({
          kind: 'blocks',
          path: data?.path,
          sectionId: data?.sectionId,
          workspace: data?.workspace,
          preserved: data?.preserved || [],
        }),
      )
      setSectionPreserved(data?.preserved || [])
      setLegacyTextOpen(false)
      setLegacyDraft(null)
    },
    onEvent: (message) => {
      setEvents((evts) => [
        ...evts,
        { event: message.event, data: message.data, at: formatElapsedSince(sessionStartRef.current) },
      ])
      // `code_edited` is the one event both `edit_region` and
      // `edit_section_blocks` produce on success (never `hardware_status`'s
      // periodic poll, which emits no events at all — see backend/app/build/
      // service.py::detect_hardware), so it is an unambiguous confirmation
      // that the edit the chain submitted has landed in the backend's
      // BuildWorkspace. The chain clears the draft only if no newer edit was
      // made meanwhile, then sends `compile`.
      if (message.event === 'code_edited') {
        setProtocolError('')
        syncChain(driver.editAcknowledged())
      } else if (message.event === 'compile_succeeded' || message.event === 'compile_failed') {
        syncChain(driver.compileFinished(message.event === 'compile_succeeded'))
      } else if (message.event === 'flash_succeeded' || message.event === 'flash_failed') {
        syncChain(driver.flashFinished())
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
      // GENERAL TERMINAL: which of compile/flash/validation it keeps showing
      // once that operation finishes (see `currentOp` below) — set from the
      // real backend event that starts each one, the same plain-response-to-
      // an-occurrence pattern the compile toast above uses, never derived by
      // watching other state in an effect.
      if (message.event === 'compile_started') {
        setActiveOp('compile')
      } else if (message.event === 'flash_started') {
        setActiveOp('flash')
      } else if (message.event === 'validation_started') {
        setActiveOp('validation')
      }
    },
    onError: (message) => {
      setProtocolError(message)
      setValidationPending(false)
      setSectionLoading(false)
      // A rejected edit (e.g. the security region's ownership rule) or a
      // refused compile/flash ends the chain: nothing further is sent, and
      // the draft is left exactly as the student made it.
      syncChain(driver.error())
    },
  })

  // The socket senders only exist once `useBuildSocket` has returned, but the
  // handlers passed INTO it drive the chain — so the chain's way out is
  // attached here, right after the hook call.
  useEffect(() => {
    const senders = { sendEditRegion, sendEditSectionBlocks, sendCompile, sendFlash }
    driver.attach((send, draft) => transmitVia(senders, send, draft))
  }, [driver, sendEditRegion, sendEditSectionBlocks, sendCompile, sendFlash])

  const hasActiveProject = Boolean(state?.has_active_project)
  const files = state?.files || {}
  const fileNames = Object.keys(files)
  const resolvedActiveFile = activeFile && files[activeFile] ? activeFile : fileNames[0] || null
  const activeSegments = files[resolvedActiveFile]?.segments || []
  // The workspace tab bar's active pane, derived from the same
  // `selectedSectionId` the CODE view's in-place highlight already used —
  // only an EDITABLE section ever swaps the editor over to its own
  // workspace pane; a selected LOCKED/EXPLORE section stays a plain
  // highlight inside the stacked CODE view (see `openSection`).
  const selectedSegment = activeSegments.find((s) => s.region_id === selectedSectionId) || null
  const workspaceActive = Boolean(selectedSegment) && selectedSegment.policy === 'editable'
  const sectionDirty = draftVersion !== null
  const busy = chainBusy(chain)
  const flashing = chain.phase === CHAIN_PHASE.FLASHING
  const submitting = chain.phase === CHAIN_PHASE.SUBMITTING

  const compileStatus = state?.compile_status || 'not_started'
  const isCompiling = compileStatus === 'running'
  // Compile always targets the whole project, not just the open section (see
  // backend/app/build/service.py::compile_workspace), so the toast names
  // the sketch's entry file — the same `.ino` file
  // `BuildWorkspace.materialize` picks — rather than whatever section
  // happens to be open.
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

  // `running` covers both the brief edit submission and the real compile,
  // since from the student's point of view both are "compiling" — see the
  // chain in src/build/compileChain.js. `succeeded`/`failed` reflect the
  // real backend `compile_status`; nothing here is simulated.
  const compileToastKind = submitting || isCompiling
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
  // matches what it built (backend/app/build_sessions.py: flash_ready).
  const flashReady = Boolean(state?.flash_ready)
  // FLASH is offered whenever nothing else is in flight: it no longer
  // requires a prior COMPILE, because the chain compiles first whenever the
  // editor holds an unsubmitted draft (`sectionDirty`, which `flash_ready`
  // cannot see) or the backend's last build is not the current source
  // (`!flashReady`). It then flashes only after that compile succeeded with
  // no newer edit made meanwhile, and the backend re-checks the same content
  // hash before uploading anything.
  const canFlash = hasActiveProject && !busy && !isCompiling && !validationPending
  const flashNeedsCompile = sectionDirty || !flashReady
  const flashBannerStatus = flashing ? 'running' : flashStatus

  const validationStatus = state?.validation_status || 'not_started'
  const validationOutput = state?.validation_output || null
  // Phase 1.2. The connected panel's own `RemediationSpec` prose (backend/
  // app/build/validation/models.py), verbatim from `state.remediation` —
  // `null` for the four panels that declare no remediation activity yet.
  // Never assembled or paraphrased here: what a fix must achieve is
  // courseware content, not frontend copy.
  const remediation = state?.remediation || null
  // Mirrors exactly the two gates `BuildService.validate_workspace` itself
  // enforces (app/build/service.py) — a successful flash must have happened,
  // AND the workspace still has to be the one that was flashed (flash_ready,
  // the same content-hash check flashing itself uses) — plus the local
  // "nothing else is already in flight" guards `canFlash` already follows.
  // This can never offer an action the backend would refuse; it only avoids
  // a round trip to find that out.
  const canValidate =
    flashStatus === 'succeeded' && flashReady && !validationPending && !busy && !isCompiling
  const validationBannerStatus = validationPending ? 'running' : validationStatus

  // Backend `hardware` block (backend/app/build_sessions.py: `snapshot`) —
  // real device-detection state, distinct from `flash_status` (which only
  // reflects discovery run as part of an actual flash attempt, or never, if
  // one hasn't happened yet) and distinct from `status` (the WebSocket
  // transport, not the physical board). Since Phase 1 this block is a
  // projection of the shared `device_monitor` state Hack Mode reads too, so
  // the two screens cannot disagree about what is plugged in.
  const socketLinked = status === CONNECTION_STATUS.CONNECTED
  // When the socket isn't open, nothing about the `hardware` block can be
  // trusted (it's whatever was last pushed, possibly never), so the header
  // falls back to "nothing known" rather than reporting a stale board as
  // still attached. `socketLinked` itself is never *shown*: it stays
  // internal, for gating the poll below and for diagnostics.
  const hardware = (socketLinked && state?.hardware) || UNKNOWN_HARDWARE

  // Keeps a truthful LINK as boards are plugged/unplugged while Build Mode
  // stays open, rather than only ever showing whatever was detected at
  // connect time — see CLAUDE.md requirement 7. Skips a poll while a compile
  // or flash is in flight so this never competes with either for the
  // toolchain, and re-checks immediately once the socket (re)connects. Kept
  // running even with no active project — the header must stay truthful
  // while a student waits for a panel to be identified.
  const pollGuardRef = useRef({ isCompiling })
  useEffect(() => {
    pollGuardRef.current = { isCompiling }
  })
  useEffect(() => {
    if (!socketLinked) return undefined
    const poll = () => {
      // The driver, not `chain`: the chain may have advanced since the last
      // render, and a poll must never compete with a submission, compile or
      // upload that is already on the wire.
      if (pollGuardRef.current.isCompiling || driver.isBusy()) return
      sendHardwareStatus()
    }
    poll()
    const id = setInterval(poll, HARDWARE_POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [socketLinked, sendHardwareStatus, driver])

  // The compile half of the General Terminal below — real `arduino-cli
  // compile` output (`compileOutput.stdout`/`stderr`), never simulated. This
  // was already sent by the backend in every `state` frame (`compile_output`)
  // but had nowhere inline to render since the compile toast only ever showed
  // an icon/title; this is the same data, just finally given a terminal.
  const compileOutput = state?.compile_output || null
  const compileTerminalText = submitting
    ? 'Submitting edits…'
    : isCompiling
      ? 'Compiling…'
      : compileStatus === 'not_started'
        ? 'Compile output will appear here once you compile the firmware.'
        : [
            COMPILE_BANNER[compileStatus],
            compileOutput && !compileOutput.success
              ? COMPILE_FAILURE_NOTE[compileOutput.category] || compileOutput.category
              : null,
            compileOutput?.stdout,
            compileOutput?.stderr,
          ]
            .filter(Boolean)
            .join('\n')
  const compileBannerStatus = submitting || isCompiling ? 'running' : compileStatus

  // The Flash Terminal under the code editor — real Arduino CLI/esptool
  // output (`flashOutput.stdout`/`stderr`), never simulated text. Built the
  // same way the old `.flash-result` block was (same banner/device/failure-
  // note copy), just rendered as one scrollable terminal block instead of a
  // separate colored banner plus a nested `<pre>`.
  const flashTerminalText = flashing
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

  // The Validation Terminal, built the same way as the Flash Terminal above
  // — real backend validator output (`validationOutput.message`/`details`),
  // never simulated. `details` is the strategy's own free-form evidence
  // mapping (app/build/validation/models.py: `ValidationResult.details`),
  // rendered as pretty JSON since its shape is validator-specific and not
  // something this frontend should parse.
  const validationTerminalText = validationPending
    ? 'Running validation check…'
    : validationStatus === 'not_started'
      ? 'Validation output will appear here once you run the validation test.'
      : [
          VALIDATION_BANNER[validationStatus],
          validationOutput?.message || null,
          validationOutput && validationOutput.outcome !== 'success'
            ? VALIDATION_OUTCOME_NOTE[validationOutput.outcome] || null
            : null,
          validationOutput?.details && Object.keys(validationOutput.details).length > 0
            ? JSON.stringify(validationOutput.details, null, 2)
            : null,
        ]
          .filter(Boolean)
          .join('\n')

  // GENERAL TERMINAL. Compile, flash and validation used to each get their
  // own always-visible terminal block; this merges their presentation into
  // one, showing whichever of the three is currently running and, once it
  // finishes, keeping that same operation's result on screen until a
  // different one starts — exactly the "stays until superseded" behavior the
  // old Flash/Validation Terminals already had individually. None of the
  // three text/status builders above changed; this only picks between them.
  const compileRunning = submitting || isCompiling
  const validationRunning = validationPending
  const currentOp = flashing ? 'flash' : compileRunning ? 'compile' : validationRunning ? 'validation' : activeOp
  const generalTerminalText =
    currentOp === 'compile'
      ? compileTerminalText
      : currentOp === 'flash'
        ? flashTerminalText
        : currentOp === 'validation'
          ? validationTerminalText
          : 'Output will appear here once you compile, flash, or validate.'
  const generalTerminalBannerStatus =
    currentOp === 'compile'
      ? compileBannerStatus
      : currentOp === 'flash'
        ? flashBannerStatus
        : currentOp === 'validation'
          ? validationBannerStatus
          : 'not_started'
  const generalTerminalStatusText =
    currentOp === 'compile'
      ? compileRunning
        ? 'COMPILING…'
        : BUILD_STATUS_LABEL[compileStatus] || compileStatus
      : currentOp === 'flash'
        ? flashing
          ? 'UPLOADING…'
          : BUILD_STATUS_LABEL[flashStatus] || flashStatus
        : currentOp === 'validation'
          ? validationPending
            ? 'VALIDATING…'
            : BUILD_STATUS_LABEL[validationStatus] || validationStatus
          : BUILD_STATUS_LABEL.not_started

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
    // Also when the Activity log panel is (re)opened, so it opens on the
    // newest entry rather than the first.
  }, [events, sidePanel, leftPanelCollapsed])

  // Leaving the open section while it holds an uncompiled draft is REFUSED,
  // not silently allowed: with no separate save step, switching away would
  // throw the student's blocks away. The same guard covers an in-flight chain,
  // whose socket answers must still land on the section that submitted.
  function canLeaveSection() {
    if (!sectionDirty && !busy) return true
    setChainNotice(SWITCH_BLOCKED_NOTICE)
    return false
  }

  // Shared by every path that leaves whatever section is currently open
  // (switching files, returning to CODE, closing the active workspace tab) —
  // clears the section baseline the same way each of those already did
  // individually. Never called while a dirty/busy section is open; callers
  // gate that with `canLeaveSection()` first.
  function resetOpenSection() {
    setSelectedSectionId(null)
    setSectionData(null)
    syncChain(driver.loadBaseline(null))
    setSectionPreserved([])
    setLegacyTextOpen(false)
    setLegacyDraft(null)
  }

  // A section IS the interaction surface (Phase B8 correction) — clicking
  // one opens it, whatever its policy. LOCKED/EXPLORE need no round trip
  // (their current text is already in `state.files`); only EDITABLE asks
  // the backend for a Blockly representation, since that answer also says
  // whether the toolbox can draw this construct at all. An EDITABLE section
  // also gets a closable tab in the workspace tab strip above the editor
  // (`openWorkspaceTabs`) — LOCKED/EXPLORE sections stay plain in-place
  // highlights in the CODE view and never get one.
  function openSection(regionId) {
    if (regionId === selectedSectionId) return
    const segment = activeSegments.find((s) => s.region_id === regionId)
    if (!segment) return
    if (!canLeaveSection()) return
    setChainNotice('')
    setSelectedSectionId(regionId)
    setSectionData(null)
    syncChain(driver.loadBaseline(null))
    setSectionPreserved([])
    setLegacyTextOpen(false)
    setLegacyDraft(null)
    if (segment.policy === 'editable') {
      setSectionLoading(true)
      sendSectionBlockly(resolvedActiveFile, regionId)
      setOpenWorkspaceTabs((tabs) => (tabs.includes(regionId) ? tabs : [...tabs, regionId]))
    }
  }

  // The workspace tab strip's "Code" tab — returns to the full stacked
  // source view without closing any other open tab.
  function closeWorkspaceView() {
    if (!canLeaveSection()) return
    setChainNotice('')
    resetOpenSection()
  }

  // Closing a tab via its × never silently discards an uncompiled edit: if
  // it is the tab currently open, this goes through the exact same
  // dirty/busy guard `closeWorkspaceView`/`openSection` do before anything
  // is reset. Closing a background tab (not the active one) touches only
  // the tab strip — its section holds no live draft, since only the active
  // section's edits ever reach the chain driver.
  function closeWorkspaceTab(regionId) {
    if (regionId === selectedSectionId) {
      if (!canLeaveSection()) return
      setChainNotice('')
      resetOpenSection()
    }
    setOpenWorkspaceTabs((tabs) => tabs.filter((id) => id !== regionId))
  }

  function switchFile(name) {
    if (name === resolvedActiveFile) return
    if (!canLeaveSection()) return
    setChainNotice('')
    setActiveFile(name)
    resetOpenSection()
    setOpenWorkspaceTabs([])
  }

  // Blockly reported a change: the CURRENT serialized workspace becomes the
  // draft COMPILE will submit.
  function onBlocksChange(workspace) {
    syncChain(driver.edit({ workspace }))
  }

  function openLegacyText(segment) {
    setLegacyTextOpen(true)
    setLegacyDraft(segment.text || '')
    // Opening the text editor is not an edit; typing in it is.
    syncChain(
      driver.loadBaseline({
        kind: 'text',
        path: resolvedActiveFile,
        sectionId: segment.region_id,
        source: segment.text || '',
      }),
    )
  }

  function onLegacyTextChange(source) {
    setLegacyDraft(source)
    syncChain(driver.edit({ source }))
  }

  // COMPILE and FLASH are the same chain with a different final step — see
  // src/build/compileChain.js. Starting one clears the previous attempt's
  // messages, so a rejection from an earlier attempt can never be mistaken
  // for the verdict on this one.
  function startChain(intent) {
    if (busy || driver.isBusy() || isCompiling || validationPending) return
    // Report any canvas change Blockly has not delivered yet, synchronously,
    // so the draft the chain submits below is the CURRENT workspace.
    blocklyApiRef.current?.flush()
    setProtocolError('')
    setChainNotice('')
    syncChain(driver.request(intent, flashReady))
  }

  function compile() {
    startChain(CHAIN_INTENT.COMPILE)
  }

  function flash() {
    if (!canFlash) return
    startChain(CHAIN_INTENT.FLASH)
  }

  function validate() {
    if (!canValidate) return
    setValidationPending(true)
    sendValidate()
  }

  function dismissCompileToast() {
    setShowSuccessToast(false)
    setFailureToastDismissed(true)
  }

  function flashHint() {
    if (canFlash) {
      return flashNeedsCompile
        ? 'Compile the current blocks, then upload them to the connected ESP32'
        : 'Upload the compiled firmware to the connected ESP32'
    }
    if (flashing) return 'A flash is already in progress'
    if (busy || isCompiling) return 'Wait for the current compilation to finish'
    if (validationPending) return 'Wait for the validation check to finish'
    return 'Flash is not available right now'
  }

  function validationHint() {
    if (canValidate) return 'Run the validation check against the flashed firmware'
    if (validationPending) return 'A validation check is already running'
    if (flashing) return 'Wait for the current flash to finish'
    if (busy || isCompiling) return 'Wait for the current compilation to finish'
    if (flashStatus !== 'succeeded') return 'Flash the compiled firmware to the device before validating'
    if (!flashReady) {
      return 'The firmware changed since the last flash — compile and flash again before validating'
    }
    return 'Flash the compiled firmware successfully before validating'
  }

  // NO-DEVICE CORRECTION. `has_active_project` is False for every connection
  // whose panel did not resolve to a real activity — no board, an
  // unidentified/unregistered board, a registered panel with no courseware,
  // or firmware that could not be materialized (see
  // backend/app/build/no_device.py and backend/app/build_project_selection.py).
  // There is no default activity in this state: no panel selected, no
  // firmware loaded, no Blockly workspace — only the shared hardware readout,
  // so the PANEL/USB/CONNECTED status keeps updating while a student waits.
  if (state && !hasActiveProject) {
    return (
      <div className="screen screen-fixed screen-dotted">
        <AppHeader title="Build Mode" onMenu={onMenu} />
        <main className="prep-body">
          <section className="prep-card">
            <h2>No ESP32 detected</h2>
            <p className="prep-sub">Build Mode loads a panel&apos;s activity from the physically attached ESP32.</p>
            <p className="prep-sub">
              Connect a panel over USB and wait for it to be identified to load its firmware here.
            </p>
            <div className="prep-actions">
              <button type="button" className="btn" onClick={onQuit}>
                <Icon name="arrow-left" size={14} />
                Back to menu
              </button>
            </div>
          </section>
        </main>
        <StatusBar>
          <HardwareHeaderStatus hardware={hardware} />
        </StatusBar>
      </div>
    )
  }

  const project = state?.project
  const projectName = project?.module_id || project?.scenario_id || 'project'
  const boardName = project?.board?.name
  const sidePanelDef = SIDE_PANELS.find((p) => p.id === sidePanel) || SIDE_PANELS[0]

  // The Blockly switch opens the workspace of the most recently opened
  // editable section, or else the first editable one. With no editable section
  // there is no canvas to open, so it is disabled rather than faked.
  const blocklyTarget =
    openWorkspaceTabs[openWorkspaceTabs.length - 1] ??
    activeSegments.find((s) => s.policy === 'editable')?.region_id ??
    null

  // The rail chooses the left column's panel; pressing the open panel's own
  // button collapses the column to hand its width to the editor.
  function pickSidePanel(id) {
    if (id === sidePanel && !leftPanelCollapsed) {
      setLeftPanelCollapsed(true)
    } else {
      setSidePanel(id)
      setLeftPanelCollapsed(false)
    }
  }

  const compileLabel =
    (busy && chain.intent === CHAIN_INTENT.COMPILE) || (!busy && isCompiling) ? 'Compiling…' : 'Compile'
  const flashLabel = flashing
    ? 'Flashing…'
    : busy && chain.intent === CHAIN_INTENT.FLASH
      ? 'Compiling before flash…'
      : 'Flash'

  const compileDetail =
    compileBannerStatus === 'running'
      ? 'Compiling…'
      : compileBannerStatus === 'succeeded'
        ? compileOutput?.duration_seconds != null
          ? `Built in ${compileOutput.duration_seconds} s`
          : 'Built'
        : compileBannerStatus === 'failed'
          ? 'Failed — see terminal'
          : 'Not started'
  const flashDetail =
    flashBannerStatus === 'running' || flashBannerStatus === 'detecting'
      ? 'Uploading…'
      : flashBannerStatus === 'succeeded'
        ? flashOutput?.port
          ? `Uploaded to ${flashOutput.port}`
          : 'Uploaded'
        : flashBannerStatus === 'no_device'
          ? 'No device detected'
          : flashBannerStatus === 'failed'
            ? 'Failed — see terminal'
            : canFlash && flashReady
              ? 'Ready to flash'
              : 'Not started'
  const validationDetail =
    validationBannerStatus === 'running'
      ? 'Validating…'
      : validationBannerStatus === 'succeeded'
        ? 'Remediation validated'
        : validationBannerStatus === 'failed'
          ? validationOutput?.outcome === 'error'
            ? 'Check did not complete'
            : 'Requirement not met'
          : 'Not started'

  return (
    <div className="screen screen-fixed">
      <AppHeader
        title="Build Mode"
        onMenu={onMenu}
        actions={
          <>
            <div className="session-actions is-inline" role="group" aria-label="Session">
              <button
                type="button"
                className="btn"
                title="End this session and start a new one from the baseline"
                onClick={onReset}
              >
                <Icon name="refresh" size={14} />
                Reset
              </button>
              <button type="button" className="btn" title="End this session and return to the main menu" onClick={onQuit}>
                <Icon name="arrow-left" size={14} />
                Quit
              </button>
            </div>
            <div className="seg-group is-compact" role="group" aria-label="Editor mode">
              <button
                type="button"
                className={`seg${workspaceActive ? '' : ' is-on'}`}
                aria-pressed={!workspaceActive}
                onClick={workspaceActive ? closeWorkspaceView : undefined}
              >
                C++
              </button>
              <button
                type="button"
                className={`seg${workspaceActive ? ' is-on' : ''}`}
                aria-pressed={workspaceActive}
                disabled={!blocklyTarget}
                title={blocklyTarget ? 'Edit the open section with Blockly blocks' : 'This file has no editable section'}
                onClick={workspaceActive ? undefined : () => openSection(blocklyTarget)}
              >
                Blockly
              </button>
            </div>
            <button
              type="button"
              className="btn"
              disabled={busy || isCompiling || validationPending || !hasActiveProject}
              onClick={compile}
            >
              <Icon name="play" size={16} />
              {compileLabel}
            </button>
            <button type="button" className="btn btn-primary" disabled={!canFlash} onClick={flash} title={flashHint()}>
              <Icon name="bolt" size={16} />
              {flashLabel}
            </button>
          </>
        }
      />

      <main className="screen-body build-body">
        <nav className="card build-rail" aria-label="Workspace panels">
          {SIDE_PANELS.map((panel) => {
            const open = panel.id === sidePanel && !leftPanelCollapsed
            return (
              <button
                key={panel.id}
                type="button"
                className={`icon-btn is-quiet${open ? ' is-on' : ''}`}
                aria-label={panel.label}
                aria-pressed={open}
                title={panel.label}
                onClick={() => pickSidePanel(panel.id)}
              >
                <Icon name={panel.icon} size={20} />
              </button>
            )
          })}
        </nav>

        {leftPanelCollapsed ? null : (
          <aside className="card build-side" aria-label={sidePanelDef.label}>
            <div className="side-head">
              <h2>{sidePanelDef.label}</h2>
              <button
                type="button"
                className="icon-btn is-quiet is-sm"
                aria-label="Collapse panel"
                title="Collapse panel"
                onClick={() => setLeftPanelCollapsed(true)}
              >
                <Icon name="panel-collapse" size={14} />
              </button>
            </div>

            {sidePanel === 'project' ? (
              <div className="side-body">
                <div className="tree">
                  <p className="tree-row is-folder">
                    <Icon name="chevron-down" size={10} />
                    <Icon name="folder" size={16} />
                    <span>{projectName}</span>
                  </p>
                  <div className="tree-children">
                    {fileNames.map((name) => (
                      <button
                        key={name}
                        type="button"
                        className={`tree-row${name === resolvedActiveFile ? ' is-on' : ''}`}
                        onClick={() => switchFile(name)}
                      >
                        <Icon name={fileIcon(name)} size={16} />
                        <span>{name}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <p className="side-caption">Sections</p>
                {activeSegments.length === 0 ? (
                  <p className="muted-note">Loading firmware…</p>
                ) : (
                  <ul className="section-list">
                    {activeSegments.map((segment) => {
                      const policy = segment.policy || 'locked'
                      return (
                        <li key={segment.region_id}>
                          <button
                            type="button"
                            className={`section-row is-${policy}${segment.region_id === selectedSectionId ? ' is-on' : ''}`}
                            title={`${segment.region_id} (${POLICY_LABEL[policy] || policy})`}
                            onClick={() => openSection(segment.region_id)}
                          >
                            <Icon name={POLICY_ICON[policy] || 'lock'} size={14} />
                            <span className="section-name">{segment.region_id}</span>
                            <span className={`policy-tag is-${policy}`}>{POLICY_LABEL[policy] || policy}</span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            ) : null}

            {sidePanel === 'brief' ? (
              <div className="side-body brief">
                <p className="eyebrow">Scenario</p>
                <p className="brief-value">{project?.scenario_id || 'connecting…'}</p>
                {project?.module_id ? <p className="muted-note">{project.module_id}</p> : null}
                {/* Phase 1.2 — what the remediation must achieve, so a student
                    can read this before running validation rather than
                    discovering the requirement only from a failed check.
                    Verbatim from the panel's own `RemediationSpec`; absent
                    when the package declares no remediation activity yet. */}
                {remediation ? (
                  <>
                    <section>
                      <h3>Vulnerability</h3>
                      <p>{remediation.vulnerability}</p>
                    </section>
                    <section>
                      <h3>Remediation goal</h3>
                      <p>{remediation.remediation_goal}</p>
                    </section>
                    <section>
                      <h3>Validation requirement</h3>
                      <p>{remediation.validation_requirement}</p>
                    </section>
                  </>
                ) : (
                  <p className="empty-line">
                    <Icon name="info" size={14} /> No remediation activity is defined for this panel yet.
                  </p>
                )}
              </div>
            ) : null}

            {sidePanel === 'activity' ? (
              <div className="side-body activity-body">
                {/* Bounded + independently scrollable. Timestamps are
                    session-elapsed HH:MM:SS (formatElapsedSince), not
                    time-of-day; see the module-level comment above. */}
                <div className="activity-log-scroll" ref={activityLogRef} onScroll={handleActivityLogScroll}>
                  <ul className="activity-log">
                    {events.length === 0 ? (
                      <li className="empty-line">No activity yet.</li>
                    ) : (
                      events.map((entry, i) => (
                        <li key={i}>
                          <span className="log-time">{entry.at}</span>
                          <span>{EVENT_LABELS[entry.event] || entry.event}</span>
                        </li>
                      ))
                    )}
                  </ul>
                </div>
              </div>
            ) : null}
          </aside>
        )}

        <div className="build-center">
          <section className="card build-editor">
            {/* File tabs, plus one closable tab per EDITABLE section a student
                has opened. A file tab shows that file's full stacked source;
                a section tab shows that section's own Blockly workspace. Only
                the open section is ever mounted, and nothing here changes the
                single-open-section model (`selectedSectionId`/`sectionData`). */}
            <div className="tab-strip" role="tablist" aria-label="Open files">
              {fileNames.map((name) => (
                <button
                  key={name}
                  type="button"
                  role="tab"
                  aria-selected={!workspaceActive && name === resolvedActiveFile}
                  className={`tab${!workspaceActive && name === resolvedActiveFile ? ' is-on' : ''}`}
                  onClick={() => {
                    if (name !== resolvedActiveFile) switchFile(name)
                    else if (workspaceActive) closeWorkspaceView()
                  }}
                >
                  <Icon name={fileIcon(name)} size={14} />
                  {name}
                </button>
              ))}
              {openWorkspaceTabs.map((id) => {
                const active = id === selectedSectionId && workspaceActive
                return (
                  <span key={id} className={`tab tab-workspace${active ? ' is-on' : ''}`}>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={active}
                      className="tab-label"
                      onClick={() => openSection(id)}
                    >
                      <Icon name="rail-blocks" size={14} />
                      {id}
                    </button>
                    <button
                      type="button"
                      className="tab-close"
                      aria-label={`Close ${id} workspace`}
                      onClick={() => closeWorkspaceTab(id)}
                    >
                      <Icon name="close" size={10} />
                    </button>
                  </span>
                )
              })}
            </div>

            {workspaceActive ? (
              // THE OPEN EDITABLE SECTION'S OWN WORKSPACE TAB. Replaces the
              // stacked source view entirely while open — only the active
              // tab's workspace is ever mounted — and returning to the file
              // tab brings the source view back unchanged.
              <div className="code-workspace">
                {sectionLoading || !sectionData ? (
                  <p className="muted-note">Loading section…</p>
                ) : sectionData.representable ? (
                  // THE INTENDED EDITING PATH. Blockly is the source of
                  // truth for this section — the generated C++ is an output
                  // of it, never something typed here. `key={selectedSectionId}`
                  // remounts the canvas (and reloads `sectionData.workspace`)
                  // whenever the student opens a different section; see
                  // BlocklyWorkspace.jsx.
                  <div className="blockly-panel">
                    <BlocklyWorkspace
                      key={selectedSectionId}
                      initialWorkspaceState={sectionData.workspace}
                      onWorkspaceChange={onBlocksChange}
                      apiRef={blocklyApiRef}
                    />
                  </div>
                ) : (
                  // Understood by the backend as EDITABLE, but the toolbox
                  // has no vocabulary for this construct yet — refused as a
                  // block edit, never faked. The legacy raw-text path stays
                  // available so the section is still completable meanwhile;
                  // it is not offered for any section the toolbox CAN draw.
                  <>
                    {!legacyTextOpen ? (
                      <>
                        <CppCode code={selectedSegment.text} className="code-segment" />
                        <button type="button" className="btn btn-sm" onClick={() => openLegacyText(selectedSegment)}>
                          Edit as text (legacy)
                        </button>
                      </>
                    ) : (
                      <textarea
                        className="code-editor-full"
                        spellCheck={false}
                        value={legacyDraft ?? ''}
                        onChange={(e) => onLegacyTextChange(e.target.value)}
                      />
                    )}
                  </>
                )}
              </div>
            ) : (
              // THE COMPLETE .ino IS THE PRIMARY WORKSPACE. Every discovered
              // section renders here, in document order, all the time — a
              // student reads the whole firmware for context and clicks a
              // section directly in place to open its workspace tab (an
              // EDITABLE one) or just highlight it in place (LOCKED/EXPLORE).
              // Text is always the plain, current text from `state.files`.
              <div className="code-view">
                {activeSegments.length === 0 ? (
                  <p className="muted-note">Loading firmware…</p>
                ) : (
                  numberSegments(activeSegments).map(({ segment, start, lines }) => {
                    const policy = segment.policy || 'locked'
                    const isSelected = segment.region_id === selectedSectionId
                    return (
                      <div
                        key={segment.region_id}
                        className={`code-section is-${policy}${isSelected ? ' is-selected' : ''}`}
                        role="button"
                        tabIndex={0}
                        aria-label={`Open ${segment.region_id} (${POLICY_LABEL[policy] || policy})`}
                        onClick={() => openSection(segment.region_id)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            openSection(segment.region_id)
                          }
                        }}
                      >
                        <span className={`policy-tag is-${policy}`}>
                          <Icon name={POLICY_ICON[policy] || 'lock'} size={12} />
                          {POLICY_LABEL[policy] || policy}
                        </span>
                        <div className="code-lines">
                          <pre className="code-gutter" aria-hidden="true">
                            {lines.map((_, i) => start + i).join('\n')}
                          </pre>
                          <CppCode code={lines.join('\n')} className="code-segment" />
                        </div>
                      </div>
                    )
                  })
                )}
              </div>
            )}

            {/* Every action button (COMPILE / FLASH / validation) lives in the
                top bar and the pipeline strip. This is the workspace's
                submission-state readout, plus why the last COMPILE/FLASH chain
                stopped short, if it did. */}
            {protocolError || sectionDirty || chainNotice ? (
              <div className="build-notices">
                {protocolError ? (
                  <div className="notice is-danger" role="alert">
                    <Icon name="warning" size={14} />
                    {protocolError}
                  </div>
                ) : sectionDirty ? (
                  <div className="notice is-warning">
                    <Icon name="warning" size={14} />
                    Edits not compiled yet — Compile submits and builds them.
                  </div>
                ) : null}
                {chainNotice ? (
                  <div className="notice is-warning">
                    <Icon name="warning" size={14} />
                    {chainNotice}
                  </div>
                ) : null}
              </div>
            ) : null}

            <div className="editor-foot">
              <span className="crumbs">
                {projectName}
                <Icon name="chevron-right" size={10} />
                <b>{resolvedActiveFile || 'connecting…'}</b>
              </span>
              <span className="editor-foot-meta">
                <span>C++ (Arduino)</span>
                {boardName ? <span>{boardName}</span> : null}
              </span>
            </div>

            {/* GENERAL TERMINAL — compile, flash and validation output merged
                into one compact, bounded, scrollable terminal under the
                editor. It shows whichever of the three is currently running
                and keeps that operation's result on screen afterwards until a
                different one starts — see the `activeOp`/`currentOp`
                derivation above. Real backend output only, never simulated. */}
            <div className="general-terminal">
              <button
                type="button"
                className="terminal-toggle"
                aria-expanded={terminalOpen}
                onClick={() => setTerminalOpen((open) => !open)}
              >
                <span className="terminal-toggle-label">
                  <Icon name={terminalOpen ? 'caret-down' : 'caret-up'} size={10} />
                  General terminal{currentOp ? ` — ${GENERAL_TERMINAL_LABEL[currentOp]}` : ''}
                </span>
                <span className={`chip ${(STATUS_VISUAL[generalTerminalBannerStatus] || STATUS_VISUAL.not_started).tone}`}>
                  {generalTerminalStatusText}
                </span>
              </button>
              {terminalOpen ? <pre className="terminal-output">{generalTerminalText}</pre> : null}
            </div>
          </section>

          <section className="card build-pipeline" aria-label="Build pipeline">
            <ol className="pipeline">
              <PipelineStep status={compileBannerStatus} title="Compile" detail={compileDetail} />
              <li className={`pipeline-link${compileBannerStatus === 'succeeded' ? ' is-done' : ''}`} aria-hidden="true" />
              <PipelineStep status={flashBannerStatus} title="Flash" detail={flashDetail} />
              <li className={`pipeline-link${flashBannerStatus === 'succeeded' ? ' is-done' : ''}`} aria-hidden="true" />
              <PipelineStep status={validationBannerStatus} title="Verify" detail={validationDetail} />
            </ol>
            <button type="button" className="btn" disabled={!canValidate} onClick={validate} title={validationHint()}>
              <Icon name="shield" size={16} />
              {validationPending ? 'Validating…' : 'Run validation'}
            </button>
          </section>
        </div>
      </main>

      <StatusBar>
        <HardwareHeaderStatus hardware={hardware} />
      </StatusBar>

      {/* Small bottom-right compile notification. Floats above the layout
          (position: fixed) rather than taking up workspace; the full compiler
          output is in the General Terminal. */}
      {compileToastKind && (
        <div className={`toast is-${compileToastKind}`} role="status">
          <button
            type="button"
            className="icon-btn is-quiet is-sm toast-dismiss"
            onClick={dismissCompileToast}
            aria-label="Dismiss notification"
          >
            <Icon name="close" size={10} />
          </button>
          <strong>
            <Icon name={COMPILE_TOAST_CONTENT[compileToastKind].icon} size={16} />
            {COMPILE_TOAST_CONTENT[compileToastKind].title}
          </strong>
          <span>{compileToastKind === 'failed' ? 'Check compiler errors' : primaryFileName}</span>
        </div>
      )}
    </div>
  )
}
