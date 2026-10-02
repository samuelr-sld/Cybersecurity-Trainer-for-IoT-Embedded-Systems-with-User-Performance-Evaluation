import { useEffect, useRef, useState } from 'react'
import Icon from './Icon'
import {
  DOCK_LIMITS,
  DOCK_MODE,
  dockMode,
  dragHeightFor,
  effectiveDockHeight,
  keyboardResizeHeight,
  maxDockHeight,
} from '../build/terminalDock'

// How close to the bottom the output must be scrolled for it to keep following
// new text. Anything further up means the student is reading, so it is left be.
const FOLLOW_THRESHOLD_PX = 8

const OUTPUT_ID = 'build-terminal-output'

/**
 * Build Mode's Terminal: a dock along the bottom of the editor card that shows
 * the compile / flash / validation output Build Mode already holds.
 *
 * It is OUTPUT ONLY — a `<pre>`, not a terminal emulator, with no input, no
 * prompt, no connection of its own and nothing it can run. `text` is whatever
 * Build Mode built from the Build mode backend's `state` frames; `chip`, if
 * given, is `{label, tone}` for the operation it is showing.
 *
 * The state (`dock`) belongs to the caller, because the caller is what knows when
 * new output arrives and has to open it (see src/build/terminalDock.js). A drag
 * is the one exception: while it is in progress the height lives here, so the
 * pointer moving does not re-render the whole screen, and the final height is
 * reported once, on release.
 *
 * Layout. Docked, it is the last flex child of the editor card and the editor
 * above it simply gets less height — which is what makes Blockly's own
 * ResizeObserver re-measure the canvas. Maximized, it is positioned over the
 * whole card instead of resizing the editor to nothing, and an empty spacer of
 * its docked height keeps its place in the flow: the editor underneath keeps
 * exactly the layout, scroll positions and unsaved blocks it had (the caller
 * hides it), so there is nothing to re-measure on the way in or out.
 */
export default function TerminalDock({ dock, onDock, text, chip }) {
  const rootRef = useRef(null)
  const chipRef = useRef(null)
  const outputRef = useRef(null)
  const followRef = useRef(true)
  const dragRef = useRef(null)
  const [containerHeight, setContainerHeight] = useState(0)
  const [dragHeight, setDragHeight] = useState(null)

  const mode = dockMode(dock)
  const open = mode !== DOCK_MODE.COLLAPSED
  const maximized = mode === DOCK_MODE.MAXIMIZED
  const docked = mode === DOCK_MODE.DOCKED
  const height = dragHeight ?? effectiveDockHeight(dock, containerHeight)

  // The editor card is the dock's own parent. Its height bounds how tall the dock
  // may be, and it only changes when the window does (the dock lives inside it).
  // A ResizeObserver reports once as soon as it observes, so there is no initial
  // measure to make by hand.
  useEffect(() => {
    const card = rootRef.current?.parentElement
    if (!card) return undefined
    const observer = new ResizeObserver(() => setContainerHeight(card.clientHeight))
    observer.observe(card)
    return () => observer.disconnect()
  }, [])

  // The status chip is where a result is read, so it gets a one-shot ring whenever
  // its text changes: running -> failed, a new operation, a success. That is the
  // cue that something just changed even while the compile toast is held back
  // (it is not drawn over a maximized terminal) or the student looked away.
  // Skipped for reduced motion; the chip itself is a live status region, so a
  // screen reader hears the new text either way.
  const chipLabel = chip?.label
  useEffect(() => {
    const chipEl = chipRef.current
    if (!chipEl || typeof chipEl.animate !== 'function') return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    chipEl.animate(
      [{ boxShadow: '0 0 0 0 currentColor' }, { boxShadow: '0 0 0 7px transparent' }],
      { duration: 900, easing: 'ease-out' },
    )
  }, [chipLabel])

  // Keep the newest output in view: a new text always scrolls to its end, and
  // after that the output keeps following only while the student has not scrolled
  // up to read. A collapsed dock cannot be scrolled, so it follows again on open.
  const shownTextRef = useRef(text)
  useEffect(() => {
    const output = outputRef.current
    if (!output) return
    if (shownTextRef.current !== text) {
      shownTextRef.current = text
      followRef.current = true
    }
    if (!open) {
      followRef.current = true
      return
    }
    if (followRef.current) output.scrollTop = output.scrollHeight
  }, [text, open, maximized, height])

  function onOutputScroll(event) {
    const output = event.currentTarget
    followRef.current = output.scrollHeight - output.scrollTop - output.clientHeight < FOLLOW_THRESHOLD_PX
  }

  // --- resizing: pointer (mouse, touch, pen) and keyboard --------------------------
  function onGripPointerDown(event) {
    if (event.button) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { startY: event.clientY, startHeight: height, last: height }
    setDragHeight(height)
    event.preventDefault()
  }

  function onGripPointerMove(event) {
    const drag = dragRef.current
    if (!drag) return
    drag.last = dragHeightFor({
      startHeight: drag.startHeight,
      startY: drag.startY,
      y: event.clientY,
      containerHeight,
    })
    setDragHeight(drag.last)
  }

  // Release, cancel and a lost capture all end the drag; whichever comes first
  // commits the height last drawn, so the dock never snaps back.
  function endDrag() {
    const drag = dragRef.current
    if (!drag) return
    dragRef.current = null
    setDragHeight(null)
    onDock({ type: 'resize', height: drag.last, containerHeight })
  }

  function onGripKeyDown(event) {
    const next = keyboardResizeHeight(height, event.key, containerHeight)
    if (next === null) return
    event.preventDefault()
    onDock({ type: 'resize', height: next, containerHeight })
  }

  // Escape is the way out of a maximized terminal from the keyboard.
  function onRootKeyDown(event) {
    if (event.key === 'Escape' && maximized) {
      event.stopPropagation()
      onDock({ type: 'restore' })
    }
  }

  const className = `terminal-dock is-${mode}${dragHeight === null ? '' : ' is-resizing'}`

  return (
    <>
      {maximized ? <div className="terminal-spacer" style={{ height }} aria-hidden="true" /> : null}
      <section
        ref={rootRef}
        className={className}
        style={docked ? { height } : undefined}
        aria-label="Terminal"
        onKeyDown={onRootKeyDown}
      >
        {docked ? (
          <div
            className="terminal-grip"
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize terminal"
            aria-controls={OUTPUT_ID}
            aria-valuemin={DOCK_LIMITS.minHeight}
            aria-valuemax={maxDockHeight(containerHeight)}
            aria-valuenow={height}
            aria-valuetext={`${height} pixels tall`}
            tabIndex={0}
            title="Drag to resize the terminal, or press the up and down arrow keys"
            onPointerDown={onGripPointerDown}
            onPointerMove={onGripPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onLostPointerCapture={endDrag}
            onKeyDown={onGripKeyDown}
          />
        ) : null}

        <div className="terminal-head">
          {/* Clicking the title also opens and closes the dock: a large target for a
              touch screen. The collapse/expand button beside it is the keyboard and
              screen-reader route to the same action, so the title is not a tab stop. */}
          <span
            className="terminal-title"
            onClick={maximized ? undefined : () => onDock({ type: 'toggle' })}
          >
            <span className="terminal-name">Terminal</span>
            {chip ? (
              <span ref={chipRef} className={`chip ${chip.tone}`} role="status">
                {chip.label}
              </span>
            ) : null}
          </span>
          <div className="terminal-actions">
            {open ? (
              <button
                type="button"
                className="icon-btn is-quiet is-sm"
                aria-label={maximized ? 'Restore terminal' : 'Maximize terminal'}
                title={maximized ? 'Restore terminal size' : 'Maximize terminal'}
                onClick={() => onDock({ type: 'toggleMaximize' })}
              >
                <Icon name={maximized ? 'restore' : 'maximize'} size={14} />
              </button>
            ) : null}
            <button
              type="button"
              className="icon-btn is-quiet is-sm"
              aria-label={open ? 'Collapse terminal' : 'Expand terminal'}
              aria-expanded={open}
              aria-controls={OUTPUT_ID}
              title={open ? 'Collapse terminal' : 'Expand terminal'}
              onClick={() => onDock({ type: open ? 'collapse' : 'expand' })}
            >
              <Icon name={open ? 'chevron-down' : 'chevron-up'} size={12} />
            </button>
          </div>
        </div>

        {/* A focusable, labelled log: keyboard users can scroll it. `aria-live="off"`
            because the chip above announces the result; reading every line of a
            compile aloud as it arrives would not help anyone. */}
        <pre
          id={OUTPUT_ID}
          ref={outputRef}
          className="terminal-output"
          role="log"
          aria-live="off"
          aria-label="Terminal output"
          tabIndex={0}
          hidden={!open}
          onScroll={onOutputScroll}
        >
          {text}
        </pre>
      </section>
    </>
  )
}
