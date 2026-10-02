import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { FIELD_EMPTY } from './deviceState.js'
import { CHECKING, menuHardwareView } from './menuHardwareModel.js'

const report = (panel, usb) => ({ panel, usb })

test('a registered panel shows its name and its real USB port', () => {
  const view = menuHardwareView(
    report(
      { status: 'identified', name: 'SMART HOME MQTT CONTROL SYSTEM', mac: '20:9b:a9:88:0b:e4' },
      { connected: true, port: 'COM7' },
    ),
  )
  assert.deepEqual(view.panel, { label: 'SMART HOME MQTT CONTROL SYSTEM', tone: 'is-ok' })
  assert.deepEqual(view.usb, { label: 'COM7', tone: 'is-ok' })
})

test('a MAC bound to no panel reads Unregistered, and keeps the port', () => {
  const view = menuHardwareView(
    report({ status: 'unregistered', name: null, mac: 'aa:bb:cc:dd:ee:ff' }, { connected: true, port: 'COM3' }),
  )
  assert.equal(view.panel.label, 'Unregistered')
  assert.equal(view.usb.label, 'COM3')
})

test('a board whose MAC is not read reads Unknown, not Unregistered', () => {
  const view = menuHardwareView(
    report({ status: 'unidentified', name: null, mac: null }, { connected: true, port: '/dev/ttyUSB0' }),
  )
  assert.equal(view.panel.label, 'Unknown')
  assert.equal(view.usb.label, '/dev/ttyUSB0')
})

test('nothing attached reads No device and Disconnected', () => {
  const view = menuHardwareView(
    report({ status: 'not_connected', name: null, mac: null }, { connected: false, port: null }),
  )
  assert.deepEqual(view.panel, { label: 'No device', tone: '' })
  assert.deepEqual(view.usb, { label: 'Disconnected', tone: 'is-bad' })
})

test('the panel is never taken from the USB port', () => {
  // Even a payload that (wrongly) carried a port beside "no device" or an
  // unnamed identification must not make the PANEL field say anything about it.
  for (const status of ['not_connected', 'unidentified', 'unregistered']) {
    const view = menuHardwareView(report({ status, name: null, mac: null }, { connected: true, port: 'COM3' }))
    assert.ok(!view.panel.label.includes('COM3'), status)
  }
  const unnamed = menuHardwareView(report({ status: 'identified', name: null, mac: null }, { connected: true, port: 'COM3' }))
  assert.equal(unnamed.panel.label, 'Unknown')
})

test('before the first answer, and before the backend has looked, it says Checking', () => {
  assert.deepEqual(menuHardwareView(null), {
    panel: { label: CHECKING, tone: '' },
    usb: { label: CHECKING, tone: '' },
  })
  const cold = menuHardwareView(
    report({ status: 'not_checked', name: null, mac: null }, { connected: false, port: null }),
  )
  assert.equal(cold.panel.label, CHECKING)
  assert.equal(cold.usb.label, CHECKING)
})

test('an unreachable backend is a dash, never "No device"', () => {
  const view = menuHardwareView(null, true)
  assert.equal(view.panel.label, FIELD_EMPTY)
  assert.equal(view.usb.label, FIELD_EMPTY)
})

test('the menu reads the report only through the polling hook (no socket, no per-render fetch)', () => {
  const menu = readFileSync(new URL('../screens/MainMenu.jsx', import.meta.url), 'utf8')
  const hook = readFileSync(new URL('../hooks/useHardwareStatus.js', import.meta.url), 'utf8')
  assert.match(menu, /useHardwareStatus\(\)/)
  assert.doesNotMatch(menu, /fetchHardwareStatus|new WebSocket/)
  assert.doesNotMatch(hook, /new WebSocket/)
  // One effect owns the interval and tears it down; a render never fetches.
  assert.match(hook, /clearInterval/)
  assert.match(hook, /controller\.abort\(\)/)
  assert.match(hook, /document\.hidden/)
})
