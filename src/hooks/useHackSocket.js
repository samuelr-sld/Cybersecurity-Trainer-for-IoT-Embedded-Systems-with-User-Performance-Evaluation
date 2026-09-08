import { useCallback, useEffect, useRef, useState } from 'react'

export const CONNECTION_STATUS = {
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  DISCONNECTED: 'disconnected',
  ERROR: 'error',
}

/**
 * Resolve the Hack Mode WebSocket URL for the current environment.
 *
 * `VITE_HACK_WS_URL` overrides everything, for setups where the frontend and
 * backend are not simply "same host, different port" (e.g. behind a reverse
 * proxy). Absent that, the URL is derived from the page's own protocol and
 * hostname — so it keeps working under https/wss and on whatever host the
 * dev server is opened from, never a hardcoded production hostname — plus a
 * port that defaults to the backend's own dev default (see
 * backend/app/config.py: TRAINER_PORT=8000) and can be overridden with
 * `VITE_HACK_WS_PORT` alone when only the port differs.
 */
function resolveWsUrl() {
  const configured = import.meta.env.VITE_HACK_WS_URL
  if (configured) return configured
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const port = import.meta.env.VITE_HACK_WS_PORT || '8000'
  return `${protocol}//${window.location.hostname}:${port}/ws/hack`
}

/**
 * Owns the Hack Mode WebSocket connection.
 *
 * One socket per mount: created when this hook's owner mounts, closed when
 * it unmounts. The effect has an empty dependency array, so React's
 * mount -> cleanup -> mount Strict Mode probe just creates, immediately
 * closes, and recreates the socket — the same pattern HackTerminal already
 * uses for its xterm instance — rather than this hook needing its own
 * duplicate-connection guard.
 *
 * `handlers` is read through a ref refreshed on every render, so the caller
 * can pass fresh closures each render (as HackMode does) without that
 * churning the effect and reconnecting the socket.
 */
export default function useHackSocket(handlers) {
  const handlersRef = useRef(handlers)
  // Refreshed after every render (not during render — refs are an external
  // system as far as React's render phase is concerned) so the connection
  // effect below always calls the latest closures without depending on
  // `handlers` and reconnecting because of it.
  useEffect(() => {
    handlersRef.current = handlers
  })

  const socketRef = useRef(null)
  const [status, setStatus] = useState(CONNECTION_STATUS.CONNECTING)

  useEffect(() => {
    const socket = new WebSocket(resolveWsUrl())
    socketRef.current = socket
    // No setStatus(CONNECTING) here: the useState above already initializes
    // to CONNECTING, and this effect only ever runs once per mount.

    socket.onopen = () => setStatus(CONNECTION_STATUS.CONNECTED)
    socket.onclose = () => setStatus(CONNECTION_STATUS.DISCONNECTED)
    socket.onerror = () => setStatus(CONNECTION_STATUS.ERROR)
    socket.onmessage = (evt) => {
      let message
      try {
        message = JSON.parse(evt.data)
      } catch {
        return
      }
      const h = handlersRef.current
      switch (message.type) {
        case 'session':
          h.onSession?.(message)
          break
        case 'output':
          h.onOutput?.(message.data)
          break
        case 'action':
          h.onAction?.(message.action)
          break
        case 'error':
          h.onError?.(message.message)
          break
        case 'event':
          h.onEvent?.(message)
          break
        case 'state':
          h.onState?.(message.data)
          break
        default:
          break
      }
    }

    return () => {
      // Detach handlers before closing so no late native event (the close
      // this triggers included) can call back into a hook instance whose
      // owner is already unmounting.
      socket.onopen = null
      socket.onclose = null
      socket.onerror = null
      socket.onmessage = null
      socket.close()
      socketRef.current = null
    }
  }, [])

  const sendInput = useCallback((line) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data: line }))
    }
  }, [])

  const sendResize = useCallback((cols, rows) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'resize', cols, rows }))
    }
  }, [])

  return { status, sendInput, sendResize }
}
