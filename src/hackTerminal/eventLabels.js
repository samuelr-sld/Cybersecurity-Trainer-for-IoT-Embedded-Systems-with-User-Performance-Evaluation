// Backend `ScenarioEventType` values (backend/app/scenarios/events.py) -> the
// copy the Activity Log and the Evaluation workflow view show. Deliberately a
// plain lookup, not a switch: an event type this map doesn't know about (the
// closed set changed) falls back to the raw value rather than silently
// dropping the row.
export const HACK_EVENT_LABELS = {
  firmware_extracted: 'Firmware extracted',
  firmware_analyzed: 'Firmware analyzed',
  broker_discovered: 'MQTT broker discovered',
  topic_discovered: 'MQTT topic discovered',
  // Key is the generic `scan` (backend/app/scenarios/events.py) — the
  // `nmap` command's one event, kept free of "MQTT" so a future scenario's
  // own recon can reuse it. The label stays MQTT-specific because that is
  // what this scenario's scan actually found; only the event name is generic.
  scan: 'MQTT service scanned',
  mqtt_observed: 'MQTT telemetry observed',
  spoof_attempted: 'Spoof attempt',
  spoof_rejected: 'Spoof rejected',
  spoof_succeeded: 'Spoof successful',
  target_impacted: 'Target impacted',
  attack_completed: 'Attack completed',
}

export function hackEventLabel(event) {
  return HACK_EVENT_LABELS[event] || event
}
