import { useEffect, useState } from 'react'
import { fetchHealth } from '../api/trainerApi'

export const BACKEND_STATUS = {
  CHECKING: 'checking',
  ONLINE: 'online',
  OFFLINE: 'offline',
}

/**
 * Whether the trainer backend answers its liveness probe, re-checked on an
 * interval while the owning screen is mounted. This is the one connection fact
 * the screens outside Hack/Build can truthfully show: the ESP32's own state
 * only reaches the page through a mode's WebSocket, so nothing here claims a
 * panel is attached.
 */
export default function useBackendStatus(intervalMs = 15000) {
  const [status, setStatus] = useState(BACKEND_STATUS.CHECKING)

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    async function check() {
      const ok = await fetchHealth(controller.signal)
      if (active) setStatus(ok ? BACKEND_STATUS.ONLINE : BACKEND_STATUS.OFFLINE)
    }
    check()
    const id = setInterval(check, intervalMs)
    return () => {
      active = false
      controller.abort()
      clearInterval(id)
    }
  }, [intervalMs])

  return status
}
