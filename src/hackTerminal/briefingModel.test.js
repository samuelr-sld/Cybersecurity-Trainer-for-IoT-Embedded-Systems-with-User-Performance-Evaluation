import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  BRIEFING_KIND,
  OBJECTIVE_STATE,
  UNSPECIFIED_BRIEFING,
  hintsWithProgress,
  objectiveProgress,
  objectiveStates,
  readBriefing,
  splitInlineCode,
} from './briefingModel.js'

// The `scenario` object of a `session` frame, in the shape
// backend/app/hack_briefing.py sends (kept small here; the backend suite pins
// the real packages' content).
const activityFrame = () => ({
  kind: 'activity',
  panel_id: 'some-panel',
  scenario_id: 'some-scenario',
  title: 'Some Activity',
  objectives: [
    { id: 'extract', label: 'Extract the firmware.', required_events: ['firmware_extracted'] },
    { id: 'find', label: 'Find the broker and topic.', required_events: ['broker_discovered', 'topic_discovered'] },
    { id: 'attack', label: 'Complete the attack.', required_events: ['attack_completed'] },
  ],
  outcomes: ['Explain a thing.'],
  hints: [
    { id: 'h-extract', text: 'Read it with `esptool.py read_flash`.', objective_id: 'extract' },
    { id: 'h-free', text: 'A hint with no objective.', objective_id: null },
  ],
  guide: [{ heading: 'The system', paragraphs: ['One.', 'Two.'] }],
})

const foundationFrame = () => ({
  kind: 'foundation',
  panel_id: 'other-panel',
  scenario_id: 'other-scenario',
  title: 'A Foundation Panel',
  objectives: [],
  outcomes: [],
  hints: [],
  guide: [{ heading: 'Current state', paragraphs: ['No activity is defined.'] }],
})

// --- readBriefing ------------------------------------------------------------------------

test('an activity briefing is read straight from the frame, no panel id involved', () => {
  const briefing = readBriefing(activityFrame())
  assert.equal(briefing.kind, BRIEFING_KIND.ACTIVITY)
  assert.equal(briefing.title, 'Some Activity')
  assert.deepEqual(
    briefing.objectives.map((o) => [o.id, o.label, o.requiredEvents]),
    [
      ['extract', 'Extract the firmware.', ['firmware_extracted']],
      ['find', 'Find the broker and topic.', ['broker_discovered', 'topic_discovered']],
      ['attack', 'Complete the attack.', ['attack_completed']],
    ],
  )
  assert.deepEqual(
    briefing.hints.map((h) => [h.id, h.objectiveId]),
    [
      ['h-extract', 'extract'],
      ['h-free', null],
    ],
  )
  assert.deepEqual(briefing.guide, [{ heading: 'The system', paragraphs: ['One.', 'Two.'] }])
  assert.deepEqual(briefing.outcomes, ['Explain a thing.'])
})

test('a foundation briefing has no objectives and no hints, only its guide', () => {
  const briefing = readBriefing(foundationFrame())
  assert.equal(briefing.kind, BRIEFING_KIND.FOUNDATION)
  assert.deepEqual(briefing.objectives, [])
  assert.deepEqual(briefing.hints, [])
  assert.equal(briefing.guide.length, 1)
})

test('a foundation panel never shows hints or objectives even if a frame carried some', () => {
  const frame = { ...foundationFrame(), ...{ objectives: activityFrame().objectives, hints: activityFrame().hints } }
  const briefing = readBriefing(frame)
  assert.deepEqual(briefing.objectives, [])
  assert.deepEqual(briefing.hints, [])
})

test('anything that is not a briefing degrades to unspecified, never to a guess or a throw', () => {
  for (const raw of [undefined, null, 'text', 7, [], {}, { kind: 'surprise' }]) {
    const briefing = readBriefing(raw)
    assert.equal(briefing.kind, BRIEFING_KIND.UNSPECIFIED, JSON.stringify(raw))
    assert.deepEqual(briefing.objectives, [])
    assert.deepEqual(briefing.hints, [])
  }
  assert.equal(readBriefing(null), UNSPECIFIED_BRIEFING)
})

test('malformed entries are dropped rather than rendered', () => {
  const briefing = readBriefing({
    kind: 'activity',
    objectives: [null, 'x', { id: 'ok', label: 'Good', required_events: ['a', 5, null] }, { id: 'no-label' }],
    hints: [{ id: 'h', text: '' }, { id: 'h2', text: 'Fine' }],
    guide: [{ heading: '', paragraphs: ['x'] }, { heading: 'H', paragraphs: ['p', '  ', 3] }],
  })
  assert.deepEqual(briefing.objectives, [{ id: 'ok', label: 'Good', requiredEvents: ['a'] }])
  assert.deepEqual(briefing.hints, [{ id: 'h2', text: 'Fine', objectiveId: null }])
  assert.deepEqual(briefing.guide, [{ heading: 'H', paragraphs: ['p'] }])
})

// --- objective progress, from recorded events -------------------------------------------

const objectives = () => readBriefing(activityFrame()).objectives

test('with no events recorded every objective is pending — known immediately, not awaited', () => {
  const items = objectiveStates(objectives(), [])
  assert.deepEqual(
    items.map((i) => i.state),
    [OBJECTIVE_STATE.PENDING, OBJECTIVE_STATE.PENDING, OBJECTIVE_STATE.PENDING],
  )
  assert.deepEqual(objectiveProgress(items), { done: 0, total: 3 })
})

test('an objective is done once every required event is recorded, partial when some are', () => {
  let items = objectiveStates(objectives(), ['firmware_extracted', 'broker_discovered'])
  assert.deepEqual(
    items.map((i) => [i.id, i.state]),
    [
      ['extract', 'done'],
      ['find', 'partial'],
      ['attack', 'pending'],
    ],
  )
  // Partial does not count towards the header's "n / N".
  assert.deepEqual(objectiveProgress(items), { done: 1, total: 3 })

  items = objectiveStates(objectives(), ['firmware_extracted', 'broker_discovered', 'topic_discovered', 'attack_completed'])
  assert.deepEqual(objectiveProgress(items), { done: 3, total: 3 })
})

test('events that belong to no objective change nothing', () => {
  const items = objectiveStates(objectives(), ['scan', 'spoof_rejected'])
  assert.ok(items.every((i) => i.state === OBJECTIVE_STATE.PENDING))
})

test('an objective that names no events can never be completed from activity', () => {
  const items = objectiveStates([{ id: 'x', label: 'Unmeasurable', requiredEvents: [] }], ['anything'])
  assert.equal(items[0].state, OBJECTIVE_STATE.PENDING)
})

test('a resumed session replays its events and lands on the same progress', () => {
  const live = objectiveStates(objectives(), ['firmware_extracted'])
  const replayed = objectiveStates(objectives(), new Set(['firmware_extracted']))
  assert.deepEqual(replayed, live)
})

// --- hints ---------------------------------------------------------------------------------

test('a hint is marked done only when the objective it helps with is complete', () => {
  const briefing = readBriefing(activityFrame())
  const before = hintsWithProgress(briefing.hints, objectiveStates(briefing.objectives, []))
  assert.deepEqual(
    before.map((h) => h.done),
    [false, false],
  )
  const after = hintsWithProgress(briefing.hints, objectiveStates(briefing.objectives, ['firmware_extracted']))
  assert.deepEqual(
    after.map((h) => [h.id, h.done]),
    [
      ['h-extract', true],
      ['h-free', false], // tied to nothing: never done
    ],
  )
})

// --- inline code ---------------------------------------------------------------------------

test('backtick pairs become code segments and everything else stays text', () => {
  assert.deepEqual(splitInlineCode('Run `nmap -p <port> <host>` then `help`.'), [
    { code: false, text: 'Run ' },
    { code: true, text: 'nmap -p <port> <host>' },
    { code: false, text: ' then ' },
    { code: true, text: 'help' },
    { code: false, text: '.' },
  ])
})

test('text with no code, a leading or trailing code span, or a stray backtick is handled', () => {
  assert.deepEqual(splitInlineCode('Plain text.'), [{ code: false, text: 'Plain text.' }])
  assert.deepEqual(splitInlineCode('`only`'), [{ code: true, text: 'only' }])
  assert.deepEqual(splitInlineCode('one ` stray'), [{ code: false, text: 'one ` stray' }])
  assert.deepEqual(splitInlineCode(''), [])
})

test('segments are text, not markup: angle brackets and html stay literal', () => {
  const parts = splitInlineCode('Use `<b>bold</b>` carefully')
  assert.deepEqual(parts[1], { code: true, text: '<b>bold</b>' })
})
