import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

// Build Mode's chrome (Phase 5 polish) is CSS and layout, which this repo has no
// DOM framework to render; these are source checks that pin the structure the
// browser verification established, so a later edit cannot quietly undo it.
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const build = read('../screens/BuildMode.jsx')
const css = read('../styles/build.css')

// A rule's body, from its selector's opening brace to the next closing brace.
function rule(selector) {
  const start = css.indexOf(`${selector} {`)
  assert.notEqual(start, -1, `${selector} is defined`)
  return css.slice(start, css.indexOf('}', start))
}

test('the header is one toolbar: a single wrapper holds every control group', () => {
  assert.match(build, /<div className="build-toolbar">/)
  assert.match(rule('.build-toolbar'), /gap: var\(--space-3\)/)
  // The mode switch is sized so it lines up with the 44px buttons beside it.
  assert.match(rule('.build-toolbar .seg-group.is-compact'), /padding: 2px/)
})

test('the section tag lives in the tab bar, not over the Blockly canvas', () => {
  assert.doesNotMatch(build, /canvas-pill/)
  assert.doesNotMatch(css, /canvas-pill/)
  assert.match(build, /\{blocklyCanvasOpen \? \(\s*<span\s+className="tab-context"/)
  assert.match(rule('.tab-context'), /margin-left: auto/)
})

test('the compile toast hangs below the tab bar, from tokens, and stays out of a maximized terminal', () => {
  assert.match(build, /toast build-toast is-/)
  assert.match(build, /compileToastKind && !dock\.maximized/)
  assert.match(rule('.toast.build-toast'), /top: calc\(var\(--topbar-height\) \+ 44px \+ 8px\)/)
})

test('the project tree keeps a file\'s extension visible and offers the full name', () => {
  assert.match(build, /splitExtension\(name\)/)
  assert.match(build, /title=\{name\}/)
  assert.match(rule('.tree-ext'), /flex: none/)
  assert.match(rule('.tree-stem'), /text-overflow: ellipsis/)
})

test('sections: quiet in-card captions, only locked code dimmed', () => {
  assert.match(rule('.code-section > .policy-tag'), /border: 0/)
  assert.match(rule('.code-section.is-locked .code-lines'), /opacity: 0\.7/)
  assert.doesNotMatch(rule('.code-section.is-editable'), /opacity/)
})

test('toolbox: the reference\'s 40px rows and an ordinary divider, no bright white', () => {
  const rows = css.slice(css.indexOf('.blockly-host .blocklyToolboxCategory {'))
  assert.match(rows.slice(0, rows.indexOf('}')), /margin-bottom: 0 !important/)
  assert.match(rule('.blockly-host .blocklyTreeSeparator'), /border-bottom-color: var\(--line\) !important/)
  assert.doesNotMatch(css, /#e5e5e5/i)
})

test('the terminal chip cue is skipped for reduced motion and never changes the dock state', () => {
  const dock = read('../components/TerminalDock.jsx')
  assert.match(dock, /prefers-reduced-motion: reduce/)
  assert.match(dock, /chipEl\.animate\(/)
})
