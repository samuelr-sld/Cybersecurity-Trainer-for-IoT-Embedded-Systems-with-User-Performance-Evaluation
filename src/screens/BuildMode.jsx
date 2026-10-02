import { useEffect, useReducer, useRef, useState } from 'react'
import ActivityLog from '../components/ActivityLog'
import AppHeader from '../components/AppHeader'
import BlocklyWorkspace from '../components/BlocklyWorkspace'
import BuildBrief from '../components/BuildBrief'
import CppCode from '../components/CppCode'
import Icon from '../components/Icon'
import StatusBar from '../components/StatusBar'
import TerminalDock from '../components/TerminalDock'
import { formatElapsed, restoreFromSessionFrame } from '../build/sessionRestore'
import { VALIDATION_UNDEFINED_NOTE, validationDefined } from '../build/buildControls'
import { splitExtension } from '../build/fileName'
import { numberSegments } from '../build/sectionLines'
import { POLICY_HINT, POLICY_ICON, POLICY_LABEL, policyOf, sectionLabel } from '../build/sectionNav'
import { INITIAL_DOCK, dockReducer, terminalRevealedBy } from '../build/terminalDock'
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

// The Terminal's status chip names whichever operation it is currently showing
// (see `currentOp` below); with nothing run yet there is no chip.
const TERMINAL_OP_LABEL = { compile: 'COMPILE', flash: 'FLASH', validation: 'VALIDATION' }

// Backend `CompileStatus` -> the Terminal's banner copy while it is showing a
// compile, mirroring FLASH_BANNER's shape below.
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

// Backend `ValidationStatus` -> the Terminal's banner copy while it is showing
// a validation (Phase B7). `ValidationStatus` has no `detecting`/`no_device`
// members — a validation never touches device discovery, it judges firmware
// already on the board — so this map only ever needs the three states below.
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

// How the Terminal's status chip looks for each backend status. The status WORD
// is always in the chip (BUILD_STATUS_LABEL), so colour is never the only cue.
const STATUS_VISUAL = {
  not_started: { icon: 'status-pending', tone: 'is-muted' },
  detecting: { icon: 'status-partial', tone: 'is-warning' },
  running: { icon: 'status-partial', tone: 'is-warning' },
  succeeded: { icon: 'status-done', tone: 'is-success' },
  failed: { icon: 'status-failed', tone: 'is-danger' },
  no_device: { icon: 'status-failed', tone: 'is-danger' },
}

// The rail's panels. The rail only chooses which one the side column shows;
// every panel is a view of state this screen already holds. (The reference's
// further rail slots — run, terminal, device — have no real function yet, so
// they are not drawn.)
const SIDE_PANELS = [
  { id: 'project', icon: 'rail-files', label: 'Project' },
  { id: 'brief', icon: 'rail-checklist', label: 'Build Brief' },
  { id: 'activity', icon: 'rail-timeline', label: 'Activity log' },
]

// The Activity log's clock is SESSION ELAPSED TIME (HH:MM:SS since this Build
// Mode session's own `session` frame arrived — see `sessionStartRef`), never a
// time of day: nothing server-side stamps a `BuildEvent`. Both are only ever
// called from socket handlers, never during render — module-level so that is
// obvious to the React Compiler's purity check, which cannot see that
// `useBuildSocket` invokes its handlers from socket callbacks.
function sessionClockNow() {
  return Date.now()
}

function elapsedSince(startMs) {
  // `startMs` is null only in the instant before `onSession` has set it, which
  // no logged event can observe (the `session` frame always precedes any
  // `event` frame); fall back to "just started" rather than a huge value.
  return formatElapsed((Date.now() - (startMs ?? Date.now())) / 1000)
}

function fileIcon(name) {
  return name.endsWith('.ino') ? 'file-sketch' : 'file'
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
  // THE SIDE COLUMN. `sidePanel` is which of the rail's panels (SIDE_PANELS) it
  // shows; whether it is open is remembered PER EDITOR MODE. C++ opens with the
  // Project panel beside the source, as in the reference. Blockly opens with
  // the column closed, so Blockly's own toolbox is the left column of the
  // canvas and the canvas keeps the width — opening a panel there is the
  // student's choice, and pressing the rail's open button (or the Blocks
  // toolbox button) closes it again.
  const [sidePanel, setSidePanel] = useState('project')
  const [sideOpenCpp, setSideOpenCpp] = useState(true)
  const [sideOpenBlockly, setSideOpenBlockly] = useState(false)
  // Whether Blockly's own toolbox (the block palette) is shown on a canvas. A
  // presentation toggle driven by the rail's Blocks toolbox button, independent
  // of the side column above — hiding one never hides the other. Remembered
  // across section switches and C++/Blockly switches; Reset or re-entering the
  // mode remounts this screen, which starts it shown again.
  const [toolboxVisible, setToolboxVisible] = useState(true)
  // The moment THIS Build Mode session started, for the Activity log's elapsed
  // timestamps — set in `onSession` below (never during render: `Date.now()` is
  // impure), so a fresh `/ws/build` connection always restarts at 00:00:00 and
  // a resumed one continues from its real age.
  const sessionStartRef = useRef(null)
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

  // True from the moment validation is requested until the backend answers
  // with a fresh `state` (or rejects the request with an `error` frame). The
  // backend's own `validation_started`/succeeded/failed events already stream
  // through `onEvent` into the Activity Log below; this local flag is only
  // what keeps the button disabled and the terminal honest while the check
  // runs.
  const [validationPending, setValidationPending] = useState(false)

  // Which of compile/flash/validation the Terminal below is currently
  // showing — null until the first one runs. Set from the real backend
  // `*_started` events in `onEvent` below, and kept as the last one that ran
  // once everything is idle, so the terminal's result stays on screen until a
  // different operation starts (`currentOp` below folds this together with the
  // three live "is running" flags).
  const [activeOp, setActiveOp] = useState(null)
  // How much room the Terminal dock takes (src/build/terminalDock.js): collapsed
  // to its header, docked at a height the student can drag, or maximized over the
  // editor. Pure presentation — it changes nothing about what is compiled,
  // flashed or validated, and `onEvent` below opens it when output arrives.
  const [dock, dispatchDock] = useReducer(dockReducer, INITIAL_DOCK)

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
      // A resumed session brings its own log, age and last operation (so the
      // terminal keeps showing it); a new one brings none, and restarts at
      // 00:00:00.
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
        { event: message.event, data: message.data, at: elapsedSince(sessionStartRef.current) },
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
      // TERMINAL: which of compile/flash/validation it keeps showing once that
      // operation finishes (see `currentOp` below) — set from the real backend
      // event that starts each one, the same plain-response-to-an-occurrence
      // pattern the compile toast above uses, never derived by watching other
      // state in an effect.
      if (message.event === 'compile_started') {
        setActiveOp('compile')
      } else if (message.event === 'flash_started') {
        setActiveOp('flash')
      } else if (message.event === 'validation_started') {
        setActiveOp('validation')
      }
      // Output changed: open the Terminal if it is collapsed (a failure included).
      // It never maximizes, and a dock that is already open keeps its size.
      if (terminalRevealedBy(message.event)) dispatchDock({ type: 'reveal' })
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

  // Keep what the student just opened in view. A LOCKED/EXPLORE section is
  // only highlighted inside the long stacked file, possibly far off screen:
  // one that is entirely out of view is brought to the top, one already
  // (partly) visible moves only as far as it must, so clicking a section in
  // place never makes the file jump.
  const codeViewRef = useRef(null)
  useEffect(() => {
    if (!selectedSectionId || workspaceActive) return
    const view = codeViewRef.current
    const row = [...(view?.children || [])].find((el) => el.dataset.regionId === selectedSectionId)
    if (!row) return
    const v = view.getBoundingClientRect()
    const r = row.getBoundingClientRect()
    row.scrollIntoView({ block: r.bottom <= v.top || r.top >= v.bottom ? 'start' : 'nearest' })
  }, [selectedSectionId, workspaceActive])

  // The same for the tab strip, which scrolls sideways once enough sections
  // have been opened.
  const tabStripRef = useRef(null)
  useEffect(() => {
    tabStripRef.current?.querySelector('.tab.is-on')?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
  }, [selectedSectionId, workspaceActive, openWorkspaceTabs.length])

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
  // Mirrors exactly the two gates `BuildService.validate_workspace` itself
  // enforces (app/build/service.py) — a successful flash must have happened,
  // AND the workspace still has to be the one that was flashed (flash_ready,
  // the same content-hash check flashing itself uses) — plus the local
  // "nothing else is already in flight" guards `canFlash` already follows.
  // This can never offer an action the backend would refuse; it only avoids
  // a round trip to find that out.
  //
  // A panel that declares no remediation (`state.remediation` is null — Panel
  // 2 today) has no validation to run, so Run Validation is never offered for
  // it, whatever else is true.
  const hasValidation = validationDefined(state?.remediation)
  const canValidate =
    hasValidation &&
    flashStatus === 'succeeded' &&
    flashReady &&
    !validationPending &&
    !busy &&
    !isCompiling
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

  // The compile half of the Terminal below — real `arduino-cli compile` output
  // (`compileOutput.stdout`/`stderr`), never simulated. The backend sends it in
  // every `state` frame (`compile_output`); the compile toast only shows an
  // icon/title, so this is where the output itself is read.
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

  // The flash half of the Terminal — real Arduino CLI/esptool output
  // (`flashOutput.stdout`/`stderr`), never simulated text: banner, device and
  // failure-note copy, then the tool's own output.
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

  // The validation half, built the same way as the flash half above — real
  // backend validator output (`validationOutput.message`/`details`),
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

  // TERMINAL. Compile, flash and validation share one output area: it shows
  // whichever of the three is currently running and, once it finishes, keeps
  // that same operation's result on screen until a different one starts. None of
  // the three text/status builders above changed; this only picks between them.
  const compileRunning = submitting || isCompiling
  const validationRunning = validationPending
  const currentOp = flashing ? 'flash' : compileRunning ? 'compile' : validationRunning ? 'validation' : activeOp
  const terminalText =
    currentOp === 'compile'
      ? compileTerminalText
      : currentOp === 'flash'
        ? flashTerminalText
        : currentOp === 'validation'
          ? validationTerminalText
          : 'Output will appear here once you compile, flash, or validate.'
  const terminalStatus =
    currentOp === 'compile'
      ? compileBannerStatus
      : currentOp === 'flash'
        ? flashBannerStatus
        : currentOp === 'validation'
          ? validationBannerStatus
          : 'not_started'
  // The header chip: which operation, and how it stands. No chip until one has run.
  const terminalChip = currentOp
    ? {
        label: `${TERMINAL_OP_LABEL[currentOp]} · ${BUILD_STATUS_LABEL[terminalStatus] || terminalStatus}`,
        tone: (STATUS_VISUAL[terminalStatus] || STATUS_VISUAL.not_started).tone,
      }
    : null

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

  function compileHint() {
    if (compiling) return 'Compilation in progress'
    if (busy) return 'Wait for the current operation to finish'
    if (validationPending) return 'Wait for the validation check to finish'
    return 'Compile the firmware'
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
    if (!hasValidation) return VALIDATION_UNDEFINED_NOTE
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

  // The Blockly switch opens the workspace of the most recently opened
  // editable section, or else the first editable one. With no editable section
  // there is no canvas to open, so it is disabled rather than faked.
  const blocklyTarget =
    openWorkspaceTabs[openWorkspaceTabs.length - 1] ??
    activeSegments.find((s) => s.policy === 'editable')?.region_id ??
    null

  // A real Blockly canvas is on screen (not still loading, not the legacy
  // text fallback). Only then does the workspace have a toolbox of its own.
  const blocklyCanvasOpen = workspaceActive && !sectionLoading && Boolean(sectionData?.representable)
  const sideOpen = workspaceActive ? sideOpenBlockly : sideOpenCpp
  const setSideOpen = workspaceActive ? setSideOpenBlockly : setSideOpenCpp
  const sidePanelDef = SIDE_PANELS.find((p) => p.id === sidePanel) || SIDE_PANELS[0]

  // A rail button opens its panel; pressing the open panel's own button closes
  // the column again, handing its width back to the editor.
  function pickSidePanel(id) {
    if (sideOpen && id === sidePanel) {
      setSideOpen(false)
    } else {
      setSidePanel(id)
      setSideOpen(true)
    }
  }

  // The header's build actions are icon-only, so these labels are what a
  // screen reader hears (aria-label) and what the tooltip's lead says while an
  // operation is running; `is-working` is the sighted cue.
  const compiling = (busy && chain.intent === CHAIN_INTENT.COMPILE) || (!busy && isCompiling)
  const flashWorking = flashing || (busy && chain.intent === CHAIN_INTENT.FLASH)
  const compileLabel = compiling ? 'Compiling…' : 'Compile'
  const flashLabel = flashing
    ? 'Flashing…'
    : busy && chain.intent === CHAIN_INTENT.FLASH
      ? 'Compiling before flash…'
      : 'Flash'
  const validationLabel = validationPending ? 'Validating…' : 'Run validation'
  const compileDisabled = busy || isCompiling || validationPending || !hasActiveProject

  return (
    <div className="screen screen-fixed">
      <AppHeader
        title="Build Mode"
        onMenu={onMenu}
        actions={
          <div className="build-toolbar">
            {/* The C++ | Blockly switch is TWO DISTINCT WORKSPACES, not two
                tabs of one editor: C++ shows the stacked source, Blockly the
                canvas of the open editable section. It sits first, ahead of
                Compile and Flash, as in the reference; Reset and Quit close
                the bar, set apart from them. */}
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
            {/* COMPILE / FLASH / RUN VALIDATION: compact icon-only controls,
                the only place these three actions live. Each calls the same
                handler as ever (compile / flash / validate). The accessible
                name is the action (or what it is doing right now); the
                tooltip is the reason it is or is not available, and the same
                text is also an .sr-only description, since a disabled button
                can neither take focus nor show a tooltip on a touch screen.
                Run validation has no bug icon to borrow: it reuses the
                shield. */}
            <div className="build-actions" role="group" aria-label="Build actions">
              <button
                type="button"
                className={`icon-btn build-action${compiling ? ' is-working' : ''}`}
                disabled={compileDisabled}
                onClick={compile}
                aria-label={compileLabel}
                aria-busy={compiling || undefined}
                title={compileHint()}
              >
                <Icon name="play" size={18} />
              </button>
              <button
                type="button"
                className={`icon-btn build-action is-primary${flashWorking ? ' is-working' : ''}`}
                disabled={!canFlash}
                onClick={flash}
                aria-label={flashLabel}
                aria-busy={flashWorking || undefined}
                aria-describedby="build-flash-note"
                title={flashHint()}
              >
                <Icon name="bolt" size={18} />
              </button>
              <button
                type="button"
                className={`icon-btn build-action${validationPending ? ' is-working' : ''}`}
                disabled={!canValidate}
                onClick={validate}
                aria-label={validationLabel}
                aria-busy={validationPending || undefined}
                aria-describedby="build-validation-note"
                title={validationHint()}
              >
                <Icon name="shield" size={18} />
              </button>
              <span id="build-flash-note" className="sr-only">
                {flashHint()}
              </span>
              <span id="build-validation-note" className="sr-only">
                {validationHint()}
              </span>
            </div>
            <span className="topbar-sep" aria-hidden="true" />
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
          </div>
        }
      />

      <main className="screen-body build-body">
        <nav className="card build-rail" aria-label="Workspace panels">
          {/* The Blocks toolbox button exists only while a Blockly canvas is
              on screen. It shows or hides Blockly's own toolbox (the canvas's
              left column) and nothing else: the side column below has its own
              rail buttons and is unaffected either way. */}
          {blocklyCanvasOpen ? (
            <button
              type="button"
              className={`icon-btn is-quiet${toolboxVisible ? ' is-on' : ''}`}
              aria-label="Blocks toolbox"
              aria-pressed={toolboxVisible}
              title={toolboxVisible ? 'Hide blocks toolbox' : 'Show blocks toolbox'}
              onClick={() => setToolboxVisible((visible) => !visible)}
            >
              <Icon name="rail-blocks" size={20} />
            </button>
          ) : null}
          {SIDE_PANELS.map((panel) => {
            const open = sideOpen && panel.id === sidePanel
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

        {sideOpen ? (
          <aside className="card build-side" aria-label={sidePanelDef.label}>
            <div className="side-head">
              <h2>{sidePanelDef.label}</h2>
              <button
                type="button"
                className="icon-btn is-quiet is-sm"
                aria-label="Collapse panel"
                title="Collapse panel"
                onClick={() => setSideOpen(false)}
              >
                <Icon name="panel-collapse" size={14} />
              </button>
            </div>

            {sidePanel === 'project' ? (
              <div className="side-body">
                <div className="tree">
                  <p className="tree-row is-folder" title={projectName}>
                    <Icon name="chevron-down" size={10} />
                    <Icon name="folder" size={16} />
                    <span>{projectName}</span>
                  </p>
                  <div className="tree-children">
                    {fileNames.map((name) => {
                      // The stem may be ellipsized in a narrow column; the extension never is.
                      const [stem, extension] = splitExtension(name)
                      return (
                        <button
                          key={name}
                          type="button"
                          className={`tree-row${name === resolvedActiveFile ? ' is-on' : ''}`}
                          title={name}
                          onClick={() => switchFile(name)}
                        >
                          <Icon name={fileIcon(name)} size={16} />
                          <span className="tree-name">
                            <span className="tree-stem">{stem}</span>
                            <span className="tree-ext">{extension}</span>
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>
            ) : null}

            {sidePanel === 'brief' ? (
              <BuildBrief remediation={state?.remediation} firmwareName={project?.firmware_name} />
            ) : null}

            {sidePanel === 'activity' ? <ActivityLog events={events} /> : null}
          </aside>
        ) : null}

        <div className="build-center">
          <section className="card build-editor" aria-label="Firmware workspace">
            {/* Everything but the Terminal dock is the editor proper. A maximized
                Terminal covers it rather than unmounting it, so the open section,
                its draft and Blockly's canvas all stay exactly as they were:
                hidden from sight and from the keyboard (inert), nothing more. */}
            <div className={`editor-main${dock.maximized ? ' is-covered' : ''}`} inert={dock.maximized}>
              {/* File tabs, then one closable tab per EDITABLE section a student
                  has opened. A file tab shows that file's full stacked source;
                  a section tab shows that section's own Blockly workspace. Only
                  the open section is ever mounted, and nothing here changes the
                  single-open-section model (`selectedSectionId`/`sectionData`).
                  Tabs draw a shortened name; the full section id stays in every
                  title and aria-label. Every section is reached by clicking it
                  in the source view below, whatever its policy. */}
              <div className="tab-bar" role="tablist" aria-label="Open files and workspaces">
                {fileNames.map((name) => {
                  const active = !workspaceActive && name === resolvedActiveFile
                  return (
                    <button
                      key={name}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      className={`tab tab-file${active ? ' is-on' : ''}`}
                      title={fileNames.length === 1 ? `Full File — ${name}` : name}
                      onClick={() => {
                        // The file tab IS "Full File": the whole source with no
                        // section open or highlighted, through the same guarded
                        // exit the C++ switch uses.
                        if (name !== resolvedActiveFile) switchFile(name)
                        else if (workspaceActive || selectedSectionId) closeWorkspaceView()
                      }}
                    >
                      <Icon name={fileIcon(name)} size={14} />
                      {name}
                    </button>
                  )
                })}
                <div
                  className="tab-strip"
                  ref={tabStripRef}
                  onWheel={(e) => {
                    if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) e.currentTarget.scrollLeft += e.deltaY
                  }}
                >
                  {openWorkspaceTabs.map((id) => {
                    const active = id === selectedSectionId && workspaceActive
                    return (
                      <span key={id} className={`tab tab-workspace${active ? ' is-on' : ''}`}>
                        <button
                          type="button"
                          role="tab"
                          aria-selected={active}
                          className="tab-label"
                          title={`${id} — ${POLICY_HINT.editable}`}
                          aria-label={`${id}, ${POLICY_HINT.editable}`}
                          onClick={() => openSection(id)}
                        >
                          <Icon name={POLICY_ICON.editable} size={14} />
                          <span className="tab-name">{sectionLabel(id)}</span>
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
                {/* Which kind of section the open canvas is. It lives in the tab
                    bar's free right-hand end rather than floating over the
                    blocks, so it costs the canvas nothing and can never sit on
                    top of one. The section's name is already on its tab. */}
                {blocklyCanvasOpen ? (
                  <span
                    className="tab-context"
                    title={`${selectedSegment.region_id} — ${POLICY_HINT[policyOf(selectedSegment)]}`}
                  >
                    <span className={`policy-tag is-${policyOf(selectedSegment)}`}>
                      <Icon name={POLICY_ICON[policyOf(selectedSegment)]} size={12} />
                      {POLICY_LABEL[policyOf(selectedSegment)]}
                    </span>
                  </span>
                ) : null}
              </div>

              {workspaceActive ? (
                // THE OPEN EDITABLE SECTION'S OWN WORKSPACE. Replaces the
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
                    <div className={`blockly-panel${toolboxVisible ? '' : ' is-toolbox-hidden'}`}>
                      <BlocklyWorkspace
                        key={selectedSectionId}
                        initialWorkspaceState={sectionData.workspace}
                        onWorkspaceChange={onBlocksChange}
                        apiRef={blocklyApiRef}
                        toolboxVisible={toolboxVisible}
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
                <div className="code-view" ref={codeViewRef}>
                  {activeSegments.length === 0 ? (
                    <p className="muted-note">Loading firmware…</p>
                  ) : (
                    numberSegments(activeSegments).map(({ segment, start, lines }) => {
                      const policy = policyOf(segment)
                      const isSelected = segment.region_id === selectedSectionId
                      return (
                        <div
                          key={segment.region_id}
                          data-region-id={segment.region_id}
                          className={`code-section is-${policy}${isSelected ? ' is-selected' : ''}`}
                          role="button"
                          tabIndex={0}
                          aria-label={`Open ${segment.region_id} (${POLICY_LABEL[policy]})`}
                          onClick={() => openSection(segment.region_id)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault()
                              openSection(segment.region_id)
                            }
                          }}
                        >
                          <span className={`policy-tag is-${policy}`}>
                            <Icon name={POLICY_ICON[policy]} size={12} />
                            {POLICY_LABEL[policy]}
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
                  top bar. This is the workspace's submission-state readout,
                  plus why the last COMPILE/FLASH chain stopped short, if it
                  did. */}
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

              {/* The breadcrumb foot belongs to the source view. Over a Blockly
                  canvas the same facts are the pill floated on the canvas, so the
                  canvas keeps this row's height. */}
              {blocklyCanvasOpen ? null : (
                <div className="editor-foot">
                  <span className="crumbs">
                    {projectName}
                    <Icon name="chevron-right" size={10} />
                    <b>{resolvedActiveFile || 'connecting…'}</b>
                    {selectedSegment ? (
                      <>
                        <Icon name="chevron-right" size={10} />
                        <b title={selectedSegment.region_id}>{sectionLabel(selectedSegment.region_id)}</b>
                        <span className={`policy-tag is-${policyOf(selectedSegment)}`}>
                          <Icon name={POLICY_ICON[policyOf(selectedSegment)]} size={12} />
                          {POLICY_LABEL[policyOf(selectedSegment)]}
                        </span>
                      </>
                    ) : null}
                  </span>
                  <span className="editor-foot-meta">
                    <span>C++ (Arduino)</span>
                    {boardName ? <span>{boardName}</span> : null}
                  </span>
                </div>
              )}

            </div>

            {/* TERMINAL — compile, flash and validation output, as a dock along
                the bottom of the editor card (src/components/TerminalDock.jsx).
                It shows whichever of the three is running and keeps that
                operation's result on screen afterwards until a different one
                starts — see `activeOp`/`currentOp` above. Real backend output
                only, never simulated, and nothing here is typed into. */}
            <TerminalDock dock={dock} onDock={dispatchDock} text={terminalText} chip={terminalChip} />
          </section>
        </div>
      </main>

      <StatusBar>
        <HardwareHeaderStatus hardware={hardware} />
      </StatusBar>

      {/* Small top-right compile notification. Floats above the layout
          (position: fixed) rather than taking up workspace; the full compiler
          output is in the Terminal. Not drawn while the Terminal is maximized:
          its header controls sit exactly where this lands, and the output it
          points to is already filling the editor. Its state keeps running, so a
          failure that has not been dismissed shows again on restore. */}
      {compileToastKind && !dock.maximized && (
        <div className={`toast build-toast is-${compileToastKind}`} role="status">
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
