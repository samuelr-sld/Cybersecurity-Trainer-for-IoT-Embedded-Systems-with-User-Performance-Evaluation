/**
 * The Build Mode EDIT -> COMPILE -> FLASH chain, as one pure reducer.
 *
 * COMPILE IS THE PERSISTENCE BOUNDARY. There is no student-facing "save": a
 * Blockly edit lives only in the browser until COMPILE (or FLASH) submits it
 * through the existing `edit_section_blocks`/`edit_region` frame. The backend
 * then does everything it always did — ownership validation, Blockly -> IR ->
 * C++, and an atomic workspace update that a rejection never reaches — and
 * only once it has ACKNOWLEDGED that edit (`code_edited`) does this chain send
 * the field-less `compile`. A rejected edit ends the chain: no compile, no
 * flash, and the local draft stays exactly as the student left it, so the
 * next attempt serializes the CURRENT workspace rather than anything from the
 * failed one.
 *
 * FLASH NEVER USES A STALE ARTIFACT. FLASH runs the same chain whenever the
 * editor holds an unsubmitted draft or the backend reports the last build is
 * not the current source (`state.flash_ready`, the backend's own content-hash
 * comparison). It sends `flash` only after `compile_succeeded` AND only if no
 * newer draft appeared while the chain ran. The backend re-checks the same
 * hash before uploading anything (`BuildService.flash_workspace`), so this
 * reducer can only ever make a flash LESS likely, never let a stale one
 * through.
 *
 * DRAFT VERSIONS, NOT "PENDING" FLAGS. Every local edit gets a fresh version
 * number. The edit step records which version it submitted; the backend's
 * acknowledgement clears the draft only if that is STILL the current version.
 * An edit made while the request was in flight therefore stays dirty — the
 * race the audit found, where `code_edited` blindly cleared a newer edit and
 * let FLASH believe the canvas matched the build.
 *
 * Dependency-free: `reduceChain(state, action) -> {state, send, clearDraft,
 * notice}` is pure, and `createChainDriver` wraps it with the local draft it
 * submits. `compileChain.test.js` drives both under Node's built-in runner
 * with a fake socket; `BuildMode.jsx` drives the same driver from its click
 * and socket handlers. This module never opens a socket itself.
 */

export const CHAIN_PHASE = Object.freeze({
  IDLE: 'idle',
  // The draft has been submitted; waiting for `code_edited` or an `error`.
  SUBMITTING: 'submitting',
  // `compile` sent; waiting for `compile_succeeded`/`compile_failed`/`error`.
  COMPILING: 'compiling',
  // `flash` sent; waiting for `flash_succeeded`/`flash_failed`/`error`.
  FLASHING: 'flashing',
})

export const CHAIN_INTENT = Object.freeze({
  COMPILE: 'compile',
  FLASH: 'flash',
})

// What the caller must transmit next. `edit` means "submit the current draft
// through the existing edit frame" — the caller reads the draft's content;
// this reducer only tracks its version.
export const CHAIN_SEND = Object.freeze({
  EDIT: 'edit',
  COMPILE: 'compile',
  FLASH: 'flash',
})

// Why a chain stopped without doing everything it was asked to. Display copy
// lives in the caller; these are stable keys.
export const CHAIN_NOTICE = Object.freeze({
  EDIT_REJECTED: 'edit_rejected',
  COMPILE_FAILED_NO_FLASH: 'compile_failed_no_flash',
  EDITED_DURING_CHAIN_NO_FLASH: 'edited_during_chain_no_flash',
  REQUEST_REFUSED: 'request_refused',
})

export const INITIAL_CHAIN = Object.freeze({
  phase: CHAIN_PHASE.IDLE,
  intent: null,
  submittedVersion: null,
})

function result(state, extra = {}) {
  return { state, send: null, clearDraft: false, notice: null, ...extra }
}

const IDLE = INITIAL_CHAIN

/**
 * Advance the chain by one action.
 *
 * Actions:
 *   {type: 'request', intent, draftVersion, flashReady}
 *       COMPILE or FLASH clicked. `draftVersion` is the current unsubmitted
 *       draft's version, or null when the editor matches the backend.
 *       `flashReady` is the backend's `state.flash_ready`.
 *   {type: 'edit_acknowledged', draftVersion}
 *       `code_edited` arrived. `draftVersion` is the draft's version NOW.
 *   {type: 'compile_finished', success, draftVersion}
 *       `compile_succeeded`/`compile_failed` arrived.
 *   {type: 'flash_finished'}
 *       `flash_succeeded`/`flash_failed` arrived.
 *   {type: 'error'}
 *       The backend answered the in-flight request with an `error` frame.
 *   {type: 'reset'}
 *       A new session started.
 */
export function reduceChain(state, action) {
  switch (action.type) {
    case 'reset':
      return result(IDLE)

    case 'request': {
      // One chain at a time; a second click while one runs is ignored rather
      // than queued, so nothing is ever sent against a half-finished chain.
      if (state.phase !== CHAIN_PHASE.IDLE) return result(state)
      const { intent, draftVersion, flashReady } = action
      if (draftVersion !== null && draftVersion !== undefined) {
        return result(
          { phase: CHAIN_PHASE.SUBMITTING, intent, submittedVersion: draftVersion },
          { send: CHAIN_SEND.EDIT },
        )
      }
      if (intent === CHAIN_INTENT.FLASH && flashReady) {
        return result(
          { phase: CHAIN_PHASE.FLASHING, intent, submittedVersion: null },
          { send: CHAIN_SEND.FLASH },
        )
      }
      return result(
        { phase: CHAIN_PHASE.COMPILING, intent, submittedVersion: null },
        { send: CHAIN_SEND.COMPILE },
      )
    }

    case 'edit_acknowledged': {
      if (state.phase !== CHAIN_PHASE.SUBMITTING) return result(state)
      // Only the exact draft that was submitted may be marked as saved. A
      // newer one stays dirty; the compile below builds what the backend
      // accepted, and a FLASH intent will refuse to upload it (see
      // `compile_finished`) because the canvas no longer matches.
      const clearDraft = action.draftVersion === state.submittedVersion
      return result(
        { ...state, phase: CHAIN_PHASE.COMPILING },
        { send: CHAIN_SEND.COMPILE, clearDraft },
      )
    }

    case 'compile_finished': {
      if (state.phase !== CHAIN_PHASE.COMPILING) return result(state)
      if (state.intent !== CHAIN_INTENT.FLASH) return result(IDLE)
      if (!action.success) {
        return result(IDLE, { notice: CHAIN_NOTICE.COMPILE_FAILED_NO_FLASH })
      }
      const draftVersion = action.draftVersion
      if (draftVersion !== null && draftVersion !== undefined) {
        // The student edited while the automatic compile ran: the build that
        // just succeeded is already older than the canvas. Never flash it.
        return result(IDLE, { notice: CHAIN_NOTICE.EDITED_DURING_CHAIN_NO_FLASH })
      }
      return result(
        { ...state, phase: CHAIN_PHASE.FLASHING },
        { send: CHAIN_SEND.FLASH },
      )
    }

    case 'flash_finished':
      if (state.phase !== CHAIN_PHASE.FLASHING) return result(state)
      return result(IDLE)

    case 'error':
      if (state.phase === CHAIN_PHASE.IDLE) return result(state)
      return result(IDLE, {
        notice:
          state.phase === CHAIN_PHASE.SUBMITTING
            ? CHAIN_NOTICE.EDIT_REJECTED
            : CHAIN_NOTICE.REQUEST_REFUSED,
      })

    default:
      return result(state)
  }
}

/** True while any step of a chain is outstanding. */
export function chainBusy(state) {
  return state.phase !== CHAIN_PHASE.IDLE
}

/**
 * Perform one `send` against the Build Mode socket senders
 * (src/hooks/useBuildSocket.js). The edit step submits `draft` exactly as it
 * is at this instant — the CURRENT workspace and preserved list, never a copy
 * captured by an earlier, rejected attempt. Returns whether a frame was sent.
 */
export function transmitVia(senders, send, draft) {
  if (!senders) return false
  if (send === CHAIN_SEND.COMPILE) return Boolean(senders.sendCompile())
  if (send === CHAIN_SEND.FLASH) return Boolean(senders.sendFlash())
  if (send !== CHAIN_SEND.EDIT || !draft) return false
  return Boolean(
    draft.kind === 'text'
      ? senders.sendEditRegion(draft.path, draft.sectionId, draft.source)
      : senders.sendEditSectionBlocks(draft.path, draft.sectionId, draft.workspace, draft.preserved),
  )
}

/**
 * The chain plus the local draft it submits, as one small stateful object —
 * the exact thing `BuildMode.jsx` drives from its click and socket handlers,
 * and the exact thing `compileChain.test.js` drives with a fake socket.
 *
 * `draft` is `{kind: 'blocks', path, sectionId, workspace, preserved}` or
 * `{kind: 'text', path, sectionId, source}`. `draftVersion` is null while
 * the editor matches the backend, and a fresh number after EVERY local edit.
 *
 * `transmit(send, draft)` performs a send and returns whether it left — in
 * the app, `transmitVia` bound to the live socket senders, attached with
 * `attach()` once the socket hook has returned them.
 *
 * Every method returns `{notice, sendFailed}`: `notice` is a CHAIN_NOTICE key
 * (or null) and `sendFailed` means a step could not be transmitted at all, in
 * which case the chain has already ended itself rather than wait forever.
 */
export function createChainDriver(initialTransmit = null) {
  let transmit = initialTransmit
  let chain = INITIAL_CHAIN
  let draft = null
  let draftVersion = null
  let counter = 0

  function step(action) {
    const outcome = reduceChain(chain, action)
    chain = outcome.state
    if (outcome.clearDraft) draftVersion = null
    let sendFailed = false
    if (outcome.send && !(transmit && transmit(outcome.send, draft))) {
      chain = INITIAL_CHAIN
      sendFailed = true
    }
    return { notice: outcome.notice, sendFailed }
  }

  const quiet = () => ({ notice: null, sendFailed: false })

  return {
    // Connect (or replace) how sends leave. Until then every send fails
    // cleanly, ending the chain, exactly as a closed socket would.
    attach(nextTransmit) {
      transmit = nextTransmit
    },
    get chain() {
      return chain
    },
    get draft() {
      return draft
    },
    get draftVersion() {
      return draftVersion
    },
    isBusy() {
      return chainBusy(chain)
    },
    // What the backend holds for the section just opened (or nothing). Not an
    // edit: the editor matches the backend afterwards.
    loadBaseline(content) {
      draft = content
      draftVersion = null
      return quiet()
    },
    // One local edit. Always a NEW version, even if an identical-looking edit
    // was submitted before — an acknowledgement clears only what it answers.
    edit(patch) {
      draft = { ...draft, ...patch }
      counter += 1
      draftVersion = counter
      return quiet()
    },
    request(intent, flashReady) {
      return step({ type: 'request', intent, draftVersion, flashReady })
    },
    editAcknowledged() {
      return step({ type: 'edit_acknowledged', draftVersion })
    },
    compileFinished(success) {
      return step({ type: 'compile_finished', success, draftVersion })
    },
    flashFinished() {
      return step({ type: 'flash_finished' })
    },
    error() {
      return step({ type: 'error' })
    },
    reset() {
      chain = INITIAL_CHAIN
      draft = null
      draftVersion = null
      return quiet()
    },
  }
}
