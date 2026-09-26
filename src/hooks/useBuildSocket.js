import { useCallback, useEffect, useRef, useState } from 'react'

export const CONNECTION_STATUS = {
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  DISCONNECTED: 'disconnected',
  ERROR: 'error',
}

/**
 * Resolve the Build Mode WebSocket URL for the current environment.
 *
 * Mirrors `src/hooks/useHackSocket.js`'s `resolveWsUrl`: `VITE_BUILD_WS_URL`
 * overrides everything, otherwise the URL is derived from the page's own
 * protocol/hostname plus a port that defaults to the backend's dev default
 * (see backend/app/config.py: TRAINER_PORT=8000), overridable alone with
 * `VITE_BUILD_WS_PORT`. Build Mode is a separate channel (`/ws/build`) from
 * Hack Mode's `/ws/hack` — see backend/app/build_websocket.py.
 */
function resolveWsUrl() {
  const configured = import.meta.env.VITE_BUILD_WS_URL
  if (configured) return configured
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const port = import.meta.env.VITE_BUILD_WS_PORT || '8000'
  return `${protocol}//${window.location.hostname}:${port}/ws/build`
}

/**
 * Owns the Build Mode WebSocket connection.
 *
 * One socket per mount, following the exact lifecycle pattern
 * `useHackSocket` uses: created on mount, closed on unmount, with `handlers`
 * read through a ref so the caller can pass fresh closures every render
 * without reconnecting the socket.
 */
export default function useBuildSocket(handlers) {
  const handlersRef = useRef(handlers)
  useEffect(() => {
    handlersRef.current = handlers
  })

  const socketRef = useRef(null)
  const [status, setStatus] = useState(CONNECTION_STATUS.CONNECTING)

  useEffect(() => {
    const socket = new WebSocket(resolveWsUrl())
    socketRef.current = socket

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
        case 'state':
          h.onState?.(message.data)
          break
        case 'section':
          // Phase B8 correction: the read leg of the section -> Blockly
          // contract (backend/app/models/build_messages.py: BuildSectionMessage).
          // Answers a `section_blockly` request — {path, sectionId,
          // representable, workspace, preserved} — and is never followed by
          // a `state` frame, since opening a section to look at it changes
          // nothing.
          h.onSection?.(message.data)
          break
        case 'event':
          h.onEvent?.(message)
          break
        case 'error':
          h.onError?.(message.message)
          break
        default:
          break
      }
    }

    return () => {
      socket.onopen = null
      socket.onclose = null
      socket.onerror = null
      socket.onmessage = null
      socket.close()
      socketRef.current = null
    }
  }, [])

  const sendEditRegion = useCallback((path, regionId, source) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(
        JSON.stringify({ type: 'edit_region', path, region_id: regionId, source }),
      )
    }
  }, [])

  // THE INTENDED EDITING INTERFACE (Phase B8 correction). A student clicks a
  // discovered section; this asks for its Blockly representation. Read-only
  // — see backend/app/models/build_messages.py: it emits no event and no
  // `state`, only a `section` frame (routed to `onSection` above).
  const sendSectionBlockly = useCallback((path, sectionId) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'section_blockly', path, section_id: sectionId }))
    }
  }, [])

  // The write leg. `workspace` is the Blockly serialization state
  // (`Blockly.serialization.workspaces.save(...)`) for exactly the one
  // section named by `sectionId`; `preserved` is the fragment list that came
  // back with it (or an edited copy of that list — e.g. cleared by the
  // student). There is no `source` field: the backend regenerates C++ from
  // what the blocks mean, never from text this frontend sends.
  const sendEditSectionBlocks = useCallback((path, sectionId, workspace, preserved) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(
        JSON.stringify({
          type: 'edit_section_blocks',
          path,
          section_id: sectionId,
          workspace,
          preserved,
        }),
      )
    }
  }, [])

  // Field-less by design — see backend/app/models/build_messages.py:
  // a compile always targets the session's own current workspace and
  // board, never anything this call could name or supply.
  const sendCompile = useCallback(() => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'compile' }))
    }
  }, [])

  // Field-less by design, for stronger reasons than compile — see
  // backend/app/models/build_messages.py: a `port`, an executable, a
  // firmware path or an extra upload flag here would hand this frontend
  // control over which physical device the backend writes firmware to. The
  // port is discovered server-side, the firmware is whatever the session's
  // own last successful compile produced, and the board comes from the
  // project definition.
  const sendFlash = useCallback(() => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'flash' }))
    }
  }, [])

  // Field-less, read-only, and safe to send as often as needed — see
  // backend/app/models/build_messages.py: this only asks the backend to
  // re-run its own real `arduino-cli board list` discovery and refresh the
  // `hardware` block of the next `state` snapshot. Never uploads anything.
  const sendHardwareStatus = useCallback(() => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'hardware_status' }))
    }
  }, [])

  // Field-less, same reasoning as `sendCompile`/`sendFlash` — see
  // backend/app/models/build_messages.py: `ValidateMessage` carries no
  // fields because the verdict must come from the backend's own validator
  // judging the firmware it already flashed, never from anything a client
  // asserts. The backend refuses this outright unless the session's own
  // workspace was already flashed successfully (app/build/service.py:
  // validate_workspace), so this call never bypasses that gate — it just
  // asks.
  const sendValidate = useCallback(() => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'validate' }))
    }
  }, [])

  return {
    status,
    sendEditRegion,
    sendSectionBlockly,
    sendEditSectionBlocks,
    sendCompile,
    sendFlash,
    sendHardwareStatus,
    sendValidate,
  }
}
