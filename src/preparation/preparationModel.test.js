import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  OUTCOME,
  PREPARATION_STEPS,
  STEP_STATUS,
  applyPreparationFrame,
  canEnterMode,
  connectionLost,
  initialPreparation,
} from './preparationModel.js'

// These fold the exact frames backend/app/preparation_websocket.py sends
// (see backend/tests/test_mode_preparation.py for the server side).

const stage = (id, status, data = {}, message = id) => ({ type: 'stage', stage: id, status, message, data })

function successfulRun(mode) {
  const frames = []
  for (const step of PREPARATION_STEPS) {
    frames.push(stage(step.id, 'running'))
    frames.push(stage(step.id, 'succeeded', step.id === 'detecting_device' ? { panel_name: 'Panel' } : {}))
  }
  frames.push(stage('ready', 'succeeded'))
  frames.push({ type: 'result', success: true, mode, message: 'ok', failed_stage: null, detail: '', data: {} })
  return frames
}

const fold = (mode, frames) => frames.reduce(applyPreparationFrame, initialPreparation(mode))

test('a fresh preparation cannot enter any mode', () => {
  assert.equal(canEnterMode(initialPreparation('hack')), false)
})

for (const mode of ['hack', 'build']) {
  test(`${mode}: every step confirmed plus a successful result enters the mode`, () => {
    const state = fold(mode, successfulRun(mode))
    assert.equal(state.outcome, OUTCOME.READY)
    assert.equal(canEnterMode(state), true)
    assert.equal(state.facts.panel_name, 'Panel')
  })
}

test('the mode stays closed until the result arrives, even with every step done', () => {
  const frames = successfulRun('hack').slice(0, -1)
  const state = fold('hack', frames)
  assert.equal(state.outcome, OUTCOME.PREPARING)
  assert.equal(canEnterMode(state), false)
})

test('a failed flash blocks entry and names the failed step', () => {
  const frames = [
    stage('detecting_device', 'running'),
    stage('detecting_device', 'succeeded'),
    stage('resolving_firmware', 'running'),
    stage('resolving_firmware', 'succeeded'),
    stage('compiling', 'running'),
    stage('compiling', 'succeeded'),
    stage('flashing', 'running'),
    stage('flashing', 'failed', { detail: 'A fatal error occurred' }, 'firmware flash failed'),
    { type: 'result', success: false, mode: 'hack', message: 'firmware flash failed', failed_stage: 'flashing', detail: 'A fatal error occurred', data: {} },
  ]
  const state = fold('hack', frames)
  assert.equal(state.outcome, OUTCOME.FAILED)
  assert.equal(canEnterMode(state), false)
  assert.equal(state.steps.flashing, STEP_STATUS.FAILED)
  assert.equal(state.steps.verifying, STEP_STATUS.PENDING)
  assert.equal(state.message, 'firmware flash failed')
  assert.equal(state.detail, 'A fatal error occurred')
})

test('a success claim without every step confirmed is refused', () => {
  const state = fold('build', [
    stage('detecting_device', 'succeeded'),
    { type: 'result', success: true, mode: 'build', message: 'ok', data: {} },
  ])
  assert.equal(state.outcome, OUTCOME.FAILED)
  assert.equal(canEnterMode(state), false)
})

test('a result for a different mode never opens this one', () => {
  const frames = successfulRun('build')
  const state = fold('hack', frames)
  assert.equal(canEnterMode(state), false)
})

test('losing the socket mid-preparation fails it; after the result it changes nothing', () => {
  const midway = fold('hack', [stage('detecting_device', 'running')])
  assert.equal(connectionLost(midway).outcome, OUTCOME.FAILED)

  const done = fold('hack', successfulRun('hack'))
  assert.equal(connectionLost(done), done)
})

test('frames after a failure cannot resurrect the preparation', () => {
  const failed = fold('hack', [stage('detecting_device', 'failed', {}, 'no ESP32 is connected')])
  const later = successfulRun('hack').reduce(applyPreparationFrame, failed)
  assert.equal(later.outcome, OUTCOME.FAILED)
  assert.equal(canEnterMode(later), false)
})

test('retry starts from a completely fresh state', () => {
  const retried = initialPreparation('hack')
  assert.ok(Object.values(retried.steps).every((status) => status === STEP_STATUS.PENDING))
  assert.equal(retried.outcome, OUTCOME.PREPARING)
})
