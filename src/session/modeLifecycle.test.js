import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { beforeEach, describe, test } from 'node:test'
import { withParticipant, withResumeSession } from '../api/trainerApi.js'
import { recallSession, rememberSession } from './activeSession.js'
import { createModeLifecycle, endRememberedSession } from './modeLifecycle.js'

function fakeStorage() {
  const map = new Map()
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  }
}

// The slice of the backend this lifecycle depends on, with the same rules as
// backend/app/websocket.py + session_api.py: a connection naming a LIVE session
// re-attaches to it; anything else (no id, an ended or unknown id) gets a new
// one; an explicit end finishes a session, once.
function fakeBackend() {
  const live = new Set()
  const log = []
  let counter = 0
  return {
    live,
    log,
    connect(url) {
      const asked = new URL(url).searchParams.get('session')
      if (asked && live.has(asked)) {
        log.push(['resume', asked])
        return { session_id: asked, resumed: true }
      }
      const id = `s-${++counter}`
      live.add(id)
      log.push(['create', id])
      return { session_id: id, resumed: false }
    },
    async end(mode, id) {
      log.push(['end', mode, id])
      await new Promise((resolve) => setTimeout(resolve, 0)) // a real round trip
      return { ended: live.delete(id) }
    },
  }
}

// What the mode hooks (useHackSocket / useBuildSocket) do on mount: ask to
// resume whatever session this tab remembers, then remember the answer.
function openMode(backend, mode, participantId = '2021-1') {
  const url = withResumeSession(
    withParticipant(`ws://localhost:8000/ws/${mode}`, participantId),
    recallSession(mode),
  )
  const frame = backend.connect(url)
  rememberSession(mode, frame.session_id)
  return frame
}

function harness({ backend, screen: initial }) {
  const nav = []
  // `go` plays App's part: every screen change is reported to the lifecycle
  // (App's screen effect does this), whoever caused it.
  const go = (next) => lifecycle.screenChanged(next)
  const lifecycle = createModeLifecycle({
    endMode: (mode) => endRememberedSession(mode, { end: backend.end }),
    toMenu: () => {
      nav.push(['menu'])
      go('menu')
    },
    restartMode: (mode) => {
      nav.push(['restart', mode, backend.live.size])
      go('prepare')
    },
    screen: initial,
  })
  return { lifecycle, nav, leave: () => go('elsewhere') }
}

beforeEach(() => {
  globalThis.sessionStorage = fakeStorage()
})

for (const mode of ['hack', 'build']) {
  describe(`${mode} mode`, () => {
    test('a page reload resumes the SAME session and ends nothing', () => {
      const backend = fakeBackend()
      const first = openMode(backend, mode)
      assert.equal(first.resumed, false)

      // The reload destroys every component. None of the lifecycle runs; the
      // tab's remembered id is all that is left, and the socket re-asks.
      const second = openMode(backend, mode)
      assert.equal(second.session_id, first.session_id)
      assert.equal(second.resumed, true)
      assert.deepEqual([...backend.live], [first.session_id])
      assert.equal(backend.log.filter((e) => e[0] === 'end').length, 0)
      assert.equal(backend.log.filter((e) => e[0] === 'create').length, 1)
    })

    test('RESET ends the old session, then re-enters the mode, and the next open is a different session', async () => {
      const backend = fakeBackend()
      const old = openMode(backend, mode)
      const { lifecycle, nav } = harness({ backend, screen: mode })

      assert.equal(await lifecycle.reset(mode), true)

      // End was requested for exactly the old id, and had completed by the
      // time the restart was triggered (the third element is how many sessions
      // were still live at that moment: none).
      assert.deepEqual(backend.log.filter((e) => e[0] === 'end'), [['end', mode, old.session_id]])
      assert.deepEqual(nav, [['restart', mode, 0]])
      assert.equal(backend.live.has(old.session_id), false)
      // Nothing is left for the next mount to resume.
      assert.equal(recallSession(mode), null)

      const fresh = openMode(backend, mode)
      assert.notEqual(fresh.session_id, old.session_id)
      assert.equal(fresh.resumed, false)
      assert.deepEqual([...backend.live], [fresh.session_id])
    })

    test('QUIT ends the current session and goes to the menu, starting nothing', async () => {
      const backend = fakeBackend()
      const old = openMode(backend, mode)
      const { lifecycle, nav } = harness({ backend, screen: mode })

      assert.equal(await lifecycle.quit(mode), true)

      assert.deepEqual(backend.log.filter((e) => e[0] === 'end'), [['end', mode, old.session_id]])
      assert.deepEqual(nav, [['menu']])
      assert.equal(backend.live.size, 0)
      assert.equal(recallSession(mode), null)
      assert.equal(backend.log.filter((e) => e[0] === 'create').length, 1) // only the original
    })

    test('a session is never ended twice: a second Reset/Quit finds nothing to end', async () => {
      const backend = fakeBackend()
      openMode(backend, mode)
      const { lifecycle } = harness({ backend, screen: mode })

      await lifecycle.quit(mode)
      await lifecycle.quit(mode)
      assert.equal(backend.log.filter((e) => e[0] === 'end').length, 1)
    })

    test('a double click ends once and navigates once', async () => {
      const backend = fakeBackend()
      openMode(backend, mode)
      const { lifecycle, nav } = harness({ backend, screen: mode })

      const results = await Promise.all([lifecycle.reset(mode), lifecycle.reset(mode)])

      assert.deepEqual(results, [true, false])
      assert.equal(backend.log.filter((e) => e[0] === 'end').length, 1)
      assert.equal(nav.length, 1)
    })

    test('if the student left the mode while the end request was out, nothing is navigated', async () => {
      const backend = fakeBackend()
      openMode(backend, mode)
      const { lifecycle, nav, leave } = harness({ backend, screen: mode })

      const pending = lifecycle.reset(mode)
      leave() // e.g. the menu drawer, which already ends the session itself
      assert.equal(await pending, false)
      assert.deepEqual(nav, [])
    })
  })
}

describe('both modes at once', () => {
  test('resetting or quitting one mode never touches the other mode’s session', async () => {
    const backend = fakeBackend()
    const hack = openMode(backend, 'hack')
    const build = openMode(backend, 'build')
    const { lifecycle } = harness({ backend, screen: 'hack' })

    await lifecycle.reset('hack')

    assert.equal(recallSession('build'), build.session_id)
    assert.equal(backend.live.has(build.session_id), true)
    assert.equal(backend.live.has(hack.session_id), false)
  })
})

describe('endRememberedSession', () => {
  test('forgets the pointer before the request is answered, and ends nothing when there is none', async () => {
    rememberSession('hack', 'h-1')
    const calls = []
    let release
    const answered = new Promise((resolve) => {
      release = resolve
    })
    const pending = endRememberedSession('hack', {
      end: (mode, id) => {
        calls.push([mode, id])
        return answered
      },
    })
    assert.equal(recallSession('hack'), null) // gone while the request is still out
    release({ ended: true })
    await pending

    await endRememberedSession('hack', { end: () => assert.fail('no session was remembered') })
    assert.deepEqual(calls, [['hack', 'h-1']])
  })
})

describe('wiring (source checks)', () => {
  const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
  const app = read('../App.jsx')

  test('App wires Quit and Reset of both modes through the lifecycle', () => {
    for (const mode of ['hack', 'build']) {
      assert.ok(app.includes(`lifecycle.quit('${mode}')`), `${mode} quit`)
      assert.ok(app.includes(`lifecycle.reset('${mode}')`), `${mode} reset`)
    }
  })

  test('Reset re-enters through mode preparation, never straight into the mode', () => {
    assert.match(app, /restartMode:\s*prepareMode/)
    // The only way a mode screen is shown after leaving it is `onReady`.
    assert.equal((app.match(/setScreen\(preparation\.mode\)/g) ?? []).length, 1)
  })

  test('both mode hooks resume only what the tab remembers, and a session frame is what remembers it', () => {
    for (const [file, mode] of [
      ['../hooks/useHackSocket.js', 'hack'],
      ['../hooks/useBuildSocket.js', 'build'],
    ]) {
      const source = read(file)
      assert.ok(source.includes(`recallSession('${mode}')`), `${file} resumes the remembered session`)
      assert.ok(source.includes(`rememberSession('${mode}', message.session_id)`), `${file} remembers it`)
    }
  })

  test('the Reset and Quit buttons are in both mode screens', () => {
    for (const file of ['../screens/HackMode.jsx', '../screens/BuildMode.jsx']) {
      const source = read(file)
      assert.ok(source.includes('onClick={onReset}'), `${file} Reset`)
      assert.ok(source.includes('onClick={onQuit}'), `${file} Quit`)
    }
  })
})
