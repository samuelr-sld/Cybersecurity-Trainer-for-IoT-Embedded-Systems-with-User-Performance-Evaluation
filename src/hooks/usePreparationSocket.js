import { useEffect, useRef } from 'react'

/**
 * Resolve the Mode Session Preparation WebSocket URL.
 *
 * Mirrors useHackSocket/useBuildSocket: `VITE_PREPARE_WS_URL` overrides
 * everything; otherwise the page's own protocol/hostname plus the backend's
 * dev port (overridable alone with `VITE_PREPARE_WS_PORT`).
 */
function resolveWsUrl() {
  const configured = import.meta.env.VITE_PREPARE_WS_URL
  if (configured) return configured
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const port = import.meta.env.VITE_PREPARE_WS_PORT || '8000'
  return `${protocol}//${window.location.hostname}:${port}/ws/prepare`
}

/**
 * Runs ONE backend preparation for `mode` (backend/app/preparation_websocket.py).
 *
 * One socket per mount, one `prepare` frame per socket: a retry is a remount
 * (the caller changes the component `key`), so every attempt starts from a
 * clean connection with no leftover state. Unmounting closes the socket,
 * which the backend treats as cancelling the preparation.
 *
 * The only thing sent is the mode — there is no field for a panel, a port or
 * a firmware; the backend resolves all of those from the attached ESP32.
 */
export default function usePreparationSocket(mode, handlers) {
  const handlersRef = useRef(handlers)
  useEffect(() => {
    handlersRef.current = handlers
  })

  useEffect(() => {
    const socket = new WebSocket(resolveWsUrl())
    socket.onopen = () => socket.send(JSON.stringify({ type: 'prepare', mode }))
    socket.onmessage = (evt) => {
      let message
      try {
        message = JSON.parse(evt.data)
      } catch {
        return
      }
      handlersRef.current.onFrame?.(message)
    }
    socket.onclose = () => handlersRef.current.onClose?.()
    socket.onerror = () => handlersRef.current.onClose?.()

    return () => {
      socket.onopen = null
      socket.onmessage = null
      socket.onclose = null
      socket.onerror = null
      socket.close()
    }
  }, [mode])
}
