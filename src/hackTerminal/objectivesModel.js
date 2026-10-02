// What the Hack Mode OBJECTIVES panel shows, derived only from the scenario's own
// `state` snapshot (backend/app/scenarios/*::snapshot). Nothing here is a
// hardcoded list of "the objectives of the panel" — it is a reading of what the
// attached panel's scenario reported:
//
//   * 'unknown' - no snapshot yet. The backend sends `state` after the first
//                 command that causes an event (and on a session resume), so
//                 before that the page cannot tell an activity from a neutral
//                 panel and must not guess.
//   * 'none'    - the scenario says it has no activity (a foundation panel, e.g.
//                 Environmental Monitoring: `foundation: true, objectives: []`),
//                 or it is a shape this page does not know. NEVER a made-up list.
//   * 'list'    - the scenario's objectives, each done / partial / pending.
//
// A scenario that states its own `objectives` array is read verbatim. The two
// activity scenarios do not; they share a `discovery` / `attack` flag skeleton
// (see targetDeviceModel.js), and the six steps below are those flags in the
// order the learning progression meets them.

import { hasActivityTarget } from './targetDeviceModel.js'

export const OBJECTIVE_STATE = {
  DONE: 'done',
  PARTIAL: 'partial',
  PENDING: 'pending',
}

const STATE_OF = (done) => (done ? OBJECTIVE_STATE.DONE : OBJECTIVE_STATE.PENDING)

// Both flags -> done, one -> partial, neither -> pending.
function both(a, b) {
  if (a && b) return OBJECTIVE_STATE.DONE
  return a || b ? OBJECTIVE_STATE.PARTIAL : OBJECTIVE_STATE.PENDING
}

const SKELETON_OBJECTIVES = [
  { id: 'extract', label: 'Extract firmware', state: ({ discovery }) => STATE_OF(discovery.firmware_extracted) },
  { id: 'analyze', label: 'Analyze firmware', state: ({ discovery }) => STATE_OF(discovery.firmware_analyzed) },
  {
    id: 'discover',
    label: 'Discover broker and topic',
    state: ({ discovery }) => both(discovery.broker_discovered, discovery.topic_discovered),
  },
  { id: 'observe', label: 'Observe MQTT traffic', state: ({ discovery }) => STATE_OF(discovery.mqtt_observed) },
  { id: 'publish', label: 'Publish forged command', state: ({ attack }) => STATE_OF(attack.spoof_attempted) },
  { id: 'attack', label: 'Successful attack', state: ({ attack }) => STATE_OF(attack.spoof_successful) },
]

function readDeclared(objectives) {
  return objectives.map((item, index) => ({
    id: String(item.id ?? index),
    label: String(item.label ?? item.name ?? item.id ?? `Objective ${index + 1}`),
    state:
      item.state === OBJECTIVE_STATE.PARTIAL
        ? OBJECTIVE_STATE.PARTIAL
        : item.done || item.state === OBJECTIVE_STATE.DONE
          ? OBJECTIVE_STATE.DONE
          : OBJECTIVE_STATE.PENDING,
  }))
}

/** @returns {{kind: 'unknown'|'none'|'list', items: Array<{id: string, label: string, state: string}>}} */
export function deriveObjectives(snapshot) {
  if (!snapshot) return { kind: 'unknown', items: [] }
  if (Array.isArray(snapshot.objectives) && snapshot.objectives.length > 0) {
    return { kind: 'list', items: readDeclared(snapshot.objectives) }
  }
  if (snapshot.foundation || !hasActivityTarget(snapshot) || !snapshot.attack) {
    return { kind: 'none', items: [] }
  }
  return {
    kind: 'list',
    items: SKELETON_OBJECTIVES.map(({ id, label, state }) => ({ id, label, state: state(snapshot) })),
  }
}

/** Completed / total, counting only fully done objectives. */
export function objectiveProgress(items) {
  return {
    done: items.filter((item) => item.state === OBJECTIVE_STATE.DONE).length,
    total: items.length,
  }
}
