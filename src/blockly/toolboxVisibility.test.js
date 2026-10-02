import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { TOOLBOX_HIDDEN_CLASS, applyToolboxVisibility } from './toolboxVisibility.js'

// A stand-in for the few Blockly calls the helper makes. `log` records the
// order they happen in, which is the point of the helper.
function fakeWorkspace({ withToolbox = true, withInjectionDiv = true } = {}) {
  const log = []
  const classes = new Set()
  const toolbox = withToolbox
    ? {
        clearSelection: () => log.push('clearSelection'),
        setVisible: (shown) => log.push(`setVisible:${shown}`),
      }
    : null
  const injectionDiv = withInjectionDiv
    ? {
        classList: {
          toggle: (name, force) => {
            if (force) classes.add(name)
            else classes.delete(name)
            log.push(`class:${name}:${force}`)
          },
        },
      }
    : null
  const workspace = { getToolbox: () => toolbox, getInjectionDiv: () => injectionDiv }
  const svgResize = () => log.push('svgResize')
  return { workspace, svgResize, log, classes }
}

test('hiding marks it hidden, closes the flyout, hides the toolbox, then makes Blockly re-measure', () => {
  const { workspace, svgResize, log, classes } = fakeWorkspace()
  applyToolboxVisibility(workspace, false, svgResize)
  assert.deepEqual(log, [`class:${TOOLBOX_HIDDEN_CLASS}:true`, 'clearSelection', 'setVisible:false', 'svgResize'])
  assert.ok(classes.has(TOOLBOX_HIDDEN_CLASS))
})

test('showing again clears the class, shows the toolbox and re-measures, without touching the selection', () => {
  const { workspace, svgResize, log, classes } = fakeWorkspace()
  applyToolboxVisibility(workspace, false, svgResize)
  log.length = 0
  applyToolboxVisibility(workspace, true, svgResize)
  assert.deepEqual(log, [`class:${TOOLBOX_HIDDEN_CLASS}:false`, 'setVisible:true', 'svgResize'])
  assert.ok(!classes.has(TOOLBOX_HIDDEN_CLASS))
})

test('the hidden class is applied before Blockly re-measures (position() reads the toolbox width)', () => {
  const { workspace, svgResize, log } = fakeWorkspace()
  applyToolboxVisibility(workspace, false, svgResize)
  assert.ok(log.indexOf(`class:${TOOLBOX_HIDDEN_CLASS}:true`) < log.indexOf('svgResize'))
})

test('a repeated call re-asserts the whole sequence (no memory to go stale)', () => {
  const { workspace, svgResize, log } = fakeWorkspace()
  applyToolboxVisibility(workspace, false, svgResize)
  log.length = 0
  applyToolboxVisibility(workspace, false, svgResize)
  assert.deepEqual(log, [`class:${TOOLBOX_HIDDEN_CLASS}:true`, 'clearSelection', 'setVisible:false', 'svgResize'])
})

test('a workspace with no toolbox is left alone', () => {
  const { workspace, svgResize, log } = fakeWorkspace({ withToolbox: false })
  applyToolboxVisibility(workspace, false, svgResize)
  assert.deepEqual(log, [])
})

test('a workspace without an injection div still hides through Blockly itself', () => {
  const { workspace, svgResize, log } = fakeWorkspace({ withInjectionDiv: false })
  applyToolboxVisibility(workspace, false, svgResize)
  assert.deepEqual(log, ['clearSelection', 'setVisible:false', 'svgResize'])
})

test('each workspace keeps its own state', () => {
  const a = fakeWorkspace()
  const b = fakeWorkspace()
  applyToolboxVisibility(a.workspace, false, a.svgResize)
  applyToolboxVisibility(b.workspace, true, b.svgResize)
  assert.ok(a.classes.has(TOOLBOX_HIDDEN_CLASS))
  assert.ok(!b.classes.has(TOOLBOX_HIDDEN_CLASS))
  assert.ok(!b.log.includes('setVisible:false'))
})

// The class is only a guarantee if the stylesheet turns it into display:none
// for the real toolbox (and its flyout), and beats Blockly's inline style.
test('build.css hides the toolbox and its flyout under the hidden class, !important', () => {
  const css = readFileSync(new URL('../styles/build.css', import.meta.url), 'utf8')
  const rule = css.match(new RegExp(`\\.${TOOLBOX_HIDDEN_CLASS}[^{]*\\{[^}]*\\}`, 's'))
  assert.ok(rule, `no .${TOOLBOX_HIDDEN_CLASS} rule in build.css`)
  assert.match(rule[0], /\.blocklyToolbox\b/)
  assert.match(rule[0], /\.blocklyToolboxFlyout\b/)
  assert.match(rule[0], /display:\s*none\s*!important/)
})
