import { useEffect, useRef } from 'react'
import * as Blockly from 'blockly/core'
import * as BlocklyEnMsg from 'blockly/msg/en'
import { ARDUINO_TOOLBOX, registerArduinoBlocks } from '../blockly/arduinoBlocks'
import { generateArduinoCode } from '../blockly/arduinoGenerator'

// `blockly/core` (the modular entry point this file deliberately uses
// instead of the "batteries included" `blockly` package — see the module
// docstring in arduinoBlocks.js) ships with an EMPTY `Blockly.Msg` table:
// unlike the full `blockly` bundle, it does not implicitly load any
// language pack. Without this, `Blockly.inject()` throws inside its own
// internals the moment it tries to build an aria-label from an undefined
// message string ("Cannot read properties of undefined (reading
// 'replace')" in `WorkspaceSvg.setInitialAriaContext`) — every Blockly
// workspace on the page crashes, taking the whole React tree down with it
// since nothing here caught it. `setLocale` must run before the first
// `Blockly.inject()` call; doing it at module load (once, for every
// consumer of this file) rather than per-instance is Blockly's own
// documented pattern for the modular/core entry point.
Blockly.setLocale(BlocklyEnMsg)

registerArduinoBlocks()

// Reads resolved (computed) CSS custom property values from the DOM — the
// same trick `HackTerminal.jsx` uses for xterm's theme, for the same
// reason: Blockly's theme option needs concrete color strings, not
// `var(--foo)` expressions.
function readCssVar(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/**
 * A dark theme matching this app's existing palette (see src/index.css) —
 * only the workspace/toolbox/flyout chrome, not the blocks themselves
 * (their default Blockly colours stay usable/familiar, per CLAUDE.md:
 * "prioritize usability... do not introduce a completely unrelated visual
 * theme"). Built once per mount from the live CSS variables rather than
 * hardcoded hex, so a future palette change here needs no Blockly edit.
 */
function buildBlocklyTheme() {
  return Blockly.Theme.defineTheme('build-mode-dark', {
    name: 'build-mode-dark',
    base: Blockly.Themes.Classic,
    componentStyles: {
      workspaceBackgroundColour: readCssVar('--bg', '#0b0f14'),
      toolboxBackgroundColour: readCssVar('--panel', '#151c24'),
      toolboxForegroundColour: readCssVar('--ink', '#e6edf3'),
      flyoutBackgroundColour: readCssVar('--panel', '#151c24'),
      flyoutForegroundColour: readCssVar('--ink', '#e6edf3'),
      flyoutOpacity: 1,
      scrollbarColour: readCssVar('--muted', '#8b98a8'),
      scrollbarOpacity: 0.4,
      insertionMarkerColour: readCssVar('--warn', '#d29922'),
      insertionMarkerOpacity: 0.4,
      markerColour: readCssVar('--ok', '#3fb950'),
      cursorColour: readCssVar('--ok', '#3fb950'),
    },
  })
}

/**
 * Blockly Phase 1 POC host (see CLAUDE.md). Owns the Blockly `WorkspaceSvg`
 * instance, its DOM lifecycle, and resize handling — mirrors
 * `HackTerminal.jsx`'s own imperative-host-plus-ResizeObserver shape for
 * exactly the same reason: a library-owned canvas that must track its
 * container's size (including size changes caused by the left panel
 * collapsing/expanding — see `BuildMode.jsx`) rather than a size this
 * component picks itself.
 *
 * `onCodeChange(code)` fires with the freshly generated C++ every time the
 * block structure actually changes (created/deleted/moved/field edited) —
 * never on pure UI events (selection, scroll, a drag still in progress) —
 * mirroring the plain `<textarea onChange>` the existing full-editor code
 * view already uses, so the caller can feed it into the very same
 * `pendingEdit` state with zero special-casing.
 */
export default function BlocklyWorkspace({ onCodeChange }) {
  const hostRef = useRef(null)
  const workspaceRef = useRef(null)
  const onCodeChangeRef = useRef(onCodeChange)

  useEffect(() => {
    onCodeChangeRef.current = onCodeChange
  })

  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined

    const workspace = Blockly.inject(host, {
      toolbox: ARDUINO_TOOLBOX,
      theme: buildBlocklyTheme(),
      renderer: 'zelos',
      grid: { spacing: 24, length: 2, colour: readCssVar('--line', '#263241'), snap: true },
      zoom: { controls: true, wheel: true, startScale: 0.9, maxScale: 3, minScale: 0.4 },
      move: { scrollbars: true, drag: true, wheel: false },
      trashcan: true,
    })
    workspaceRef.current = workspace

    const handleChange = (event) => {
      if (event.isUiEvent || workspace.isDragging()) return
      onCodeChangeRef.current?.(generateArduinoCode(workspace))
    }
    workspace.addChangeListener(handleChange)

    const resizeObserver = new ResizeObserver(() => {
      Blockly.svgResize(workspace)
    })
    resizeObserver.observe(host)

    return () => {
      resizeObserver.disconnect()
      workspace.removeChangeListener(handleChange)
      workspace.dispose()
      workspaceRef.current = null
    }
  }, [])

  return <div className="blockly-host" ref={hostRef} />
}
