import assert from 'node:assert/strict'
import { beforeEach, test } from 'node:test'
import {
  forgetActiveMode,
  forgetSession,
  recallActiveMode,
  recallSession,
  rememberActiveMode,
  rememberSession,
} from './activeSession.js'

function fakeStorage() {
  const map = new Map()
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  }
}

beforeEach(() => {
  globalThis.sessionStorage = fakeStorage()
})

test('a remembered session id is recalled per mode and can be forgotten', () => {
  rememberSession('hack', 'h-1')
  rememberSession('build', 'b-1')
  assert.equal(recallSession('hack'), 'h-1')
  assert.equal(recallSession('build'), 'b-1')
  forgetSession('hack')
  assert.equal(recallSession('hack'), null)
  assert.equal(recallSession('build'), 'b-1')
})

test('unknown modes and empty ids are never stored', () => {
  rememberSession('dashboard', 'x')
  rememberSession('hack', '')
  assert.equal(recallSession('dashboard'), null)
  assert.equal(recallSession('hack'), null)
})

test('the active mode round-trips with its student and can be forgotten', () => {
  rememberActiveMode('build', { id: '2021-1', name: 'Ada', extra: 'dropped' })
  assert.deepEqual(recallActiveMode(), { mode: 'build', student: { id: '2021-1', name: 'Ada' } })
  forgetActiveMode()
  assert.equal(recallActiveMode(), null)
})

test('a corrupt or foreign active-mode entry is treated as absent', () => {
  globalThis.sessionStorage.setItem('trainer.activeMode', '{not json')
  assert.equal(recallActiveMode(), null)
  globalThis.sessionStorage.setItem(
    'trainer.activeMode',
    JSON.stringify({ mode: 'menu', student: { id: 'x' } }),
  )
  assert.equal(recallActiveMode(), null)
})

test('missing or throwing storage degrades to "nothing remembered"', () => {
  delete globalThis.sessionStorage
  rememberSession('hack', 'h-1')
  assert.equal(recallSession('hack'), null)
  const blocked = () => {
    throw new Error('blocked')
  }
  globalThis.sessionStorage = { getItem: blocked, setItem: blocked, removeItem: blocked }
  rememberSession('hack', 'h-1')
  assert.equal(recallSession('hack'), null)
  forgetSession('hack')
})
