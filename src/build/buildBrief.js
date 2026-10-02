// What the Build Brief shows. A pure view of the backend's `state.remediation`
// (backend/app/build_sessions.py `snapshot`): the connected panel's own
// `RemediationSpec` prose, or null for a panel that declares no remediation
// activity. Nothing is written, completed or paraphrased here — what a fix
// must achieve is courseware content, not frontend copy.

// Display title per backend field, in reading order.
const BRIEF_FIELDS = [
  { id: 'vulnerability', title: 'Vulnerability' },
  { id: 'remediation_goal', title: 'Remediation goal' },
  { id: 'validation_requirement', title: 'Validation requirement' },
]

// The honest state for a panel with no remediation data (Panel 2 today). It
// does not suggest what a remediation might be.
export const NO_REMEDIATION_TEXT = 'No cybersecurity remediation activity defined for this panel.'

/**
 * @param {object|null|undefined} remediation `state.remediation`
 * @returns {{declared: boolean, sections: {id: string, title: string, text: string}[]}}
 */
export function buildBrief(remediation) {
  const sections = BRIEF_FIELDS.flatMap(({ id, title }) => {
    const text = typeof remediation?.[id] === 'string' ? remediation[id].trim() : ''
    return text ? [{ id, title, text }] : []
  })
  return { declared: sections.length > 0, sections }
}
