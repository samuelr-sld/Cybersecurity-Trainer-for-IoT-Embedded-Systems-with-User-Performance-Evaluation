import assert from 'node:assert/strict'
import test from 'node:test'
import { POLICY_HINT, POLICY_ICON, POLICY_LABEL, policyOf, sectionLabel } from './sectionNav.js'

test('sectionLabel drops the helper_ and callback_ prefixes for display', () => {
  assert.equal(sectionLabel('helper_setMotorOutputs'), 'setMotorOutputs')
  assert.equal(sectionLabel('callback_onMessage'), 'onMessage')
  assert.equal(sectionLabel('helper_applyCommand'), 'applyCommand')
})

test('sectionLabel leaves every other id exactly as it is', () => {
  for (const id of ['setup', 'loop', 'global', 'global_2', 'helper', 'callback', 'myhelper_x', 'x_helper_y']) {
    assert.equal(sectionLabel(id), id)
  }
})

test('sectionLabel only strips a leading prefix, once, and never to nothing', () => {
  assert.equal(sectionLabel('helper_helper_x'), 'helper_x')
  assert.equal(sectionLabel('helper_'), 'helper_')
  assert.equal(sectionLabel('callback_'), 'callback_')
})

test('every policy has a label, an icon and a hint', () => {
  for (const policy of ['editable', 'explore', 'locked']) {
    assert.ok(POLICY_LABEL[policy], `label for ${policy}`)
    assert.ok(POLICY_ICON[policy], `icon for ${policy}`)
    assert.ok(POLICY_HINT[policy], `hint for ${policy}`)
  }
})

test('policyOf reads the backend policy and draws anything unknown as locked', () => {
  assert.equal(policyOf({ policy: 'editable' }), 'editable')
  assert.equal(policyOf({ policy: 'explore' }), 'explore')
  assert.equal(policyOf({ policy: 'locked' }), 'locked')
  // Unfamiliar firmware must never look editable.
  assert.equal(policyOf({ policy: 'structural' }), 'locked')
  assert.equal(policyOf({ policy: 'toString' }), 'locked')
  assert.equal(policyOf({}), 'locked')
  assert.equal(policyOf(null), 'locked')
})

test('the hints say which policies are read-only', () => {
  assert.match(POLICY_HINT.editable, /Blockly/)
  assert.match(POLICY_HINT.explore, /read-only/)
  assert.match(POLICY_HINT.locked, /read-only/)
})
