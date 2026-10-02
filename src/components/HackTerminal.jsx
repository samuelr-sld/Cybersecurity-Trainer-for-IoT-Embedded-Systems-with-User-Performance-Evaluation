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

// The design tokens (src/styles/tokens.css) as an xterm theme. xterm draws
// bold ANSI colours in their "bright" variant, so each colour the prompt and
// notices use is set for both the normal and bright slot.
function buildTheme() {
  const success = readCssVar('--success', '#b9f56b')
  const info = readCssVar('--info', '#7aa7e0')
  const danger = readCssVar('--danger', '#ff7b7b')
  const warning = readCssVar('--warning', '#f2d34a')
  return {
    background: readCssVar('--base', '#141316'),
    foreground: readCssVar('--text', '#e5e4e5'),
    cursor: readCssVar('--text', '#e5e4e5'),
    cursorAccent: readCssVar('--base', '#141316'),
    selectionBackground: 'rgba(122, 167, 224, 0.35)',
    green: success,
    brightGreen: success,
    blue: info,
    brightBlue: info,
    red: danger,
    brightRed: danger,
    yellow: warning,
    brightYellow: warning,
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
    // The whole scrollback as plain text (what "copy output" copies). Wrapped
    // lines are rejoined so a long command is not split at the terminal width.
    getText() {
      const buffer = termRef.current?.buffer.active
      if (!buffer) return ''
      const lines = []
      for (let i = 0; i < buffer.length; i += 1) {
        const line = buffer.getLine(i)
        if (!line) continue
        const text = line.translateToString(true)
        if (line.isWrapped && lines.length > 0) lines[lines.length - 1] += text
        else lines.push(text)
      }
      return lines.join('\n').replace(/\s+$/, '')
    },
  }))

  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined

    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily: readCssVar('--font-mono', 'monospace'),
      fontSize: 14,
      lineHeight: 1.45,
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
