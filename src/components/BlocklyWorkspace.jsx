import { useEffect, useRef } from 'react'
import * as Blockly from 'blockly/core'
import * as BlocklyEnMsg from 'blockly/msg/en'
import { registerArduinoBlocks } from '../blockly/arduinoBlocks'
import { generateArduinoCode } from '../blockly/arduinoGenerator'
import { POPULATED_TOOLBOX } from '../blockly/catalog/masterCatalog.generated'
import { applyToolboxVisibility } from '../blockly/toolboxVisibility'

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
 * A dark theme matching the design tokens (src/styles/tokens.css) — only the
 * workspace/toolbox/flyout chrome and the block font, not the blocks'
 * own colours (their default Blockly colours stay usable/familiar, per
 * CLAUDE.md: "prioritize usability... do not introduce a completely unrelated
 * visual theme"). Built once per mount from the live CSS variables rather than
 * hardcoded hex, so a future palette change here needs no Blockly edit.
 */
function buildBlocklyTheme() {
  return Blockly.Theme.defineTheme('build-mode-dark', {
    name: 'build-mode-dark',
    base: Blockly.Themes.Classic,
    fontStyle: { family: readCssVar('--font-sans', 'sans-serif'), weight: '600', size: 12 },
    componentStyles: {
      workspaceBackgroundColour: readCssVar('--base', '#141316'),
      toolboxBackgroundColour: readCssVar('--surface', '#1c1c1e'),
      toolboxForegroundColour: readCssVar('--text', '#e5e4e5'),
      flyoutBackgroundColour: readCssVar('--surface', '#1c1c1e'),
      flyoutForegroundColour: readCssVar('--text', '#e5e4e5'),
      flyoutOpacity: 1,
      scrollbarColour: readCssVar('--muted', '#919093'),
      scrollbarOpacity: 0.4,
      insertionMarkerColour: readCssVar('--warning', '#f2d34a'),
      insertionMarkerOpacity: 0.4,
      markerColour: readCssVar('--success', '#b9f56b'),
      cursorColour: readCssVar('--success', '#b9f56b'),
    },
  })
}

/**
 * Section-based Blockly host (Phase B8 correction). Owns the Blockly
 * `WorkspaceSvg` instance, its DOM lifecycle, and resize handling — mirrors
 * `HackTerminal.jsx`'s own imperative-host-plus-ResizeObserver shape for
 * exactly the same reason: a library-owned canvas that must track its
 * container's size (including size changes caused by the left panel
 * collapsing/expanding — see `BuildMode.jsx`) rather than a size this
 * component picks itself.
 *
 * `initialWorkspaceState` is one section's `workspace` field exactly as
 * `section_blockly`/`edit_section_blocks` responses carry it (backend/app/
 * build/blockly_bridge/models.py::BlocklySection.to_workspace_state()) —
 * loaded once, right after `Blockly.inject`, via
 * `Blockly.serialization.workspaces.load`. The CALLER remounts this
 * component (via a `key` on the opened section id — see `BuildMode.jsx`)
 * whenever the student switches sections, rather than this component trying
 * to imperatively swap a live workspace's content — simpler, and it is what
 * keeps one section's edits from ever leaking into another's canvas.
 *
 * `onWorkspaceChange(stateJSON)` fires with the CURRENT full serialized
 * workspace state — the authoritative payload `edit_section_blocks` sends
 * when the student compiles — every time the block structure actually
 * changes (created/deleted/moved/field edited), never on pure UI events
 * (selection, scroll, a drag still in progress) and never for an event that
 * leaves the serialization identical (see `lastSerialized` below). `onCodeChange(code)`, kept for the legacy whole-file flow
 * (`BuildMode.jsx`'s Blink/no-sections fallback), fires alongside it with the
 * client-side generated preview; a section-based caller can ignore it.
 *
 * `apiRef`, optional, is a ref this component fills with `{flush()}` while
 * mounted: `flush()` reports the workspace's CURRENT serialization through
 * `onWorkspaceChange` synchronously if it differs from the last one reported
 * (see the FLUSH comment below).
 *
 * `toolboxVisible` (default true) shows or hides Blockly's own toolbox — a
 * presentation toggle driven by `BuildMode.jsx`'s rail button; it changes no
 * block, category or serialization. Hidden, the toolbox leaves the layout
 * entirely and the canvas takes its space (see src/blockly/toolboxVisibility.js
 * and the `.toolbox-hidden` rule in build.css).
 */
export default function BlocklyWorkspace({
  initialWorkspaceState,
  onWorkspaceChange,
  onCodeChange,
  apiRef,
  toolboxVisible = true,
}) {
  const hostRef = useRef(null)
  const workspaceRef = useRef(null)
  const onCodeChangeRef = useRef(onCodeChange)
  const onWorkspaceChangeRef = useRef(onWorkspaceChange)
  const toolboxVisibleRef = useRef(toolboxVisible)

  useEffect(() => {
    onCodeChangeRef.current = onCodeChange
    onWorkspaceChangeRef.current = onWorkspaceChange
    toolboxVisibleRef.current = toolboxVisible
  })

  // A toggle after mount. (A new canvas, which is a remount, applies the
  // current value itself right after it is injected, below.)
  useEffect(() => {
    const workspace = workspaceRef.current
    if (workspace) applyToolboxVisibility(workspace, toolboxVisible, Blockly.svgResize)
  }, [toolboxVisible])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined

    const workspace = Blockly.inject(host, {
      // Every catalog category that has at least one usable block; the rest
      // of the master catalog is metadata only (see src/blockly/catalog/).
      toolbox: POPULATED_TOOLBOX,
      theme: buildBlocklyTheme(),
      renderer: 'zelos',
      grid: { spacing: 24, length: 2, colour: readCssVar('--line-strong', '#3a3b3e'), snap: true },
      zoom: { controls: true, wheel: true, startScale: 0.9, maxScale: 3, minScale: 0.4 },
      move: { scrollbars: true, drag: true, wheel: false },
      trashcan: true,
    })
    workspaceRef.current = workspace

    if (initialWorkspaceState) {
      // Loaded BEFORE the change listener attaches, so the initial load
      // itself never reports back as a student edit.
      Blockly.serialization.workspaces.load(initialWorkspaceState, workspace)
    }
    // A section opened while the toolbox is hidden stays hidden: applied here,
    // in the same task as the injection, so it is never drawn first. (A new
    // workspace starts with its toolbox shown, so there is nothing to apply
    // for the common case.)
    if (!toolboxVisibleRef.current) applyToolboxVisibility(workspace, false, Blockly.svgResize)

    // Only a change to what the workspace SERIALIZES to is reported. Blockly
    // delivers events asynchronously, so the creation events of the load
    // above can still arrive after this listener attaches; without this
    // comparison, merely opening a section would report an "edit" — and Build
    // Mode, which keeps a student on a section while it holds uncompiled
    // edits, would then refuse to let them leave a section they never
    // touched.
    let lastSerialized = JSON.stringify(Blockly.serialization.workspaces.save(workspace))
    const report = () => {
      const state = Blockly.serialization.workspaces.save(workspace)
      const serialized = JSON.stringify(state)
      if (serialized === lastSerialized) return
      lastSerialized = serialized
      onWorkspaceChangeRef.current?.(state)
      onCodeChangeRef.current?.(generateArduinoCode(workspace))
    }
    const handleChange = (event) => {
      if (event.isUiEvent || workspace.isDragging()) return
      report()
    }
    workspace.addChangeListener(handleChange)
    // FLUSH. Blockly delivers change events asynchronously, so an edit is
    // reported a moment after it happens — normally within a frame, but a
    // throttled or backgrounded tab can defer it much longer. A caller about
    // to act on "the current workspace" (Build Mode's COMPILE/FLASH) calls
    // this first, so what it submits is the canvas as it is right now, never
    // the last change that happened to have been delivered.
    if (apiRef) apiRef.current = { flush: report }

    const resizeObserver = new ResizeObserver(() => {
      Blockly.svgResize(workspace)
    })
    resizeObserver.observe(host)

    return () => {
      if (apiRef && apiRef.current?.flush === report) apiRef.current = null
      resizeObserver.disconnect()
      workspace.removeChangeListener(handleChange)
      workspace.dispose()
      workspaceRef.current = null
    }
    // `initialWorkspaceState` is intentionally read once, at mount: the
    // caller remounts this whole component (via `key`) to load a different
    // section, rather than this effect re-loading state into a live
    // workspace mid-session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <div className="blockly-host" ref={hostRef} />
}
