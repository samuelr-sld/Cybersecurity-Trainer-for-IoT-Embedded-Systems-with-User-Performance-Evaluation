import Icon from './Icon'
import { NO_REMEDIATION_TEXT, buildBrief } from '../build/buildBrief'

/**
 * The body of Build Mode's "Build Brief" side panel: what the connected
 * panel's remediation must achieve, so a student can read the requirement
 * before running validation instead of finding it out from a failed check.
 *
 * `remediation` is the backend's `state.remediation`, shown verbatim (see
 * src/build/buildBrief.js). A panel with none gets a neutral line saying so —
 * never an invented vulnerability or goal. It takes no workspace space unless
 * its rail button is open.
 */
export default function BuildBrief({ remediation, firmwareName }) {
  const brief = buildBrief(remediation)
  return (
    <div className="side-body brief">
      {firmwareName ? <p className="brief-value">{firmwareName}</p> : null}
      {brief.declared ? (
        brief.sections.map((section) => (
          <section key={section.id}>
            <h3>{section.title}</h3>
            <p>{section.text}</p>
          </section>
        ))
      ) : (
        <p className="empty-line">
          <Icon name="info" size={14} /> {NO_REMEDIATION_TEXT}
        </p>
      )}
    </div>
  )
}
