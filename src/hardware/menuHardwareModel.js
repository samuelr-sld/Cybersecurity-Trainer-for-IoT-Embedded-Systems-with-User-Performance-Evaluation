import { FIELD_EMPTY } from './deviceState.js'

/**
 * What the Main Menu's PANEL and USB readouts say, from the backend's
 * GET /api/hardware/status report.
 *
 * The report is the shared device layer's verdict, so every word here maps one
 * backend fact to one label and nothing more:
 *
 *   PANEL   identified     -> the registered panel's name
 *           unregistered   -> Unregistered   (MAC read, bound to no panel)
 *           unidentified   -> Unknown        (a board is there, MAC not read)
 *           not_connected  -> No device
 *   USB     a board present -> its real port
 *           otherwise       -> Disconnected
 *
 * The panel is NEVER derived from the port. A port is a USB bridge, not a
 * panel, so nothing in this module reads `usb.port` for the PANEL field.
 *
 * Two states are not the backend's to answer and so are not "No device":
 * before the first answer (or while the backend itself has not run its first
 * detection) it is CHECKING, and if the backend cannot be reached at all the
 * page cannot know and shows the project's usual dash.
 */

export const CHECKING = 'Checking…'

/** `tone` maps to the shared `.dot` modifiers: is-ok / is-warn / is-bad / (none). */
function field(label, tone) {
  return { label, tone }
}

/**
 * @param {object|null} report  the last good response, or null before any
 * @param {boolean} unreachable the last request failed (and there is no report)
 * @returns {{ panel: {label: string, tone: string}, usb: {label: string, tone: string} }}
 */
export function menuHardwareView(report, unreachable = false) {
  if (!report) {
    const unknown = unreachable ? field(FIELD_EMPTY, '') : field(CHECKING, '')
    return { panel: unknown, usb: unknown }
  }
  return { panel: panelField(report.panel), usb: usbField(report.usb, report.panel) }
}

function panelField(panel) {
  switch (panel?.status) {
    case 'identified':
      // A name the backend did not send is not invented from the MAC or port.
      return panel.name ? field(panel.name, 'is-ok') : field('Unknown', 'is-warn')
    case 'unregistered':
      return field('Unregistered', 'is-warn')
    case 'unidentified':
      return field('Unknown', 'is-warn')
    case 'not_connected':
      return field('No device', '')
    case 'not_checked':
      return field(CHECKING, '')
    default:
      return field(FIELD_EMPTY, '')
  }
}

function usbField(usb, panel) {
  // Until the backend has run its first detection it cannot say "Disconnected".
  if (panel?.status === 'not_checked') return field(CHECKING, '')
  if (usb?.connected && usb.port) return field(usb.port, 'is-ok')
  return field('Disconnected', 'is-bad')
}
