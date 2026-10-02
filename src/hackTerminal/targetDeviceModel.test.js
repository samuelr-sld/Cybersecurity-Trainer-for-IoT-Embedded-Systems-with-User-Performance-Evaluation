import assert from 'node:assert/strict'
import { test } from 'node:test'
import { TARGET_KIND, describeTarget } from './targetDeviceModel.js'

// Snapshots in the shape the backend scenarios send. Only `readout` matters to
// this page; the rest of a snapshot is deliberately not read.
const readoutRow = (id, label, value, revealed) => ({ id, label, value: revealed ? value : null, revealed })

const undiscovered = {
  scenario_id: 'any-activity',
  target: { broker_host: 'must-not-be-read' },
  motor: { running: true },
  readout: [
    readoutRow('status', 'Status', 'ONLINE', true),
    readoutRow('broker', 'Broker', 'h:1', false),
    readoutRow('thing', 'Thing', 'x', false),
  ],
}

const foundation = {
  scenario_id: 'a-foundation',
  foundation: true,
  objectives: [],
  summary: 'This is a foundation module; no training activity is defined for it yet.',
}

test('before the session frame there is no snapshot: pending, not "none"', () => {
  for (const snapshot of [null, undefined, 'x', 3]) {
    assert.equal(describeTarget(snapshot).kind, TARGET_KIND.PENDING)
  }
})

test('a scenario that states a readout is rendered row by row, with undiscovered facts withheld', () => {
  const result = describeTarget(undiscovered)
  assert.equal(result.kind, TARGET_KIND.ROWS)
  assert.deepEqual(
    result.rows.map((r) => [r.id, r.label, r.revealed, r.value]),
    [
      ['status', 'Status', true, 'ONLINE'],
      ['broker', 'Broker', false, ''],
      ['thing', 'Thing', false, ''],
    ],
  )
})

test('the page reads nothing but the readout: no motor, broker or telemetry sniffing', () => {
  // `target` and `motor` are present and would have drawn a motor panel before.
  const result = describeTarget(undiscovered)
  assert.ok(result.rows.every((r) => !JSON.stringify(r).includes('must-not-be-read')))
})

test('a revealed row without a value is treated as withheld, never shown as "null"', () => {
  const mixed = describeTarget({
    readout: [{ id: 'a', label: 'A', revealed: true, value: null }, readoutRow('b', 'B', 'ok', true)],
  })
  assert.deepEqual(
    mixed.rows.map((r) => [r.id, r.revealed]),
    [
      ['a', false],
      ['b', true],
    ],
  )
})

test('a foundation scenario shows its own summary and no rows', () => {
  const result = describeTarget(foundation)
  assert.equal(result.kind, TARGET_KIND.NONE)
  assert.deepEqual(result.rows, [])
  assert.equal(result.summary, foundation.summary)
})

test('a snapshot nobody anticipated degrades to the neutral display instead of throwing', () => {
  for (const snapshot of [{}, { scenario_id: 'future' }, { readout: 'nope' }, { readout: [] }, { readout: [null, 5] }]) {
    const result = describeTarget(snapshot)
    assert.equal(result.kind, TARGET_KIND.NONE, JSON.stringify(snapshot))
    assert.equal(result.summary, 'No training activity is defined for this panel yet.')
  }
})
