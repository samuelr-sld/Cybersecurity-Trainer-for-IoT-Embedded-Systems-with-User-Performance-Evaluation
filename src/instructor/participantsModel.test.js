import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  LIST_FILTERS,
  filterCounts,
  filterRows,
  formatLastActive,
  initials,
  paginate,
  summarize,
  toCsv,
  toRow,
} from './participantsModel.js'

const NOW = new Date('2026-10-02T12:00:00')

const api = (id, name, hack, build, last) => ({
  participant_id: id,
  full_name: name,
  hack_session_count: hack,
  build_session_count: build,
  last_activity: last,
})

const ROWS = [
  api('2023-132653', 'Samuel Rick Salud', 10, 15, '2026-10-02T03:03:00'),
  api('2023-118204', 'Maria Angela Reyes', 8, 0, '2026-10-01T21:12:00'),
  api('2023-125530', 'Patricia Anne Cruz', 0, 0, null),
  api('2023-131048', 'Daniel Paolo Santos', 0, 4, '2026-09-20T10:00:00'),
].map(toRow)

test('rows keep exactly the fields the API provides', () => {
  assert.deepEqual(ROWS[0], {
    id: '2023-132653',
    name: 'Samuel Rick Salud',
    hackSessions: 10,
    buildSessions: 15,
    lastActivity: '2026-10-02T03:03:00',
  })
})

test('initials use the first and last word, and survive odd names', () => {
  assert.equal(initials('Samuel Rick Salud'), 'SS')
  assert.equal(initials('  madonna '), 'M')
  assert.equal(initials(''), '?')
  assert.equal(initials(undefined), '?')
})

test('the summary counts only what the list proves', () => {
  const s = summarize(ROWS, NOW.getTime())
  assert.equal(s.registered, 4)
  // Samuel (9h ago) and Maria (15h ago) are within 24 hours; Daniel is not.
  assert.equal(s.activeToday, 2)
  assert.equal(s.withHack, 2)
  assert.equal(s.withBuild, 2)
})

test('filters separate students with sessions from those not started', () => {
  assert.equal(filterRows(ROWS, { filter: 'all' }).length, 4)
  assert.deepEqual(filterRows(ROWS, { filter: 'not_started' }).map((r) => r.name), ['Patricia Anne Cruz'])
  assert.equal(filterRows(ROWS, { filter: 'active' }).length, 3)
  assert.deepEqual(filterCounts(ROWS), { all: 4, active: 3, not_started: 1 })
  assert.deepEqual(LIST_FILTERS.map((f) => f.id), ['all', 'active', 'not_started'])
})

test('search matches the name or the number, ignoring case and padding', () => {
  assert.deepEqual(filterRows(ROWS, { query: '  MARIA ' }).map((r) => r.id), ['2023-118204'])
  assert.deepEqual(filterRows(ROWS, { query: '131048' }).map((r) => r.name), ['Daniel Paolo Santos'])
  assert.equal(filterRows(ROWS, { query: 'nobody' }).length, 0)
  assert.equal(filterRows(ROWS, { query: 'maria', filter: 'not_started' }).length, 0)
})

test('pagination clamps and reports a 1-based range', () => {
  const many = Array.from({ length: 24 }, (_, i) => ({ id: String(i) }))
  const second = paginate(many, 2, 10)
  assert.equal(second.pages, 3)
  assert.equal(second.start, 11)
  assert.equal(second.end, 20)
  assert.equal(paginate(many, 99, 10).current, 3)
  assert.equal(paginate(many, 0, 10).current, 1)
  assert.deepEqual(paginate([], 1, 10), { pages: 1, current: 1, slice: [], start: 0, end: 0 })
})

test('last-active reads naturally and never invents a time', () => {
  assert.match(formatLastActive('2026-10-02T03:03:00', NOW), /^Today, /)
  assert.match(formatLastActive('2026-10-01T21:12:00', NOW), /^Yesterday, /)
  assert.doesNotMatch(formatLastActive('2026-09-20T10:00:00', NOW), /^(Today|Yesterday)/)
  assert.equal(formatLastActive(null, NOW), 'Not started')
  assert.equal(formatLastActive('not a date', NOW), 'Not started')
})

test('CSV export quotes awkward values and defuses spreadsheet formulas', () => {
  const rows = [
    toRow(api('1', 'Smith, "Bob"', 1, 2, '2026-10-02T03:03:00')),
    toRow(api('2', '=HYPERLINK("http://x")', 0, 0, null)),
  ]
  const lines = toCsv(rows).split('\r\n')
  assert.equal(lines[0], 'Student,Student number,Hack sessions,Build sessions,Last active (ISO)')
  // name quoted with doubled quotes, then number 1, 1 Hack, 2 Build, last active.
  assert.equal(lines[1], '"Smith, ""Bob""",1,1,2,2026-10-02T03:03:00')
  // The formula-looking name is neutralised with a leading apostrophe.
  assert.ok(lines[2].startsWith(`"'=HYPERLINK(""http://x"")",2,0,0,`))
})
