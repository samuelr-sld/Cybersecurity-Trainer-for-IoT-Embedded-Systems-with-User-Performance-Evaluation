import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fullscreenLabel, isFullscreen, isFullscreenSupported, toggleFullscreen } from './fullscreenModel.js'

// A document-shaped fake that records what the model asks of the browser.
function fakeDocument({ enabled = true, active = false, refuse = false } = {}) {
  const calls = []
  const doc = {
    fullscreenEnabled: enabled,
    fullscreenElement: active ? {} : null,
    exitFullscreen() {
      calls.push('exit')
      doc.fullscreenElement = null
      return Promise.resolve()
    },
    documentElement: {
      requestFullscreen(options) {
        calls.push(['request', options])
        if (refuse) return Promise.reject(new TypeError('Fullscreen request denied'))
        doc.fullscreenElement = doc.documentElement
        return Promise.resolve()
      },
    },
  }
  return { doc, calls }
}

test('fullscreen is supported only when the API is present and enabled', () => {
  assert.equal(isFullscreenSupported(fakeDocument().doc), true)
  assert.equal(isFullscreenSupported(fakeDocument({ enabled: false }).doc), false)
  assert.equal(isFullscreenSupported({ fullscreenEnabled: true, documentElement: {} }), false)
  assert.equal(isFullscreenSupported(undefined), false)
})

test('the state is exactly what the browser reports', () => {
  assert.equal(isFullscreen(fakeDocument({ active: false }).doc), false)
  assert.equal(isFullscreen(fakeDocument({ active: true }).doc), true)
  assert.equal(isFullscreen(undefined), false)
})

test('toggling when not fullscreen requests the whole page, hiding browser navigation UI', async () => {
  const { doc, calls } = fakeDocument()
  await toggleFullscreen(doc)
  assert.deepEqual(calls, [['request', { navigationUI: 'hide' }]])
  assert.equal(isFullscreen(doc), true)
})

test('toggling when fullscreen exits, and does not request again', async () => {
  const { doc, calls } = fakeDocument({ active: true })
  await toggleFullscreen(doc)
  assert.deepEqual(calls, ['exit'])
  assert.equal(isFullscreen(doc), false)
})

test('a refused request rejects and leaves the state unchanged', async () => {
  const { doc } = fakeDocument({ refuse: true })
  await assert.rejects(toggleFullscreen(doc), TypeError)
  assert.equal(isFullscreen(doc), false)
})

test('the label names the action the control will perform', () => {
  assert.equal(fullscreenLabel(false), 'Enter fullscreen')
  assert.equal(fullscreenLabel(true), 'Exit fullscreen')
})
