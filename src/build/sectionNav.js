// How Build Mode's section navigation DRAWS a section: its label, its policy
// and what that policy means. Pure presentation vocabulary — no state, no
// socket. The section ids and their policies are the backend's
// (`FileSegment.region_id` / `.policy`, backend/app/build/); nothing here
// renames, adds or reorders one.

// Backend `InteractionPolicy` -> display copy / icon. LOCKED and EXPLORE are
// both read-only; only EDITABLE opens a Blockly canvas.
export const POLICY_LABEL = { locked: 'Locked', explore: 'Explore', editable: 'Editable' }
export const POLICY_ICON = { locked: 'lock', explore: 'eye', editable: 'rail-blocks' }

// One plain phrase per policy, for tooltips and screen readers.
export const POLICY_HINT = {
  editable: 'Editable — opens in Blockly',
  explore: 'Explore — read-only, for inspection',
  locked: 'Locked — read-only',
}

/**
 * The policy a section is drawn with. A missing or unrecognised policy is
 * drawn as LOCKED: unfamiliar firmware must never look editable.
 */
export function policyOf(segment) {
  const policy = segment?.policy
  return Object.hasOwn(POLICY_LABEL, policy) ? policy : 'locked'
}

// The prefixes the backend gives helper and callback functions. They say what
// kind of function a section is, which the policy tag and the Blockly canvas
// already show, so the tabs drop them to save width.
const VISUAL_PREFIX = /^(?:helper|callback)_(?=.)/

/**
 * A section id shortened for display (`helper_setMotorOutputs` ->
 * `setMotorOutputs`). Display only: the real id is still what every handler,
 * tooltip and aria-label uses.
 */
export function sectionLabel(id) {
  return String(id).replace(VISUAL_PREFIX, '')
}
