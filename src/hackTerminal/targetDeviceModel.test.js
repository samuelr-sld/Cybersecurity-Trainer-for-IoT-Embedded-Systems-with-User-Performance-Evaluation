import assert from 'node:assert/strict'
import { test } from 'node:test'
import { foundationSummary, hasActivityTarget } from './targetDeviceModel.js'

// Representative snapshots, with the keys the backend scenarios actually send
// (backend/app/scenarios/environmental.py, smart_home.py, environmental_sensing.py).

const legacyTelemetry = {
  scenario_id: 'legacy-environmental-monitoring',
  target: { ip_address: '192.168.10.10', mqtt_port: 1883, mqtt_topic: 't', device_status: 'online' },
  environment: { temperature: 28, humidity: 65, pressure: 1008 },
  discovery: { broker_discovered: false, topic_discovered: false, mqtt_observed: false },
  attack: { spoof_attempted: false, spoof_successful: false, spoof_active: false },
}

const smartHome = {
  scenario_id: 'smart-home-mqtt-control',
  target: { broker_host: 'h', broker_port: 1883, command_topic: 'c', device_status: 'online' },
  motor: { running: false, last_command: null },
  discovery: { broker_discovered: false, topic_discovered: false, mqtt_observed: false },
  attack: { spoof_attempted: false, spoof_successful: false, spoof_active: false },
}

const foundation = {
  scenario_id: 'environmental-sensing',
  foundation: true,
  objectives: [],
  summary: 'The Environmental Monitoring System is a foundation module; no training activity is defined for it yet.',
}

test('both activity scenarios still carry the target skeleton the panel reads', () => {
  assert.equal(hasActivityTarget(legacyTelemetry), true)
  assert.equal(hasActivityTarget(smartHome), true)
})

test('a foundation snapshot has no activity target, so the panel never dereferences one', () => {
  assert.equal(hasActivityTarget(foundation), false)
})

test('a snapshot nobody anticipated degrades to the neutral display, not an exception', () => {
  for (const odd of [null, undefined, {}, { target: {} }, { discovery: {} }, 'x', 0]) {
    assert.equal(hasActivityTarget(odd), false)
    assert.equal(typeof foundationSummary(odd), 'string')
  }
})

test('the foundation summary is the backend text when present', () => {
  assert.equal(foundationSummary(foundation), foundation.summary)
})

test('the foundation summary falls back to the generic fact, and states no objective', () => {
  for (const snapshot of [{}, { summary: '' }, { summary: '   ' }, { summary: 7 }]) {
    const text = foundationSummary(snapshot)
    assert.match(text, /no training activity/i)
    assert.doesNotMatch(text, /mqtt|broker|bme280|spoof|attack/i)
  }
})
