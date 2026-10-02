// The Terminal dock's state model: pure rules, no React, no DOM.
//
// The dock is a PRESENTATION layer over output Build Mode already holds
// (compile / flash / validation output from the Build WebSocket's `state`
// frames). It runs nothing and accepts no input, so its whole state is how much
// room it takes:
//
//   collapsed  open: false                    only the header remains
//   docked     open: true,  maximized: false  the bottom `height` pixels of the editor card
//   maximized  open: true,  maximized: true   covers the whole editor card
//
// `height` is the DOCKED height (header + body) and is never touched by
// maximizing, so RESTORE is only "stop being maximized" and an expand after a
// collapse comes back at the size the student left it. The invariant is
// `maximized` implies `open`; every transition below keeps it.

export const DOCK_MODE = Object.freeze({
  COLLAPSED: 'collapsed',
  DOCKED: 'docked',
  MAXIMIZED: 'maximized',
})

export const DOCK_LIMITS = Object.freeze({
  // Compact by default: the grip row, the 36px header and about seven lines.
  defaultHeight: 184,
  // Never smaller than the grip, the header and three readable lines. Dragging
  // stops here rather than collapsing — collapsing is the header's own control.
  minHeight: 120,
  // What the editor card must keep for itself: its tab bar, a stage a student
  // can still work in, and the breadcrumb row. This is what bounds the dock's
  // maximum height.
  minEditorHeight: 300,
  // Used only while the card has not been measured yet.
  fallbackMaxHeight: 480,
  // One arrow-key press on the resize handle.
  keyStep: 24,
})

export const INITIAL_DOCK = Object.freeze({
  open: false,
  maximized: false,
  height: DOCK_LIMITS.defaultHeight,
})

/** Which of the three states a dock value is in. */
export function dockMode(dock) {
  if (!dock.open) return DOCK_MODE.COLLAPSED
  return dock.maximized ? DOCK_MODE.MAXIMIZED : DOCK_MODE.DOCKED
}

/**
 * The tallest the DOCKED dock may be inside an editor card `containerHeight`
 * pixels tall: everything but the part the editor keeps. Never below the
 * minimum, so a card too short to honour both gives the editor the shortfall
 * instead of an unusable terminal.
 */
export function maxDockHeight(containerHeight) {
  if (!Number.isFinite(containerHeight) || containerHeight <= 0) return DOCK_LIMITS.fallbackMaxHeight
  return Math.max(DOCK_LIMITS.minHeight, containerHeight - DOCK_LIMITS.minEditorHeight)
}

/** `height` forced into [minHeight, maxDockHeight(containerHeight)]. */
export function clampDockHeight(height, containerHeight) {
  const wanted = Number.isFinite(height) ? Math.round(height) : DOCK_LIMITS.defaultHeight
  return Math.min(maxDockHeight(containerHeight), Math.max(DOCK_LIMITS.minHeight, wanted))
}

/**
 * The height to draw. The stored height is what the student chose; a card that
 * later got shorter (a smaller window) clamps what is DRAWN without rewriting
 * the choice, so the dock grows back when the room does.
 */
export function effectiveDockHeight(dock, containerHeight) {
  return clampDockHeight(dock.height, containerHeight)
}

/** The height a drag that started at (`startY`, `startHeight`) gives at `y`. */
export function dragHeightFor({ startHeight, startY, y, containerHeight }) {
  // The handle is the dock's TOP edge, so moving the pointer up grows the dock.
  return clampDockHeight(startHeight + (startY - y), containerHeight)
}

/**
 * The height an arrow/Home/End key on the resize handle asks for, or null for
 * a key the handle does not use. ArrowUp grows the dock (its top edge moves up).
 */
export function keyboardResizeHeight(height, key, containerHeight) {
  switch (key) {
    case 'ArrowUp':
      return clampDockHeight(height + DOCK_LIMITS.keyStep, containerHeight)
    case 'ArrowDown':
      return clampDockHeight(height - DOCK_LIMITS.keyStep, containerHeight)
    case 'Home':
      return DOCK_LIMITS.minHeight
    case 'End':
      return maxDockHeight(containerHeight)
    default:
      return null
  }
}

/**
 * The reducer behind the dock. A transition that changes nothing returns the
 * SAME object, so React skips the render.
 *
 *   toggle / collapse / expand   open <-> closed; closing also ends maximized
 *   maximize                     only from open (a collapsed dock has no maximize control)
 *   restore / toggleMaximize     leave / enter maximized, height untouched
 *   resize {height, containerHeight}
 *                                only while docked: a collapsed or maximized dock
 *                                has no edge to drag, so a stray resize is ignored
 *   reveal                       new output arrived: open if collapsed, otherwise
 *                                leave the student's size and maximized state alone
 */
export function dockReducer(dock, action) {
  switch (action.type) {
    case 'toggle':
      return dock.open ? dockReducer(dock, { type: 'collapse' }) : dockReducer(dock, { type: 'expand' })
    case 'collapse':
      return dock.open ? { ...dock, open: false, maximized: false } : dock
    case 'expand':
      return dock.open ? dock : { ...dock, open: true, maximized: false }
    case 'maximize':
      return dock.open && !dock.maximized ? { ...dock, maximized: true } : dock
    case 'restore':
      return dock.maximized ? { ...dock, maximized: false } : dock
    case 'toggleMaximize':
      return dockReducer(dock, { type: dock.maximized ? 'restore' : 'maximize' })
    case 'resize': {
      if (!dock.open || dock.maximized) return dock
      const height = clampDockHeight(action.height, action.containerHeight)
      return height === dock.height ? dock : { ...dock, height }
    }
    case 'reveal':
      return dock.open ? dock : { ...dock, open: true, maximized: false }
    default:
      return dock
  }
}

// The backend events (BuildEventType, backend/app/build/models.py) that mean
// compile / flash / validation output has changed: each operation starting
// (its progress text) and finishing, either way. Never `code_edited`,
// `hardware_status` polls or a refused request (an `error` frame): none of those
// put anything in the terminal.
const OUTPUT_EVENTS = new Set([
  'compile_started',
  'compile_succeeded',
  'compile_failed',
  'flash_started',
  'flash_succeeded',
  'flash_failed',
  'validation_started',
  'validation_succeeded',
  'validation_failed',
])

/** Whether a Build WebSocket `event` frame's event name should reveal the terminal. */
export function terminalRevealedBy(eventName) {
  return OUTPUT_EVENTS.has(eventName)
}
