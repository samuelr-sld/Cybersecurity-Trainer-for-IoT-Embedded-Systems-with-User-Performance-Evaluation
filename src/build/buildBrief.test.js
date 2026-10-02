import assert from 'node:assert/strict'
import test from 'node:test'
import { NO_REMEDIATION_TEXT, buildBrief } from './buildBrief.js'

const PANEL_1 = {
  vulnerability: 'An authenticated client can issue commands.',
  remediation_goal: 'Enforce per-command authorization.',
  validation_requirement: 'Reject an unauthorized command.',
}

test('a declared remediation shows its three fields, verbatim and in reading order', () => {
  const brief = buildBrief(PANEL_1)
  assert.equal(brief.declared, true)
  assert.deepEqual(
    brief.sections.map((s) => [s.title, s.text]),
    [
      ['Vulnerability', PANEL_1.vulnerability],
      ['Remediation goal', PANEL_1.remediation_goal],
      ['Validation requirement', PANEL_1.validation_requirement],
    ],
  )
})

test('no remediation (null / undefined / empty) is the neutral state, with nothing invented', () => {
  for (const remediation of [null, undefined, {}, { vulnerability: '', remediation_goal: '   ' }]) {
    const brief = buildBrief(remediation)
    assert.equal(brief.declared, false)
    assert.deepEqual(brief.sections, [])
  }
  assert.match(NO_REMEDIATION_TEXT, /No cybersecurity remediation activity defined for this panel/)
})

test('a field the backend left empty is skipped rather than filled in', () => {
  const brief = buildBrief({ ...PANEL_1, remediation_goal: '' })
  assert.deepEqual(
    brief.sections.map((s) => s.id),
    ['vulnerability', 'validation_requirement'],
  )
})

test('only the three prose fields are ever surfaced', () => {
  const brief = buildBrief({ ...PANEL_1, criterion: { topic: 'secret/topic' }, token: 'abc' })
  assert.equal(JSON.stringify(brief).includes('secret/topic'), false)
  assert.equal(JSON.stringify(brief).includes('abc'), false)
})
