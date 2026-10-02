import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import {
  DOCK_LIMITS,
  DOCK_MODE,
  INITIAL_DOCK,
  clampDockHeight,
  dockMode,
  dockReducer,
  dragHeightFor,
  effectiveDockHeight,
  keyboardResizeHeight,
  maxDockHeight,
  terminalRevealedBy,
} from './terminalDock.js'

const step = (dock, ...actions) => actions.reduce(dockReducer, dock)

// An editor card 696px tall: the 1280x800 production layout.
const CARD = 696
const docked = (height = DOCK_LIMITS.defaultHeight) => ({ open: true, maximized: false, height })

// --- the three states --------------------------------------------------------

test('a fresh dock is collapsed, with a compact default height ready for when it opens', () => {
  assert.equal(dockMode(INITIAL_DOCK), DOCK_MODE.COLLAPSED)
  assert.equal(INITIAL_DOCK.height, DOCK_LIMITS.defaultHeight)
  assert.ok(DOCK_LIMITS.defaultHeight < CARD / 3, 'the default must not dominate the workspace')
})

test('expand opens it docked; collapse closes it again', () => {
  const open = step(INITIAL_DOCK, { type: 'expand' })
  assert.equal(dockMode(open), DOCK_MODE.DOCKED)
  assert.equal(dockMode(step(open, { type: 'collapse' })), DOCK_MODE.COLLAPSED)
})

test('toggle flips between collapsed and docked', () => {
  const open = step(INITIAL_DOCK, { type: 'toggle' })
  assert.equal(dockMode(open), DOCK_MODE.DOCKED)
  assert.equal(dockMode(step(open, { type: 'toggle' })), DOCK_MODE.COLLAPSED)
})

test('maximize is only available from open', () => {
  assert.equal(dockMode(step(INITIAL_DOCK, { type: 'maximize' })), DOCK_MODE.COLLAPSED)
  assert.equal(dockMode(step(docked(), { type: 'maximize' })), DOCK_MODE.MAXIMIZED)
})

test('restore returns to docked at EXACTLY the height it had before maximizing', () => {
  const before = docked(260)
  const restored = step(before, { type: 'maximize' }, { type: 'restore' })
  assert.deepEqual(restored, before)
  assert.equal(dockMode(restored), DOCK_MODE.DOCKED)
})

test('maximizing never rewrites the docked height', () => {
  assert.equal(step(docked(260), { type: 'maximize' }).height, 260)
})

test('toggleMaximize alternates maximize and restore', () => {
  const max = step(docked(), { type: 'toggleMaximize' })
  assert.equal(dockMode(max), DOCK_MODE.MAXIMIZED)
  assert.equal(dockMode(step(max, { type: 'toggleMaximize' })), DOCK_MODE.DOCKED)
})

test('collapsing a maximized dock collapses it and ends maximized; the height survives', () => {
  const collapsed = step(docked(300), { type: 'maximize' }, { type: 'collapse' })
  assert.equal(dockMode(collapsed), DOCK_MODE.COLLAPSED)
  assert.equal(collapsed.maximized, false)
  const reopened = step(collapsed, { type: 'expand' })
  assert.equal(dockMode(reopened), DOCK_MODE.DOCKED)
  assert.equal(reopened.height, 300)
})

test('maximized always implies open, whatever the sequence', () => {
  const actions = ['toggle', 'collapse', 'expand', 'maximize', 'restore', 'toggleMaximize', 'reveal']
  let dock = INITIAL_DOCK
  for (let i = 0; i < 400; i++) {
    dock = dockReducer(dock, { type: actions[(i * 7 + (i >> 2)) % actions.length] })
    assert.ok(!dock.maximized || dock.open, `maximized without open at step ${i}`)
  }
})

test('a transition that changes nothing returns the same object (React skips the render)', () => {
  assert.equal(dockReducer(INITIAL_DOCK, { type: 'collapse' }), INITIAL_DOCK)
  assert.equal(dockReducer(INITIAL_DOCK, { type: 'restore' }), INITIAL_DOCK)
  assert.equal(dockReducer(INITIAL_DOCK, { type: 'maximize' }), INITIAL_DOCK)
  const open = docked()
  assert.equal(dockReducer(open, { type: 'expand' }), open)
  assert.equal(dockReducer(open, { type: 'reveal' }), open)
  assert.equal(dockReducer(open, { type: 'bogus' }), open)
})

// --- resize bounds -----------------------------------------------------------

test('the maximum leaves the editor its minimum and never drops below the dock minimum', () => {
  assert.equal(maxDockHeight(CARD), CARD - DOCK_LIMITS.minEditorHeight)
  assert.equal(maxDockHeight(1000), 1000 - DOCK_LIMITS.minEditorHeight)
  // A card too short to honour both gives the editor the shortfall.
  assert.equal(maxDockHeight(200), DOCK_LIMITS.minHeight)
})

test('an unmeasured card falls back to a fixed maximum', () => {
  for (const unknown of [0, -5, NaN, undefined, Infinity]) {
    assert.equal(maxDockHeight(unknown), DOCK_LIMITS.fallbackMaxHeight)
  }
})

test('resize clamps to [min, max] and rounds', () => {
  const resize = (height) => dockReducer(docked(), { type: 'resize', height, containerHeight: CARD })
  assert.equal(resize(10).height, DOCK_LIMITS.minHeight)
  assert.equal(resize(-400).height, DOCK_LIMITS.minHeight)
  assert.equal(resize(5000).height, maxDockHeight(CARD))
  assert.equal(resize(250.6).height, 251)
})

test('a drag cannot collapse the dock into unusable space, however far it goes', () => {
  const dragged = dragHeightFor({ startHeight: 184, startY: 600, y: 5000, containerHeight: CARD })
  assert.equal(dragged, DOCK_LIMITS.minHeight)
  assert.ok(dragged >= DOCK_LIMITS.minHeight)
})

test('dragging up grows the dock, down shrinks it, 1:1 inside the bounds', () => {
  const args = { startHeight: 200, startY: 500, containerHeight: CARD }
  assert.equal(dragHeightFor({ ...args, y: 450 }), 250)
  assert.equal(dragHeightFor({ ...args, y: 540 }), 160)
  assert.equal(dragHeightFor({ ...args, y: 500 }), 200)
})

test('a drag upward stops at the maximum so the editor keeps its minimum', () => {
  const dragged = dragHeightFor({ startHeight: 200, startY: 500, y: -2000, containerHeight: CARD })
  assert.equal(dragged, CARD - DOCK_LIMITS.minEditorHeight)
})

test('resize is ignored unless docked', () => {
  const collapsed = { ...INITIAL_DOCK }
  const maximized = step(docked(), { type: 'maximize' })
  const resize = { type: 'resize', height: 300, containerHeight: CARD }
  assert.equal(dockReducer(collapsed, resize), collapsed)
  assert.equal(dockReducer(maximized, resize), maximized)
})

test('resizing to the same height changes nothing', () => {
  const open = docked(250)
  assert.equal(dockReducer(open, { type: 'resize', height: 250, containerHeight: CARD }), open)
})

test('a shorter card clamps what is DRAWN but not what the student chose', () => {
  const chosen = docked(380)
  assert.equal(effectiveDockHeight(chosen, 600), 300)
  assert.equal(effectiveDockHeight(chosen, CARD), 380)
  assert.equal(chosen.height, 380)
})

test('a non-numeric height falls back to the default instead of poisoning the layout', () => {
  assert.equal(clampDockHeight(NaN, CARD), DOCK_LIMITS.defaultHeight)
  assert.equal(clampDockHeight(undefined, CARD), DOCK_LIMITS.defaultHeight)
})

test('keyboard: ArrowUp grows, ArrowDown shrinks, Home/End jump, anything else is not handled', () => {
  assert.equal(keyboardResizeHeight(200, 'ArrowUp', CARD), 200 + DOCK_LIMITS.keyStep)
  assert.equal(keyboardResizeHeight(200, 'ArrowDown', CARD), 200 - DOCK_LIMITS.keyStep)
  assert.equal(keyboardResizeHeight(200, 'Home', CARD), DOCK_LIMITS.minHeight)
  assert.equal(keyboardResizeHeight(200, 'End', CARD), maxDockHeight(CARD))
  assert.equal(keyboardResizeHeight(200, 'Enter', CARD), null)
  assert.equal(keyboardResizeHeight(200, 'a', CARD), null)
})

test('keyboard resizing respects both bounds', () => {
  assert.equal(keyboardResizeHeight(DOCK_LIMITS.minHeight, 'ArrowDown', CARD), DOCK_LIMITS.minHeight)
  assert.equal(keyboardResizeHeight(maxDockHeight(CARD), 'ArrowUp', CARD), maxDockHeight(CARD))
})

// --- automatic opening --------------------------------------------------------

test('compile, flash and validation output each open a collapsed terminal', () => {
  for (const event of [
    'compile_started',
    'compile_succeeded',
    'compile_failed',
    'flash_started',
    'flash_succeeded',
    'flash_failed',
    'validation_started',
    'validation_succeeded',
    'validation_failed',
  ]) {
    assert.equal(terminalRevealedBy(event), true, event)
  }
  assert.equal(dockMode(dockReducer(INITIAL_DOCK, { type: 'reveal' })), DOCK_MODE.DOCKED)
})

test('a failure opens a collapsed terminal and never maximizes it', () => {
  assert.equal(terminalRevealedBy('compile_failed'), true)
  assert.equal(terminalRevealedBy('flash_failed'), true)
  assert.equal(terminalRevealedBy('validation_failed'), true)
  const revealed = dockReducer(INITIAL_DOCK, { type: 'reveal' })
  assert.equal(revealed.open, true)
  assert.equal(revealed.maximized, false)
})

test('revealing keeps the height the student already chose, open or collapsed', () => {
  const chosen = docked(310)
  assert.equal(dockReducer(chosen, { type: 'reveal' }), chosen)
  const collapsed = step(chosen, { type: 'collapse' })
  assert.equal(dockReducer(collapsed, { type: 'reveal' }).height, 310)
})

test('revealing leaves an already-maximized terminal maximized', () => {
  const maximized = step(docked(310), { type: 'maximize' })
  assert.equal(dockReducer(maximized, { type: 'reveal' }), maximized)
})

test('events that put nothing in the terminal do not reveal it', () => {
  for (const event of [
    'build_session_started',
    'workspace_loaded',
    'code_edited',
    'security_region_edited',
    'build_session_ended',
    'hardware_status',
    'build_completed',
    'not_an_event',
    '',
    undefined,
  ]) {
    assert.equal(terminalRevealedBy(event), false, String(event))
  }
})

// --- the dock stays a presentation layer --------------------------------------

test('the dock component is output-only: no xterm, no socket, no input, no shell', () => {
  const source = readFileSync(new URL('../components/TerminalDock.jsx', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /xterm/i)
  assert.doesNotMatch(source, /WebSocket/)
  assert.doesNotMatch(source, /<(input|textarea)\b/i)
  assert.doesNotMatch(source, /contentEditable/i)
  assert.doesNotMatch(source, /child_process|subprocess|eval\(/)
})

test('the model itself touches no browser or runtime API', () => {
  // Comments describe the surrounding system; only the code is checked.
  const code = readFileSync(new URL('./terminalDock.js', import.meta.url), 'utf8')
    .split('\n')
    .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line))
    .join('\n')
  assert.doesNotMatch(code, /\b(window|document|localStorage|WebSocket)\b/)
  assert.doesNotMatch(code, /^import /m)
})

// --- how Build Mode wires it (source checks; there is no DOM test framework) ---

test('wiring: every output event opens the dock through the one reveal action, nothing else does', () => {
  const build = readFileSync(new URL('../screens/BuildMode.jsx', import.meta.url), 'utf8')
  // The reveal is guarded by the model's own event list…
  assert.match(build, /if \(terminalRevealedBy\(message\.event\)\) dispatchDock\(\{ type: 'reveal' \}\)/)
  // …and that is the ONLY place the dock is opened by the screen: no operation
  // handler (compile / flash / validate / startChain) opens or maximizes it, so
  // WHEN an operation is invoked is exactly what it was.
  assert.equal((build.match(/dispatchDock\(/g) ?? []).length, 1, 'the reveal is the only call; the dock gets the dispatcher as onDock')
  assert.doesNotMatch(build, /dispatchDock\(\{ type: '(expand|toggle|maximize)'/)
})

test('wiring: the terminal shows the existing build output fields and invents none', () => {
  const build = readFileSync(new URL('../screens/BuildMode.jsx', import.meta.url), 'utf8')
  for (const field of ['compile_output', 'flash_output', 'validation_output']) {
    assert.ok(build.includes(`state?.${field}`), `${field} is read from the Build state`)
  }
  assert.match(build, /<TerminalDock dock=\{dock\} onDock=\{dispatchDock\} text=\{terminalText\} chip=\{terminalChip\} \/>/)
})

test('wiring: the label is "Terminal" and the old names are gone', () => {
  const build = readFileSync(new URL('../screens/BuildMode.jsx', import.meta.url), 'utf8')
  const dock = readFileSync(new URL('../components/TerminalDock.jsx', import.meta.url), 'utf8')
  assert.match(dock, /<span className="terminal-name">Terminal<\/span>/)
  for (const source of [build, dock]) {
    assert.doesNotMatch(source, /General [Tt]erminal|Build Terminal|Output Console|general-terminal/)
  }
})

test('wiring: a maximized dock hides the editor without unmounting it, and is announced as such', () => {
  const build = readFileSync(new URL('../screens/BuildMode.jsx', import.meta.url), 'utf8')
  // The editor stays in the tree (so its draft, scroll and Blockly canvas survive)…
  assert.match(build, /className=\{`editor-main\$\{dock\.maximized \? ' is-covered' : ''\}`\} inert=\{dock\.maximized\}/)
  // …and the compile toast, which lands on the maximized header's controls, steps aside.
  assert.match(build, /compileToastKind && !dock\.maximized/)
})

test('wiring: the dock has accessible names for every control', () => {
  const dock = readFileSync(new URL('../components/TerminalDock.jsx', import.meta.url), 'utf8')
  for (const label of ['Resize terminal', 'Maximize terminal', 'Restore terminal', 'Collapse terminal', 'Expand terminal', 'Terminal output']) {
    assert.ok(dock.includes(`'${label}'`) || dock.includes(`"${label}"`), label)
  }
  assert.match(dock, /aria-expanded=\{open\}/)
})
