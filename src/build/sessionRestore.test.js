import assert from 'node:assert/strict'
import { test } from 'node:test'
import { formatElapsed, restoreFromSessionFrame } from './sessionRestore.js'

test('a new session frame restores nothing and starts the clock now', () => {
  const r = restoreFromSessionFrame({ type: 'session', resumed: false, elapsed_seconds: 0 }, 1_000_000)
  assert.deepEqual(r.events, [])
  assert.equal(r.startMs, 1_000_000)
  assert.equal(r.activeOp, null)
})

test('a resumed session continues from its original start, not from page load', () => {
  const now = 5_000_000
  const r = restoreFromSessionFrame({ resumed: true, elapsed_seconds: 3725, history: [] }, now)
  assert.equal(r.startMs, now - 3725_000)
  // The next event logged after the resume reads 01:02:05 plus time since.
  assert.equal(formatElapsed((now + 5000 - r.startMs) / 1000), '01:02:10')
})

test('history becomes log entries in order with their own elapsed times', () => {
  const r = restoreFromSessionFrame(
    {
      resumed: true,
      elapsed_seconds: 90,
      history: [
        { event: 'build_session_started', data: {}, elapsed_seconds: 0 },
        { event: 'compile_started', data: { project_id: 'p' }, elapsed_seconds: 61 },
      ],
    },
    0,
  )
  assert.deepEqual(
    r.events.map((e) => [e.event, e.at]),
    [
      ['build_session_started', '00:00:00'],
      ['compile_started', '00:01:01'],
    ],
  )
  assert.deepEqual(r.events[1].data, { project_id: 'p' })
  assert.equal(r.activeOp, 'compile')
})

test('malformed frames degrade to a fresh start', () => {
  const r = restoreFromSessionFrame({ history: 'x', elapsed_seconds: 'y' }, 10)
  assert.deepEqual(r.events, [])
  assert.equal(r.startMs, 10)
})
