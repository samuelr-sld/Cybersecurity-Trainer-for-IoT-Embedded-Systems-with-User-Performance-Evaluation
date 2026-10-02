import { useEffect, useState } from 'react'
import { fetchHardwareStatus } from '../api/trainerApi'
import { HARDWARE_POLL_INTERVAL_MS } from '../hardware/deviceState'

/**
 * The attached panel / USB report for a screen with no mode WebSocket (the Main
 * Menu), from GET /api/hardware/status.
 *
 * LIGHTWEIGHT BY CONSTRUCTION. This does not start anything the backend was not
 * already doing: the endpoint reads the shared device monitor that Hack and Build
 * already poll, which caches and coalesces. On the page side that means:
 *
 *   - one request on mount, then one per `HARDWARE_POLL_INTERVAL_MS` (the same
 *     slow interval the modes use), never per render;
 *   - nothing at all while the tab is hidden, and one immediate refresh when it
 *     comes back, so a returning student is not shown a stale board;
 *   - never two in flight: a slow answer (a wedged USB driver can take many
 *     seconds) is waited for, not stacked behind a second request;
 *   - everything stops on unmount, including a request already in flight.
 *
 * It keeps the last good report through a failed request rather than blanking
 * to "No device" — failing to ask is not the same as the board being gone —
 * and only reports `unreachable` when there has never been an answer.
 *
 * @returns {{ report: object|null, unreachable: boolean }}
 */
export default function useHardwareStatus(intervalMs = HARDWARE_POLL_INTERVAL_MS) {
  const [state, setState] = useState({ report: null, unreachable: false })

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    let inFlight = false

    async function check() {
      if (inFlight || document.hidden) return
      inFlight = true
      try {
        const report = await fetchHardwareStatus(controller.signal)
        if (active) setState({ report, unreachable: false })
      } catch {
        if (active) setState((previous) => (previous.report ? previous : { report: null, unreachable: true }))
      } finally {
        inFlight = false
      }
    }

    function onVisible() {
      if (!document.hidden) check()
    }

    check()
    const id = setInterval(check, intervalMs)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      active = false
      controller.abort()
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [intervalMs])

  return state
}
