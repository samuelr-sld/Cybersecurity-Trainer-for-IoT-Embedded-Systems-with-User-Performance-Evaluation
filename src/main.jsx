import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// The bundled fonts are local files, so loading them takes a few milliseconds.
// Waiting for them before the first render keeps the interface from re-flowing
// into the web fonts, and lets xterm.js measure its character cells with the
// real terminal font. A broken or slow font file never blocks the app: the wait
// is capped and the system fallback fonts in the font stacks take over.
const FONT_WAIT_MS = 1500
const FONTS = [
  '400 14px "Inter"',
  '600 14px "Inter"',
  '700 14px "Inter"',
  '600 26px "Roboto Condensed"',
  '400 14px "JetBrains Mono"',
]

const fontsReady = document.fonts
  ? Promise.race([
      Promise.all(FONTS.map((font) => document.fonts.load(font))),
      new Promise((resolve) => setTimeout(resolve, FONT_WAIT_MS)),
    ]).catch(() => undefined)
  : Promise.resolve()

fontsReady.then(() => {
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
