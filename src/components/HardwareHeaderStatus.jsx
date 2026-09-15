import { useState } from 'react'
import {
  FIELD_EMPTY,
  connectionLabel,
  panelRepresentations,
  usbRepresentations,
} from '../hardware/deviceState'

/**
 * The uniform hardware header both modes render:
 *
 *   PANEL: 20:9b:a9:88:0b:e4 | USB: COM3 | CONNECTED
 *
 * Placed after the screen's mode name, that reads as the one format Hack
 * Mode and Build Mode share:
 *
 *   HACK MODE  | PANEL: … | USB: … | CONNECTED
 *   BUILD MODE | PANEL: … | USB: … | CONNECTED
 *
 * ONE COMPONENT, SO UNIFORMITY IS STRUCTURAL. Neither screen formats these
 * fields itself and neither can drift: they both hand this the shared
 * `hardware` payload (identical shape on `/ws/build` and `/ws/hack`) and it
 * renders the same three fields the same way. The only difference between
 * the two headers is the mode name, which lives outside this component.
 *
 * DISPLAY-SELECTION STATE ONLY. Clicking PANEL or USB changes which
 * *representation* of the same device is shown and nothing else. The
 * toggles are local `useState` — they send no frame, touch no socket, and
 * cannot compile, flash, open a serial port, reconnect the board, or alter
 * the shared device state. The underlying hardware identity is identical
 * before and after a click; only the label changes.
 */
export default function HardwareHeaderStatus({ hardware }) {
  // Monotonic click counters rather than a bounded index: the list of
  // representations grows when the MAC probe completes and shrinks on
  // unplug, and a stored index could fall out of range as it changes.
  // Taking these modulo the current length at render time is always valid.
  const [panelClicks, setPanelClicks] = useState(0)
  const [usbClicks, setUsbClicks] = useState(0)

  const panels = panelRepresentations(hardware)
  const ports = usbRepresentations(hardware)
  const connected = connectionLabel(hardware) === 'CONNECTED'

  return (
    <span className="hw-status">
      <HardwareField
        label="PANEL"
        options={panels}
        clicks={panelClicks}
        onCycle={() => setPanelClicks((n) => n + 1)}
        hint="Show this panel's other name"
      />
      <span className="hw-sep" aria-hidden="true">
        |
      </span>
      <HardwareField
        label="USB"
        options={ports}
        clicks={usbClicks}
        onCycle={() => setUsbClicks((n) => n + 1)}
        hint="Show this port's other spelling"
      />
      <span className="hw-sep" aria-hidden="true">
        |
      </span>
      <span className={connected ? 'hw-conn is-connected' : 'hw-conn is-disconnected'}>
        {connectionLabel(hardware)}
      </span>
    </span>
  )
}

/**
 * One `LABEL: value` field, clickable when there is a value to show.
 *
 * With several representations a click cycles to the next; with exactly one
 * it stays put (the honest behaviour — there is no second legitimate value
 * to move to, and inventing one is the thing this must never do). With none
 * — nothing connected, or the MAC not read yet — it renders a plain dash
 * and is not interactive, because there is nothing to toggle between.
 */
function HardwareField({ label, options, clicks, onCycle, hint }) {
  if (options.length === 0) {
    return (
      <span className="hw-field is-empty">
        {label}: <span className="hw-value">{FIELD_EMPTY}</span>
      </span>
    )
  }

  const value = options[clicks % options.length]
  const cyclable = options.length > 1

  return (
    <button
      type="button"
      className={cyclable ? 'hw-field is-cyclable' : 'hw-field'}
      onClick={onCycle}
      // Says what a click does, and — when there is only one representation
      // — says so plainly rather than implying a hidden alternative exists.
      title={cyclable ? hint : `${value} (only representation available)`}
      aria-label={`${label}: ${value}${cyclable ? '. Click to show another representation.' : ''}`}
    >
      {label}: <span className="hw-value">{value}</span>
    </button>
  )
}
