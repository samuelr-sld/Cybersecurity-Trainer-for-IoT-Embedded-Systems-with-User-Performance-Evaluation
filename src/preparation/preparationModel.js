// Mode Session Preparation — the frontend's view of backend/app/mode_preparation.py.
//
// A pure reducer over `/ws/prepare` frames, kept free of React and of the
// socket so `npm test` can drive it with plain objects. The one rule it
// exists to enforce: a mode may be entered ONLY when the backend reported
// success AND every step was individually reported as succeeded. No step is
// ever marked done by the frontend itself — each checkmark is a `stage`
// frame the backend sent after the real operation finished.

export const STEP_STATUS = {
  PENDING: 'pending',
  RUNNING: 'running',
  SUCCEEDED: 'succeeded',
  FAILED: 'failed',
}

export const OUTCOME = {
  PREPARING: 'preparing',
  READY: 'ready',
  FAILED: 'failed',
}

// Execution order, identical to PREPARATION_STEPS in the backend.
export const PREPARATION_STEPS = [
  {
    id: 'detecting_device',
    running: 'Detecting device…',
    done: 'Panel detected',
    failed: 'Panel detection failed',
    pending: 'Detect panel',
  },
  {
    id: 'resolving_firmware',
    running: 'Resolving firmware…',
    done: 'Firmware resolved',
    failed: 'Firmware resolution failed',
    pending: 'Resolve firmware',
  },
  {
    id: 'compiling',
    running: 'Compiling vulnerable firmware…',
    done: 'Vulnerable firmware compiled',
    failed: 'Firmware compilation failed',
    pending: 'Compile vulnerable firmware',
  },
  {
    id: 'flashing',
    running: 'Flashing vulnerable firmware…',
    done: 'Vulnerable firmware flashed',
    failed: 'Firmware flash failed',
    pending: 'Flash vulnerable firmware',
  },
  {
    id: 'verifying',
    running: 'Verifying device…',
    done: 'Device verified',
    failed: 'Device verification failed',
    pending: 'Verify device',
  },
  {
    id: 'loading_scenario',
    running: 'Loading scenario…',
    done: 'Scenario loaded',
    failed: 'Scenario loading failed',
    pending: 'Load scenario',
  },
]

const STEP_IDS = new Set(PREPARATION_STEPS.map((step) => step.id))

export function initialPreparation(mode) {
  return {
    mode,
    steps: Object.fromEntries(PREPARATION_STEPS.map((step) => [step.id, STEP_STATUS.PENDING])),
    outcome: OUTCOME.PREPARING,
    message: '',
    detail: '',
    facts: {},
  }
}

function fail(state, message, detail = '') {
  return { ...state, outcome: OUTCOME.FAILED, message, detail }
}

function allStepsSucceeded(state) {
  return PREPARATION_STEPS.every((step) => state.steps[step.id] === STEP_STATUS.SUCCEEDED)
}

/** Fold one server frame into the preparation state. Never throws. */
export function applyPreparationFrame(state, frame) {
  if (!frame || state.outcome !== OUTCOME.PREPARING) return state

  if (frame.type === 'stage') {
    if (frame.stage === 'ready') return state // the `result` frame decides
    if (!STEP_IDS.has(frame.stage)) return state
    const { detail, ...facts } = frame.data || {}
    const next = {
      ...state,
      steps: { ...state.steps, [frame.stage]: frame.status },
      facts: frame.status === STEP_STATUS.SUCCEEDED ? { ...state.facts, ...facts } : state.facts,
    }
    if (frame.status === STEP_STATUS.FAILED) return fail(next, frame.message, detail || '')
    return next
  }

  if (frame.type === 'result') {
    if (frame.success && frame.mode === state.mode && allStepsSucceeded(state)) {
      return { ...state, outcome: OUTCOME.READY, facts: { ...state.facts, ...(frame.data || {}) } }
    }
    if (frame.success) {
      // A success claim without every step confirmed is not a success.
      return fail(state, 'The backend reported success without confirming every step.')
    }
    return fail(state, frame.message || 'Preparation failed.', frame.detail || '')
  }

  if (frame.type === 'error') return fail(state, frame.message || 'Preparation was refused.')
  return state
}

/** The socket closed or errored before a result arrived. */
export function connectionLost(state) {
  if (state.outcome !== OUTCOME.PREPARING) return state
  return fail(state, 'Lost connection to the trainer backend during preparation.')
}

export function canEnterMode(state) {
  return state.outcome === OUTCOME.READY && allStepsSucceeded(state)
}

export function stepLabel(step, status) {
  if (status === STEP_STATUS.SUCCEEDED) return step.done
  if (status === STEP_STATUS.RUNNING) return step.running
  if (status === STEP_STATUS.FAILED) return step.failed
  return step.pending
}
