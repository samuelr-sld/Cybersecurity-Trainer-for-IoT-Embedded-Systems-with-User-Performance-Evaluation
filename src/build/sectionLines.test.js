import { test } from 'node:test'
import assert from 'node:assert/strict'
import { numberSegments } from './sectionLines.js'

test('numbering continues across sections like the rendered file', () => {
  const rows = numberSegments([{ text: 'a\nb\n' }, { text: 'c\n' }, { text: 'd\ne\nf\n' }])
  assert.deepEqual(
    rows.map((r) => [r.start, r.lines]),
    [
      [1, ['a', 'b']],
      [3, ['c']],
      [4, ['d', 'e', 'f']],
    ],
  )
})

test('a blank-line section counts as one line, an empty one as none', () => {
  const rows = numberSegments([{ text: 'x\n' }, { text: '\n' }, { text: '' }, { text: 'y' }])
  assert.deepEqual(
    rows.map((r) => [r.start, r.lines.length]),
    [
      [1, 1],
      [2, 1],
      [3, 0],
      [3, 1],
    ],
  )
})

test('missing text is treated as empty and no segments give no rows', () => {
  assert.deepEqual(numberSegments([{}])[0].lines, [])
  assert.deepEqual(numberSegments([]), [])
})
