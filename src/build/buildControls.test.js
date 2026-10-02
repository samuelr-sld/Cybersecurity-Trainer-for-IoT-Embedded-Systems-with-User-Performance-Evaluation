import assert from 'node:assert/strict'
import { test } from 'node:test'
import { VALIDATION_UNDEFINED_NOTE, validationDefined } from './buildControls.js'

test('a panel that declares remediation defines a validation (Panel 1)', () => {
  assert.equal(
    validationDefined({
      vulnerability: 'v',
      remediation_goal: 'g',
      validation_requirement: 'r',
    }),
    true,
  )
})

test('a panel with no remediation (null/absent) defines no validation (Panel 2)', () => {
  assert.equal(validationDefined(null), false)
  assert.equal(validationDefined(undefined), false)
})

test('the explanation is neutral and invents no activity', () => {
  assert.equal(VALIDATION_UNDEFINED_NOTE, 'Validation is not defined for this panel.')
})
