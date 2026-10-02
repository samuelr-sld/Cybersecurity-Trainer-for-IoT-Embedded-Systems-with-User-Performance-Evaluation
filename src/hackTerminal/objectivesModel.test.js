import { test } from 'node:test'
import assert from 'node:assert/strict'
import { OBJECTIVE_STATE, deriveObjectives, objectiveProgress } from './objectivesModel.js'

const activity = (discovery = {}, attack = {}) => ({
  scenario_id: 'any-activity',
  target: { device_status: 'online' },
  discovery: {
    firmware_extracted: false,
    firmware_analyzed: false,
    broker_discovered: false,
    topic_discovered: false,
    mqtt_observed: false,
    ...discovery,
  },
  attack: { spoof_attempted: false, spoof_successful: false, ...attack },
})

// Exactly what EnvironmentalSensingScenario.snapshot() sends for Panel 2.
const FOUNDATION = {
  scenario_id: 'environmental-sensing',
  foundation: true,
  objectives: [],
  summary: 'No training activity is defined for this panel yet.',
}

test('before any state frame the objectives are unknown, not guessed', () => {
  assert.deepEqual(deriveObjectives(null), { kind: 'unknown', items: [] })
  assert.deepEqual(deriveObjectives(undefined), { kind: 'unknown', items: [] })
})

test('a foundation panel has no objectives — never an attack list', () => {
  assert.deepEqual(deriveObjectives(FOUNDATION), { kind: 'none', items: [] })
})

test('a snapshot this page does not recognise degrades to none, not an invented list', () => {
  assert.equal(deriveObjectives({ scenario_id: 'future' }).kind, 'none')
  assert.equal(deriveObjectives({ target: {}, discovery: {} }).kind, 'none')
})

test('an activity scenario shows its six steps, all pending at the start', () => {
  const result = deriveObjectives(activity())
  assert.equal(result.kind, 'list')
  assert.deepEqual(
    result.items.map((i) => i.label),
    [
      'Extract firmware',
      'Analyze firmware',
      'Discover broker and topic',
      'Observe MQTT traffic',
      'Publish forged command',
      'Successful attack',
    ],
  )
  assert.ok(result.items.every((i) => i.state === OBJECTIVE_STATE.PENDING))
  assert.deepEqual(objectiveProgress(result.items), { done: 0, total: 6 })
})

test('each step follows its own flag', () => {
  const { items } = deriveObjectives(
    activity({ firmware_extracted: true, firmware_analyzed: true, mqtt_observed: true }, { spoof_attempted: true }),
  )
  const by = Object.fromEntries(items.map((i) => [i.id, i.state]))
  assert.equal(by.extract, 'done')
  assert.equal(by.analyze, 'done')
  assert.equal(by.discover, 'pending')
  assert.equal(by.observe, 'done')
  assert.equal(by.publish, 'done')
  assert.equal(by.attack, 'pending')
})

test('discovering only the broker or only the topic is partial progress', () => {
  assert.equal(deriveObjectives(activity({ broker_discovered: true })).items[2].state, 'partial')
  assert.equal(deriveObjectives(activity({ topic_discovered: true })).items[2].state, 'partial')
  assert.equal(
    deriveObjectives(activity({ broker_discovered: true, topic_discovered: true })).items[2].state,
    'done',
  )
  // Partial does not count towards the header's "n / N".
  const { items } = deriveObjectives(activity({ broker_discovered: true }))
  assert.equal(objectiveProgress(items).done, 0)
})

test('a scenario that declares its own objectives is read verbatim', () => {
  const result = deriveObjectives({
    objectives: [
      { id: 'a', label: 'First', done: true },
      { id: 'b', label: 'Second', state: 'partial' },
      { id: 'c', label: 'Third' },
    ],
  })
  assert.equal(result.kind, 'list')
  assert.deepEqual(
    result.items.map((i) => [i.label, i.state]),
    [
      ['First', 'done'],
      ['Second', 'partial'],
      ['Third', 'pending'],
    ],
  )
  assert.deepEqual(objectiveProgress(result.items), { done: 1, total: 3 })
})

test('a full attack completes every objective', () => {
  const { items } = deriveObjectives(
    activity(
      {
        firmware_extracted: true,
        firmware_analyzed: true,
        broker_discovered: true,
        topic_discovered: true,
        mqtt_observed: true,
      },
      { spoof_attempted: true, spoof_successful: true },
    ),
  )
  assert.deepEqual(objectiveProgress(items), { done: 6, total: 6 })
})
