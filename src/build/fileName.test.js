import assert from 'node:assert/strict'
import { test } from 'node:test'
import { splitExtension } from './fileName.js'

test('splits the stem from the extension, keeping the dot with the extension', () => {
  assert.deepEqual(splitExtension('smart_home_mqtt_control.ino'), ['smart_home_mqtt_control', '.ino'])
  assert.deepEqual(splitExtension('environmental_monitoring.ino'), ['environmental_monitoring', '.ino'])
})

test('only the last dot splits', () => {
  assert.deepEqual(splitExtension('a.b.ino'), ['a.b', '.ino'])
})

test('no extension, a leading dot or a trailing dot leave the name whole', () => {
  assert.deepEqual(splitExtension('Makefile'), ['Makefile', ''])
  assert.deepEqual(splitExtension('.gitignore'), ['.gitignore', ''])
  assert.deepEqual(splitExtension('notes.'), ['notes.', ''])
  assert.deepEqual(splitExtension(''), ['', ''])
})

test('stem and extension always rejoin to the original name', () => {
  for (const name of ['a.ino', 'x.y.z', '.env', 'noext', 'trail.', 'smart_home_mqtt_control.ino']) {
    assert.equal(splitExtension(name).join(''), name)
  }
})
