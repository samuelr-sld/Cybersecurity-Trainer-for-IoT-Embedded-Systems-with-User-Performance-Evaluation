import { useEffect, useRef, useState } from 'react'
import AppHeader from '../components/AppHeader'
import BlocklyWorkspace from '../components/BlocklyWorkspace'
import CppCode from '../components/CppCode'
import { restoreFromSessionFrame } from '../build/sessionRestore'
import useBuildSocket, { CONNECTION_STATUS } from '../hooks/useBuildSocket'
// Shared with Hack Mode rather than kept private here: both screens render
// the SAME backend device state (one `device_monitor` per process, see
// backend/app/hardware/) through the SAME header component, so the two
// cannot drift. The mode name passed to AppHeader is the only difference
// between this header and Hack Mode's.
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
  running: { icon: '⚙', title: 'Compiling…' },
  succeeded: { icon: '✓', title: 'Compilation successful' },
  failed: { icon: '✕', title: 'Compilation failed' },
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

export default function BuildMode({ onBack, onMenu, participantId }) {
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
  }, [events])

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
  // firmware loaded, no Blockly workspace — only the shared hardware header,
  // so the PANEL/USB/CONNECTED status keeps updating while a student waits.
  if (state && !hasActiveProject) {
    return (
      <div className="page">
        <AppHeader title="BUILD MODE" right={<HardwareHeaderStatus hardware={hardware} />} onMenu={onMenu} />
        <main className="page-body no-device-body">
          <div className="no-device-message">
            <h2>NO ESP32 DETECTED</h2>
            <p>Build Mode loads a panel's activity from the physically attached ESP32.</p>
            <p>Connect a panel over USB and wait for it to be identified to load its firmware here.</p>
          </div>
        </main>
        <footer className="link-footer">
          <button type="button" onClick={onBack}>
            ← BACK TO MENU
          </button>
          <span>BUILD MODE — WRITE, COMPILE, AND FLASH FIRMWARE.</span>
          <span />
        </footer>
      </div>
    )
  }

  return (
    <div className="page">
      <AppHeader
        title="BUILD MODE"
        // The uniform panel/hardware header. Identical component and
        // identical shared device state in Hack Mode — the mode name above
        // is the only difference between the two headers.
        right={<HardwareHeaderStatus hardware={hardware} />}
        onMenu={onMenu}
      />
      <nav className="ide-menu" aria-label="IDE menu">
        <span>File</span>
        <span>Edit</span>
        <span>Sketch</span>
        <span>Board: {state?.project?.board?.name || '—'}</span>
        <span>Help</span>
      </nav>
      <main className={`page-body ide-grid ${leftPanelCollapsed ? 'left-collapsed' : ''}`}>
        <aside className={`side-col ${leftPanelCollapsed ? 'collapsed' : ''}`}>
          {/* Collapsible left column — not a second sidebar: collapsing this
              one hands its width to the code/Blockly workspace via
              `.ide-grid.left-collapsed` in App.css. The Blockly canvas's own
              ResizeObserver (BlocklyWorkspace.jsx) reacts to the resulting
              size change on its own. */}
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
                <p className="file-active">{state?.project?.scenario_id || 'connecting…'}</p>
                <p className="muted-note">{state?.project?.module_id}</p>
                {/* Phase 1.2 — what the remediation must achieve, so a
                    student can read this before pressing RUN VALIDATION
                    TEST rather than discovering the requirement only from a
                    failed check. Absent (four of five panels) when the
                    package declares no remediation activity yet. */}
                {remediation && (
                  <>
                    <p className="muted-note">
                      <strong>Vulnerability:</strong> {remediation.vulnerability}
                    </p>
                    <p className="muted-note">
                      <strong>Remediation goal:</strong> {remediation.remediation_goal}
                    </p>
                    <p className="muted-note">
                      <strong>Validation requirement:</strong> {remediation.validation_requirement}
                    </p>
                  </>
                )}
              </section>
              {/* The old FIRMWARE SECTIONS panel (region names + LOCKED/
                  EDITABLE badges) was removed as student-facing UI — the
                  underlying model is untouched, and every section is still
                  directly clickable in place below via the same
                  `openSection`/POLICY_LABEL (Phase B8's "the section IS the
                  interaction surface" still holds, it just has one surface
                  now instead of two). The file switcher that used to share
                  this panel moved into the editor pane below. The old
                  BUILD STATUS panel (Compile/Flash/Validation NOT STARTED
                  pills) was removed the same way: `compile_status`/
                  `flash_status`/`validation_status` still drive button
                  enablement and the General Terminal below exactly as
                  before — only this standing, mostly-empty status readout
                  is gone. */}
              {/* The Build/Development action controls, in the existing
                  left-hand information column. There is deliberately no
                  SAVE control: COMPILE is the point at which an edit is
                  submitted, ownership-checked and persisted, and FLASH runs
                  that same chain first whenever the build is out of date
                  (src/build/compileChain.js). */}
              <section className="panel">
                <h3>BUILD ACTIONS</h3>
                <div className="build-actions-list">
                  <button
                    type="button"
                    className="btn-solid"
                    disabled={busy || isCompiling || validationPending || !hasActiveProject}
                    onClick={compile}
                  >
                    {(busy && chain.intent === CHAIN_INTENT.COMPILE) || (!busy && isCompiling)
                      ? '… COMPILING'
                      : '▶ COMPILE'}
                  </button>
                  <button
                    type="button"
                    className="btn-outline"
                    disabled={!canFlash}
                    onClick={flash}
                    title={flashHint()}
                  >
                    {flashing
                      ? '… FLASHING'
                      : busy && chain.intent === CHAIN_INTENT.FLASH
                        ? '… COMPILING BEFORE FLASH'
                        : '▲ FLASH'}
                  </button>
                  <button
                    type="button"
                    className="btn-outline"
                    disabled={!canValidate}
                    onClick={validate}
                    title={validationHint()}
                  >
                    {validationPending ? '… VALIDATING' : 'RUN VALIDATION TEST'}
                  </button>
                </div>
              </section>
            </>
          )}
        </aside>
        <section className="editor">
          {/* The Blockly workspace tab strip — the existing single-open-
              section model (`selectedSectionId`/`sectionData`, unchanged
              above) presented as browser-style tabs: CODE is always the
              first, non-closable tab and returns to the full stacked
              source view; each EDITABLE section a student has opened gets
              its own closable tab next to it. Only ever shown once at
              least one section has been opened, so an untouched file stays
              exactly as clean as the CODE view alone. */}
          {openWorkspaceTabs.length > 0 && (
            <div className="workspace-tabs" role="tablist" aria-label="Editor view">
              <button
                type="button"
                role="tab"
                aria-selected={!workspaceActive}
                className={!workspaceActive ? 'active' : ''}
                onClick={closeWorkspaceView}
              >
                Code
              </button>
              {openWorkspaceTabs.map((id) => (
                <span
                  key={id}
                  className={`workspace-tab ${id === selectedSectionId && workspaceActive ? 'active' : ''}`}
                >
                  <button
                    type="button"
                    role="tab"
                    aria-selected={id === selectedSectionId && workspaceActive}
                    className="workspace-tab-label"
                    onClick={() => openSection(id)}
                  >
                    {id}
                  </button>
                  <button
                    type="button"
                    className="workspace-tab-close"
                    aria-label={`Close ${id} workspace`}
                    onClick={() => closeWorkspaceTab(id)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          {workspaceActive ? (
            // THE OPEN EDITABLE SECTION'S OWN WORKSPACE TAB. Replaces the
            // stacked source view entirely while open — only the active
            // tab's workspace is ever mounted — and the CODE tab above
            // returns to it unchanged.
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
                <>
                  <div className="blockly-panel">
                    <BlocklyWorkspace
                      key={selectedSectionId}
                      initialWorkspaceState={sectionData.workspace}
                      onWorkspaceChange={onBlocksChange}
                      apiRef={blocklyApiRef}
                    />
                  </div>
                </>
              ) : (
                // Understood by the backend as EDITABLE, but the toolbox
                // has no vocabulary for this construct yet — refused as a
                // block edit, never faked. The legacy raw-text path stays
                // available so the section is still completable meanwhile;
                // it is not offered for any section the toolbox CAN draw.
                <>
                  {!legacyTextOpen ? (
                    <>
                      <CppCode code={selectedSegment.text} className="code-segment locked" />
                      <button type="button" className="btn-outline" onClick={() => openLegacyText(selectedSegment)}>
                        EDIT AS TEXT (LEGACY)
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
            <div className="code-view">
              {/* The file switcher, relocated here from the removed FIRMWARE
                  SECTIONS panel — same `switchFile` call and `.file-list`
                  markup, unchanged, just no longer sharing a panel with the
                  section/policy list below. */}
              {fileNames.length > 0 && (
                <ul className="file-list">
                  {fileNames.map((name) => (
                    <li key={name}>
                      <button
                        type="button"
                        className={name === resolvedActiveFile ? 'active' : ''}
                        onClick={() => switchFile(name)}
                      >
                        {name}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {/* THE COMPLETE .ino IS THE PRIMARY WORKSPACE. Every discovered
                  section renders here, in document order, all the time — a
                  student reads the whole firmware for context and clicks a
                  section directly in place to open its workspace tab (an
                  EDITABLE one) or just highlight it in place (LOCKED/
                  EXPLORE). Text is always the plain, current text from
                  `state.files` — the open EDITABLE section's live editing
                  surface now lives in its own tab pane above, not inline
                  here, so the rest of the file never disappears around
                  it. */}
              <p className="file-active">{resolvedActiveFile || 'connecting…'}</p>
              {activeSegments.length === 0 ? (
                <p className="muted-note">Loading firmware…</p>
              ) : (
                activeSegments.map((segment) => {
                  const isSelected = segment.region_id === selectedSectionId
                  return (
                    <div
                      key={segment.region_id}
                      className={`code-section ${segment.policy} ${isSelected ? 'selected' : ''}`}
                      role="button"
                      tabIndex={0}
                      aria-label={`Open ${segment.region_id}`}
                      onClick={() => openSection(segment.region_id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          openSection(segment.region_id)
                        }
                      }}
                    >
                      <CppCode code={segment.text} className="code-segment locked" />
                    </div>
                  )
                })
              )}
            </div>
          )}
          {/* Every action button (COMPILE/FLASH/validation/security test)
              lives in the left-hand BUILD ACTIONS panel — see above. This bar
              is the workspace's submission-state readout, plus why the last
              COMPILE/FLASH chain stopped short, if it did. */}
          {(protocolError || sectionDirty || chainNotice) && (
            <div className="console">
              {(protocolError || sectionDirty) && (
                <div className={`console-out ${protocolError ? 'warn' : ''}`}>
                  {protocolError
                    ? `✗ ${protocolError}`
                    : '● edits not compiled yet — COMPILE submits and builds them'}
                </div>
              )}
              {chainNotice && <div className="console-out warn">{chainNotice}</div>}
            </div>
          )}
          {/* GENERAL TERMINAL — the former separate Flash Terminal and
              Validation Terminal (plus compile output, which previously had
              no inline home at all) merged into one compact, bounded,
              scrollable terminal under the editor. It shows whichever of
              compile/flash/validation is currently running, and keeps that
              operation's result on screen afterwards until a different one
              starts — see the `activeOp`/`currentOp` derivation above. Real
              backend output only, never simulated; still reuses the exact
              same `.flash-terminal` styling and the untouched
              compile/flash/validation text builders — only the presentation
              is combined. */}
          <div className={`flash-terminal ${generalTerminalBannerStatus}`}>
            <button
              type="button"
              className="flash-terminal-header flash-terminal-toggle"
              aria-expanded={terminalOpen}
              onClick={() => setTerminalOpen((open) => !open)}
            >
              <span>
                {terminalOpen ? '▼' : '▲'} GENERAL TERMINAL{currentOp ? ` — ${GENERAL_TERMINAL_LABEL[currentOp]}` : ''}
              </span>
              <span className="status-pill">{generalTerminalStatusText}</span>
            </button>
            {terminalOpen && <pre className="flash-terminal-body">{generalTerminalText}</pre>}
          </div>
        </section>
        <aside className="side-col">
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
