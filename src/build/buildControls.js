// Pure rules for the top bar's Compile / Flash / Run Validation icon buttons.
// The handlers behind them (compile, flash, validate in screens/BuildMode.jsx)
// are unchanged; this only answers the one question the controls cannot ask
// the backend directly: does the connected panel define a validation at all?

// The neutral, accessible reason shown (title + screen-reader description)
// while Run Validation is disabled for a panel with no validation activity.
// It states a fact about the panel and suggests nothing about what a
// validation might check.
export const VALIDATION_UNDEFINED_NOTE = 'Validation is not defined for this panel.'

/**
 * Whether the connected panel defines a validation. A mirror of the backend's
 * own rule (backend/app/build_sessions.py `snapshot`): `state.remediation` is
 * the panel's declared remediation prose, and is null exactly when the panel
 * declares none (Panel 2 today) — so no panel id is consulted here.
 *
 * @param {object|null|undefined} remediation `state.remediation`
 * @returns {boolean}
 */
export function validationDefined(remediation) {
  return Boolean(remediation)
}
