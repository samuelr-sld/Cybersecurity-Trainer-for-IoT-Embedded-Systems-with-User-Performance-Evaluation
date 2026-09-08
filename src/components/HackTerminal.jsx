import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

// Reads resolved (computed) CSS custom property values from the DOM.
// xterm's `theme` option requires concrete color strings — it cannot
// consume `var(--foo)` expressions directly, so custom properties must
// be resolved via getComputedStyle() before being handed to xterm.
function readCssVar(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function buildTheme() {
  return {
    background: readCssVar('--term', '#0d0d0d'),
    foreground: '#d8d8d8',
    cursor: '#d8d8d8',
    selectionBackground: '#3a3a3a',
  }
}

/**
 * Transport-agnostic xterm.js host.
 *
 * This component owns the xterm `Terminal` instance, its DOM lifecycle,
 * and resize handling. It has no knowledge of tools, guided steps, or
 * where terminal data comes from/goes to — callers drive it entirely
 * through `onInput` (keystrokes out) and the imperative handle (text in).
 *
 * Phase 2D-B note: HackMode.jsx now drives this component over a real
 * WebSocket (see src/hooks/useHackSocket.js) instead of the earlier mock
 * transport, by changing only what `onInput` does and who calls the
 * imperative `write` API — this component did not need to change.
 *
 * `bootText`, if given, is written once per real Terminal instance, inside
 * the same effect that creates it. That ties the one-time write to the
 * instance's own create/dispose lifecycle instead of a separate effect, so
 * React Strict Mode's dev-only mount→cleanup→mount probe can't duplicate
 * it: the probe's first Terminal (and whatever was written to it) is fully
 * disposed before the surviving instance is created and written to.
 */
const HackTerminal = forwardRef(function HackTerminal({ onInput, onResize, bootText }, ref) {
  const hostRef = useRef(null)
  const termRef = useRef(null)
  const fitAddonRef = useRef(null)

  useImperativeHandle(ref, () => ({
    write(data) {
      termRef.current?.write(data)
    },
    writeln(data) {
      termRef.current?.writeln(data)
    },
    clear() {
      termRef.current?.clear()
    },
    focus() {
      termRef.current?.focus()
    },
  }))

  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined

    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily: 'var(--mono)',
      fontSize: 13,
      theme: buildTheme(),
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(host)
    fitAddon.fit()

    if (bootText) term.write(bootText)

    termRef.current = term
    fitAddonRef.current = fitAddon

    const dataDisposable = term.onData((data) => {
      onInput?.(data)
    })
    const resizeDisposable = term.onResize(({ cols, rows }) => {
      onResize?.({ cols, rows })
    })

    const resizeObserver = new ResizeObserver(() => {
      fitAddonRef.current?.fit()
    })
    resizeObserver.observe(host)

    return () => {
      resizeObserver.disconnect()
      dataDisposable.dispose()
      resizeDisposable.dispose()
      term.dispose()
      termRef.current = null
      fitAddonRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <div className="xterm-host" ref={hostRef} />
})

export default HackTerminal
