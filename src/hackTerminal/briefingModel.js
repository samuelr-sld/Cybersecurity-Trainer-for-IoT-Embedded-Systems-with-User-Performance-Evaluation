// What Hack Mode knows about the scenario it was opened for, read ONLY from the
// backend's `session` frame (`scenario`, protocol v7; backend/app/hack_briefing.py).
//
// The page holds no objective wording, no hint, no guide text and no panel id:
// the attached panel's package declares all of it and the backend sends it the
// instant the connection opens, so nothing here waits for a first event and
// nothing branches on which panel it is. A new panel is a new package, not a
// change to this file.
//
//   * 'activity'    - the package declares measurable objectives (Panel 1).
//   * 'foundation'  - a package that declares none (Panel 2): the page says so.
//   * 'unspecified' - no package named this session's scenario, or the frame
//                     carried nothing usable. There is no scenario information
//                     to show and none is guessed.
//
// Progress is never a claim a hint or a flag makes. Each objective names the
// recorded EVENTS that complete it (the same rule Attack Completion Rate counts
// by), and its state is read from the events the backend has recorded.

export const BRIEFING_KIND = {
  ACTIVITY: 'activity',
  FOUNDATION: 'foundation',
  UNSPECIFIED: 'unspecified',
}

export const OBJECTIVE_STATE = {
  DONE: 'done',
  PARTIAL: 'partial',
  PENDING: 'pending',
}

const KNOWN_KINDS = new Set(Object.values(BRIEFING_KIND))

const asText = (value) => (typeof value === 'string' ? value : '')
const asArray = (value) => (Array.isArray(value) ? value : [])

/** The briefing a screen shows when the backend named no scenario. */
export const UNSPECIFIED_BRIEFING = Object.freeze({
  kind: BRIEFING_KIND.UNSPECIFIED,
  title: '',
  objectives: [],
  outcomes: [],
  hints: [],
  guide: [],
})

/**
 * Reads the `scenario` object of a `session` frame into the shape the screen
 * uses. Total: anything that is not a briefing (absent, null, an older backend
 * that does not send one, a malformed value) is the unspecified briefing, so a
 * frame this page does not understand degrades to "nothing to show" and never
 * to a thrown error or an invented list.
 */
export function readBriefing(raw) {
  if (!raw || typeof raw !== 'object') return UNSPECIFIED_BRIEFING
  const kind = KNOWN_KINDS.has(raw.kind) ? raw.kind : BRIEFING_KIND.UNSPECIFIED

  const objectives = asArray(raw.objectives)
    .filter((item) => item && typeof item === 'object' && asText(item.id) && asText(item.label))
    .map((item) => ({
      id: item.id,
      label: item.label,
      requiredEvents: asArray(item.required_events).filter((event) => typeof event === 'string'),
    }))

  const hints = asArray(raw.hints)
    .filter((item) => item && typeof item === 'object' && asText(item.id) && asText(item.text))
    .map((item) => ({
      id: item.id,
      text: item.text,
      objectiveId: asText(item.objective_id) || null,
    }))

  const guide = asArray(raw.guide)
    .filter((item) => item && typeof item === 'object' && asText(item.heading))
    .map((item) => ({
      heading: item.heading,
      paragraphs: asArray(item.paragraphs).filter((p) => typeof p === 'string' && p.trim()),
    }))

  return {
    kind,
    title: asText(raw.title),
    // Only an activity has objectives and hints to give. A foundation panel
    // defines no activity, so whatever else a frame carried, none are shown:
    // the page never suggests a task for a panel that has none.
    objectives: kind === BRIEFING_KIND.ACTIVITY ? objectives : [],
    outcomes: asArray(raw.outcomes).filter((text) => typeof text === 'string' && text.trim()),
    hints: kind === BRIEFING_KIND.ACTIVITY ? hints : [],
    guide,
  }
}

// --- progress, from recorded events --------------------------------------------------

/**
 * Each objective's done / partial / pending state from the event types the
 * backend has recorded so far: every required event reached -> done, some ->
 * partial, none -> pending. An objective that names no events can never be
 * completed from recorded activity, so it stays pending rather than being
 * guessed done.
 *
 * @param {Array<{id: string, label: string, requiredEvents: string[]}>} objectives
 * @param {Iterable<string>} eventTypes
 */
export function objectiveStates(objectives, eventTypes) {
  const reached = new Set(eventTypes)
  return objectives.map((objective) => {
    const required = objective.requiredEvents
    const hit = required.filter((event) => reached.has(event)).length
    let state = OBJECTIVE_STATE.PENDING
    if (required.length > 0 && hit === required.length) state = OBJECTIVE_STATE.DONE
    else if (hit > 0) state = OBJECTIVE_STATE.PARTIAL
    return { id: objective.id, label: objective.label, state }
  })
}

/** Completed / total, counting only fully done objectives. */
export function objectiveProgress(items) {
  return {
    done: items.filter((item) => item.state === OBJECTIVE_STATE.DONE).length,
    total: items.length,
  }
}

/**
 * The hints, each marked done when the objective it helps with is complete.
 * A hint tied to no objective is never done. Display only: marking a hint done
 * changes nothing the backend records.
 */
export function hintsWithProgress(hints, items) {
  const done = new Set(items.filter((item) => item.state === OBJECTIVE_STATE.DONE).map((i) => i.id))
  return hints.map((hint) => ({ ...hint, done: hint.objectiveId !== null && done.has(hint.objectiveId) }))
}

// --- text ----------------------------------------------------------------------------------

/**
 * Splits hint/guide text into plain and code segments on `backtick` pairs, so a
 * command a student might type is shown as code. Pure text handling: the
 * segments are rendered as text, never evaluated, and nothing offers to run one
 * (the toolbox that did is gone). An unmatched backtick stays literal.
 *
 * @returns {Array<{code: boolean, text: string}>}
 */
export function splitInlineCode(text) {
  const parts = []
  const pattern = /`([^`]+)`/g
  let last = 0
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) parts.push({ code: false, text: text.slice(last, match.index) })
    parts.push({ code: true, text: match[1] })
    last = match.index + match[0].length
  }
  if (last < text.length) parts.push({ code: false, text: text.slice(last) })
  return parts
}
