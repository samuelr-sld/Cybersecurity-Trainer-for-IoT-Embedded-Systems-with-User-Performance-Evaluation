/**
 * Shared hardware-status display behaviour for Build Mode and Hack Mode.
 *
 *                          ESP32
 *                            |
 *              Arduino CLI detection + esptool read_mac
 *                            |
 *                   Shared Device State (backend/app/hardware/)
 *                       /            \
 *                      v              v
 *              Build Mode          Hack Mode
 *              `state.hardware`    `hardware` frame
 *                      \              /
 *                       v            v
 *                    this module + <HardwareHeaderStatus>
 *
 * Both modes already receive the SAME backend payload — one `device_monitor`
 * per process, one `DeviceStatus` vocabulary, and (since the panel-identity
 * change) literally the same snapshot shape on both channels. This module is
 * the frontend half of that guarantee: the field vocabulary lives here once,
 * so the two screens cannot drift into describing one physical board
 * differently.
 *
 * NOTHING HERE INVENTS A VALUE. Every MAC, panel name, and port rendered by
 * these helpers was read back from the real device by the backend. There is
 * no hardcoded MAC, no hardcoded COM/tty port, no hardcoded panel name, and
 * no default of "connected" — anything unknown renders as an explicit dash,
 * never as a plausible-looking guess.
 */

/** Shown wherever a field has no truthful value to display. */
export const FIELD_EMPTY = '—'

/**
 * How often either mode asks the backend to re-run device discovery while
 * it stays open — the only way a plug or an unplug is ever reflected, since
 * nothing pushes an OS-level USB event to these sockets.
 *
 * Kept deliberately infrequent (see CLAUDE.md: "avoid aggressive polling")
 * next to a real `arduino-cli board list` invocation, and comfortably
 * longer than the backend's own coalescing window
 * (TRAINER_HARDWARE_CACHE_SECONDS, 4s) so a poll always gets a fresh
 * answer while several sessions polling at once still collapse into one CLI
 * call. A poll does NOT re-read the MAC: the backend caches that per port,
 * because reading it resets the board.
 */
export const HARDWARE_POLL_INTERVAL_MS = 10000

/** The shape a mode renders before the backend has told it anything. */
export const UNKNOWN_HARDWARE = {
  status: 'not_checked',
  connected: false,
  board_name: null,
  port: null,
  port_aliases: [],
  mac: null,
  panel: null,
}

/**
 * The interchangeable ways to name the connected panel, in display order.
 *
 * MAC first — it is the board's durable physical identity and is always
 * available once probed — then the human-readable panel name, when this MAC
 * is mapped in backend/app/hardware/panels.py. An unmapped board yields
 * only the MAC, so the field shows the address rather than an invented
 * panel name. Empty when nothing is connected, or while the MAC is still
 * being read.
 *
 * @param {object|null} hardware
 * @returns {string[]}
 */
export function panelRepresentations(hardware) {
  if (!hardware?.connected) return []
  return [hardware.mac, hardware.panel].filter(Boolean)
}

/**
 * The legitimate ways this machine spells the connected device's port.
 *
 * Taken verbatim from `port_aliases`, which the backend built from what
 * `arduino-cli board list` actually reported (its `address` and, when it
 * differs, its `label`). On Windows both are "COM3", so there is exactly
 * one representation and the field simply stays on it; on Linux they can
 * genuinely differ. Falls back to the single `port` for a payload that
 * predates aliases. Never fabricates an alternative spelling.
 *
 * @param {object|null} hardware
 * @returns {string[]}
 */
export function usbRepresentations(hardware) {
  if (!hardware?.connected) return []
  const aliases = Array.isArray(hardware.port_aliases) ? hardware.port_aliases : []
  const candidates = aliases.length > 0 ? aliases : [hardware.port]
  return candidates.filter(Boolean)
}

/**
 * The student-facing connection word — only ever CONNECTED or DISCONNECTED.
 *
 * The backend keeps a richer internal vocabulary (not_checked, detecting,
 * ambiguous, error — see backend/app/hardware/state.py) and that stays
 * available for diagnostics, but the header collapses all of it: anything
 * that is not a confirmed single attached board reads DISCONNECTED. This is
 * deliberately about the ESP32, never about the WebSocket.
 *
 * @param {object|null} hardware
 * @returns {'CONNECTED'|'DISCONNECTED'}
 */
export function connectionLabel(hardware) {
  return hardware?.connected ? 'CONNECTED' : 'DISCONNECTED'
}
