import chalkboardUserSvg from '../assets/icons/chalkboard-user-solid-full.svg?raw'
import userSvg from '../assets/icons/user-solid-full.svg?raw'

/**
 * The application's icon set.
 *
 * Offline by construction: every glyph is inline SVG, so nothing is fetched at
 * run time and there is no icon font or CDN. Strokes and fills use
 * `currentColor`, so an icon takes the colour of the text around it.
 *
 * `user` and `chalkboard-user` are Font Awesome Free 7.3.1 solid icons
 * (fontawesome.com/license/free, icons CC BY 4.0), kept exactly as supplied in
 * src/assets/icons/ and read from those files below — replacing a file there
 * replaces the icon. All other glyphs are the designed outline icons from the
 * CyberTrainer reference screens.
 *
 * Decorative by default (`aria-hidden`). Pass `title` only for an icon that
 * stands alone without a text label.
 */

// The `d` of the single <path> in a Font Awesome SVG file.
const pathOf = (svg) => /\sd="([^"]+)"/.exec(svg)?.[1] ?? ''

const FA_USER = pathOf(userSvg)
const FA_CHALKBOARD_USER = pathOf(chalkboardUserSvg)

const STROKE = { fill: 'none', stroke: 'currentColor', strokeLinecap: 'round', strokeLinejoin: 'round' }
const FILL = { fill: 'currentColor', stroke: 'none' }

// name -> { box, base, body }. `base` is the default paint for the <svg> root.
const ICONS = {
  // --- chrome ---------------------------------------------------------------
  menu: {
    box: '0 0 28 20',
    base: FILL,
    body: (
      <>
        <rect y="0" width="28" height="3.5" rx="1" />
        <rect y="8.25" width="28" height="3.5" rx="1" />
        <rect y="16.5" width="28" height="3.5" rx="1" />
      </>
    ),
  },
  'arrow-right': { box: '0 0 16 16', base: STROKE, body: <path d="M3 8h10M9 4l4 4-4 4" strokeWidth="1.8" /> },
  'arrow-left': { box: '0 0 14 14', base: STROKE, body: <path d="M12 7H2M6 3L2 7l4 4" strokeWidth="1.6" /> },
  'chevron-down': { box: '0 0 10 10', base: STROKE, body: <path d="M2 3.5l3 3 3-3" strokeWidth="1.5" /> },
  'chevron-up': { box: '0 0 10 10', base: STROKE, body: <path d="M2 6.5l3-3 3 3" strokeWidth="1.5" /> },
  // A panel taking all the room / giving it back (the usual window vocabulary).
  maximize: { box: '0 0 14 14', base: STROKE, body: <rect x="2.5" y="2.5" width="9" height="9" rx="1.6" strokeWidth="1.4" /> },
  restore: {
    box: '0 0 14 14',
    base: STROKE,
    body: (
      <>
        <rect x="2.5" y="4.5" width="7" height="7" rx="1.4" strokeWidth="1.3" />
        <path d="M4.5 4.5V3.4a.9.9 0 01.9-.9h5.2a.9.9 0 01.9.9v5.2a.9.9 0 01-.9.9H9.5" strokeWidth="1.3" />
      </>
    ),
  },
  // The whole screen (not one panel, as maximize/restore are): corner brackets
  // pointing out to enter fullscreen, and in to leave it.
  'fullscreen-enter': { box: '0 0 20 20', base: STROKE, body: <path d="M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4" strokeWidth="1.8" /> },
  'fullscreen-exit': { box: '0 0 20 20', base: STROKE, body: <path d="M7 3v4H3M13 3v4h4M17 13h-4v4M3 13h4v4" strokeWidth="1.8" /> },
  'chevron-left': { box: '0 0 12 12', base: STROKE, body: <path d="M8 2L4 6l4 4" strokeWidth="1.7" /> },
  'chevron-right': { box: '0 0 12 12', base: STROKE, body: <path d="M4 2l4 4-4 4" strokeWidth="1.7" /> },
  'caret-up': { box: '0 0 10 10', base: FILL, body: <path d="M5 2l4 6H1z" /> },
  'caret-down': { box: '0 0 10 10', base: FILL, body: <path d="M5 8L1 2h8z" /> },
  close: { box: '0 0 10 10', base: STROKE, body: <path d="M2 2l6 6M8 2L2 8" strokeWidth="1.4" /> },
  plus: { box: '0 0 14 14', base: STROKE, body: <path d="M7 2v10M2 7h10" strokeWidth="1.6" /> },
  refresh: {
    box: '0 0 14 14',
    base: STROKE,
    body: <path d="M11.5 7a4.5 4.5 0 1 1-1.4-3.2M11.5 2v2.5H9" strokeWidth="1.4" />,
  },
  search: {
    box: '0 0 18 18',
    base: STROKE,
    body: (
      <>
        <circle cx="8" cy="8" r="5" strokeWidth="1.6" />
        <path d="M12 12l4 4" strokeWidth="1.6" />
      </>
    ),
  },
  download: {
    box: '0 0 16 16',
    base: STROKE,
    body: <path d="M8 2v8.5M4.5 7L8 10.5 11.5 7M2.5 13.5h11" strokeWidth="1.7" />,
  },
  info: {
    box: '0 0 14 14',
    base: STROKE,
    body: (
      <>
        <circle cx="7" cy="7" r="6" strokeWidth="1.3" />
        <path d="M7 6.2v3.8M7 3.9v.2" strokeWidth="1.5" />
      </>
    ),
  },
  warning: {
    box: '0 0 14 14',
    base: STROKE,
    body: (
      <>
        <path d="M7 1.5L13 12H1z" strokeWidth="1.4" />
        <path d="M7 5.5v3M7 10v.1" strokeWidth="1.4" />
      </>
    ),
  },
  eye: {
    box: '0 0 20 20',
    base: STROKE,
    body: (
      <>
        <path d="M2 10s3-6 8-6 8 6 8 6-3 6-8 6-8-6-8-6z" strokeWidth="1.5" />
        <circle cx="10" cy="10" r="2.5" strokeWidth="1.5" />
      </>
    ),
  },
  'eye-off': {
    box: '0 0 20 20',
    base: STROKE,
    body: (
      <>
        <path d="M2 10s3-6 8-6 8 6 8 6-3 6-8 6-8-6-8-6z" strokeWidth="1.5" />
        <circle cx="10" cy="10" r="2.5" strokeWidth="1.5" />
        <path d="M3.5 16.5l13-13" strokeWidth="1.5" />
      </>
    ),
  },
  lock: {
    box: '0 0 18 18',
    base: STROKE,
    body: (
      <>
        <rect x="3" y="8" width="12" height="7.5" rx="2" strokeWidth="1.5" />
        <path d="M6 8V5.8a3 3 0 0 1 6 0V8" strokeWidth="1.5" />
      </>
    ),
  },
  'id-card': {
    box: '0 0 18 18',
    base: STROKE,
    body: (
      <>
        <rect x="1.5" y="3.5" width="15" height="11" rx="2" strokeWidth="1.5" />
        <circle cx="6" cy="8.5" r="1.6" strokeWidth="1.3" />
        <path d="M10 7.5h4M10 10.5h3" strokeWidth="1.3" />
      </>
    ),
  },
  users: {
    box: '0 0 18 18',
    base: STROKE,
    body: (
      <>
        <circle cx="6.5" cy="6" r="2.7" strokeWidth="1.5" />
        <path d="M1.5 15c.4-2.6 2.2-3.8 5-3.8s4.6 1.2 5 3.8M12 3.5a2.7 2.7 0 0 1 0 5M13.5 11.5c1.8.4 2.8 1.6 3 3.5" strokeWidth="1.5" />
      </>
    ),
  },

  // --- Font Awesome Free (user-supplied files) --------------------------------
  user: { box: '0 0 640 640', base: FILL, body: <path d={FA_USER} /> },
  'chalkboard-user': { box: '0 0 640 640', base: FILL, body: <path d={FA_CHALKBOARD_USER} /> },

  // --- actions ----------------------------------------------------------------
  play: { box: '0 0 16 16', base: FILL, body: <path d="M3 2.5v11l10-5.5z" /> },
  bolt: { box: '0 0 16 16', base: FILL, body: <path d="M9 1.5L3.5 9H8l-1 5.5L12.5 7H8z" /> },
  shield: {
    box: '0 0 16 16',
    base: STROKE,
    body: <path d="M8 1.5l5.5 2v4.2c0 3-2.3 5.6-5.5 6.8-3.2-1.2-5.5-3.8-5.5-6.8V3.5z" strokeWidth="1.4" />,
  },
  copy: {
    box: '0 0 16 16',
    base: STROKE,
    body: (
      <>
        <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" strokeWidth="1.3" />
        <path d="M10.5 3.5v-.5a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 3v5A1.5 1.5 0 0 0 4 9.5h.5" strokeWidth="1.3" />
      </>
    ),
  },
  trash: {
    box: '0 0 16 16',
    base: STROKE,
    body: <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8h5.8l.6-8" strokeWidth="1.3" />,
  },
  undo: {
    box: '0 0 16 16',
    base: STROKE,
    body: <path d="M3.5 6.5H10a3.5 3.5 0 0 1 0 7H6M3.5 6.5L6 4M3.5 6.5L6 9" strokeWidth="1.4" />,
  },
  redo: {
    box: '0 0 16 16',
    base: STROKE,
    body: <path d="M12.5 6.5H6a3.5 3.5 0 0 0 0 7h4M12.5 6.5L10 4M12.5 6.5L10 9" strokeWidth="1.4" />,
  },
  target: {
    box: '0 0 20 20',
    base: STROKE,
    body: (
      <>
        <circle cx="10" cy="10" r="4.5" strokeWidth="1.5" />
        <circle cx="10" cy="10" r="1.4" fill="currentColor" stroke="none" />
        <path d="M10 1.5V4M10 16v2.5M1.5 10H4M16 10h2.5" strokeWidth="1.5" />
      </>
    ),
  },
  'zoom-in': { box: '0 0 18 18', base: STROKE, body: <path d="M9 3v12M3 9h12" strokeWidth="1.8" /> },
  'zoom-out': { box: '0 0 18 18', base: STROKE, body: <path d="M3 9h12" strokeWidth="1.8" /> },
  'panel-collapse': {
    box: '0 0 14 14',
    base: STROKE,
    body: <path d="M8.5 3L4.5 7l4 4M11.5 3v8" strokeWidth="1.5" />,
  },

  // --- files & workspace --------------------------------------------------------
  file: {
    box: '0 0 16 16',
    base: STROKE,
    body: <path d="M3.5 1.8h5.5L12.5 5.3v8.9h-9z M9 1.8v3.5h3.5" strokeWidth="1.2" />,
  },
  folder: {
    box: '0 0 16 16',
    base: STROKE,
    body: (
      <path
        d="M1.8 4a1.2 1.2 0 0 1 1.2-1.2h3l1.5 1.7H13A1.2 1.2 0 0 1 14.2 5.7V12A1.2 1.2 0 0 1 13 13.2H3A1.2 1.2 0 0 1 1.8 12z"
        strokeWidth="1.2"
      />
    ),
  },
  'file-sketch': {
    box: '0 0 16 16',
    base: STROKE,
    body: (
      <>
        <circle cx="8" cy="8" r="6.5" strokeWidth="1.3" />
        <path d="M4.5 8h7M8 4.5v7" strokeWidth="1.3" />
      </>
    ),
  },
  terminal: {
    box: '0 0 16 16',
    base: STROKE,
    body: (
      <>
        <rect x="1.5" y="2.5" width="13" height="11" rx="2" strokeWidth="1.3" />
        <path d="M4.5 6l2.2 2-2.2 2M8.5 10.2h3" strokeWidth="1.3" style={{ stroke: 'var(--success)' }} />
      </>
    ),
  },
  'rail-files': {
    box: '0 0 20 20',
    base: STROKE,
    body: (
      <path
        d="M2.5 5a1.5 1.5 0 0 1 1.5-1.5h3.5l1.8 2H16A1.5 1.5 0 0 1 17.5 7v7.5A1.5 1.5 0 0 1 16 16H4a1.5 1.5 0 0 1-1.5-1.5z"
        strokeWidth="1.6"
      />
    ),
  },
  'rail-checklist': {
    box: '0 0 20 20',
    base: STROKE,
    body: (
      <>
        <path d="M3 3.5l1.5 1.5L7 2.5M3 9l1.5 1.5L7 8M3 14.5L4.5 16 7 13.5" strokeWidth="1.5" />
        <path d="M10 5h7M10 10.5h7M10 16h7" strokeWidth="1.6" />
      </>
    ),
  },
  'rail-timeline': {
    box: '0 0 20 20',
    base: STROKE,
    body: (
      <>
        <path d="M5 3v14" strokeWidth="1.5" />
        <circle cx="5" cy="5" r="1.9" strokeWidth="1.5" style={{ fill: 'var(--surface)' }} />
        <circle cx="5" cy="10" r="1.9" strokeWidth="1.5" style={{ fill: 'var(--surface)' }} />
        <circle cx="5" cy="15" r="1.9" strokeWidth="1.5" style={{ fill: 'var(--surface)' }} />
        <path d="M9.5 5H17M9.5 10H15M9.5 15H16" strokeWidth="1.5" />
      </>
    ),
  },
  'rail-blocks': {
    box: '0 0 20 20',
    base: STROKE,
    body: (
      <>
        <rect x="2.5" y="2.5" width="6" height="6" rx="1.5" strokeWidth="1.6" />
        <rect x="11.5" y="2.5" width="6" height="6" rx="1.5" strokeWidth="1.6" />
        <rect x="2.5" y="11.5" width="6" height="6" rx="1.5" strokeWidth="1.6" />
        <rect x="11.5" y="11.5" width="6" height="6" rx="1.5" strokeWidth="1.6" />
      </>
    ),
  },
  code: { box: '0 0 14 14', base: STROKE, body: <path d="M8.5 3L4.5 7l4 4M11.5 3v8" strokeWidth="1.5" /> },
  chip: {
    box: '0 0 18 18',
    base: STROKE,
    body: (
      <>
        <rect x="3.5" y="5" width="11" height="8" rx="2" strokeWidth="1.5" />
        <path d="M6.5 5V2.8M9 5V2.8M11.5 5V2.8M6.5 15.2V13M9 15.2V13M11.5 15.2V13" strokeWidth="1.5" />
      </>
    ),
  },
  gear: {
    box: '0 0 18 18',
    base: STROKE,
    body: (
      <>
        <circle cx="9" cy="9" r="2.6" strokeWidth="1.5" />
        <path d="M9 1.8v2M9 14.2v2M1.8 9h2M14.2 9h2M3.9 3.9l1.4 1.4M12.7 12.7l1.4 1.4M3.9 14.1l1.4-1.4M12.7 5.3l1.4-1.4" strokeWidth="1.5" />
      </>
    ),
  },

  // --- evaluation navigation ----------------------------------------------------
  'nav-overview': {
    box: '0 0 18 18',
    base: STROKE,
    body: (
      <>
        <rect x="2" y="2" width="6" height="6" rx="1.5" strokeWidth="1.5" />
        <rect x="10" y="2" width="6" height="6" rx="1.5" strokeWidth="1.5" />
        <rect x="2" y="10" width="6" height="6" rx="1.5" strokeWidth="1.5" />
        <rect x="10" y="10" width="6" height="6" rx="1.5" strokeWidth="1.5" />
      </>
    ),
  },
  'nav-hack': {
    box: '0 0 18 18',
    base: STROKE,
    body: <path d="M9 1.8L15.5 5.4V12.6L9 16.2L2.5 12.6V5.4Z M2.5 5.4L9 9L15.5 5.4M9 9V16.2" strokeWidth="1.5" />,
  },
  'nav-build': {
    box: '0 0 18 18',
    base: STROKE,
    body: (
      <>
        <circle cx="7" cy="10.5" r="3.5" strokeWidth="1.6" />
        <circle cx="13" cy="5" r="2" strokeWidth="1.5" />
        <circle cx="13" cy="14" r="2" strokeWidth="1.5" />
      </>
    ),
  },
  'nav-workflow': {
    box: '0 0 18 18',
    base: STROKE,
    body: (
      <>
        <circle cx="3.5" cy="9" r="1.8" strokeWidth="1.5" />
        <circle cx="9" cy="9" r="1.8" strokeWidth="1.5" />
        <circle cx="14.5" cy="9" r="1.8" strokeWidth="1.5" />
        <path d="M5.3 9h1.9M10.8 9h1.9" strokeWidth="1.5" />
      </>
    ),
  },
  'nav-report': {
    box: '0 0 18 18',
    base: STROKE,
    body: <path d="M4 2h7l3.5 3.5V16H4z M11 2v3.5h3.5M6.5 9h5M6.5 12h5" strokeWidth="1.5" />,
  },

  // --- status marks (28 box so they scale cleanly) --------------------------------
  'status-done': {
    box: '0 0 28 28',
    base: FILL,
    body: (
      <>
        <circle cx="14" cy="14" r="13" />
        <path d="M8.5 14.3l3.8 3.8 7.2-7.6" strokeWidth="2.2" style={{ stroke: 'var(--base)', fill: 'none' }} strokeLinecap="round" strokeLinejoin="round" />
      </>
    ),
  },
  'status-partial': {
    box: '0 0 28 28',
    base: FILL,
    body: (
      <>
        <circle cx="14" cy="14" r="12" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M14 2a12 12 0 0 1 0 24z" />
      </>
    ),
  },
  'status-pending': {
    box: '0 0 28 28',
    base: FILL,
    body: <circle cx="14" cy="14" r="12" fill="none" stroke="currentColor" strokeWidth="2" />,
  },
  'status-failed': {
    box: '0 0 28 28',
    base: FILL,
    body: (
      <>
        <circle cx="14" cy="14" r="13" />
        <path d="M9.5 9.5l9 9M18.5 9.5l-9 9" strokeWidth="2.2" style={{ stroke: 'var(--base)', fill: 'none' }} strokeLinecap="round" />
      </>
    ),
  },

  // --- Main Menu tiles (92 box) ------------------------------------------------------
  'tile-hack': {
    box: '0 0 92 92',
    base: FILL,
    body: (
      <>
        <path d="M46 8L80 26V66L46 84L12 66V26Z" />
        <path
          d="M12 26L46 44L80 26M46 44V84"
          strokeWidth="3.5"
          strokeLinejoin="round"
          style={{ stroke: 'var(--surface)', fill: 'none' }}
        />
      </>
    ),
  },
  'tile-build': {
    box: '0 0 92 92',
    base: { fill: 'none', stroke: 'currentColor' },
    body: (
      <>
        <circle cx="30" cy="48" r="16" strokeWidth="9" />
        <circle cx="30" cy="48" r="24" strokeWidth="7" strokeDasharray="7.5 7.5" />
        <circle cx="60" cy="68" r="11" strokeWidth="7" />
        <circle cx="60" cy="68" r="17" strokeWidth="6" strokeDasharray="6 6" />
        <circle cx="62" cy="26" r="8" strokeWidth="6" />
        <circle cx="62" cy="26" r="13" strokeWidth="5" strokeDasharray="5 5" />
      </>
    ),
  },
  'tile-progress': {
    box: '0 0 92 92',
    base: FILL,
    body: (
      <>
        <circle cx="46" cy="46" r="40" />
        <path d="M46 6V46L74 74" strokeWidth="4" strokeLinecap="round" style={{ stroke: 'var(--surface)', fill: 'none' }} />
        <path d="M46 46H86" strokeWidth="4" style={{ stroke: 'var(--surface)', fill: 'none' }} />
      </>
    ),
  },
}

export default function Icon({ name, size = 16, className, title }) {
  const def = ICONS[name]
  if (!def) return null
  const [, , vw, vh] = def.box.split(' ').map(Number)
  const longest = Math.max(vw, vh)
  const labelled = Boolean(title)
  return (
    <svg
      className={className ? `icon ${className}` : 'icon'}
      width={(size * vw) / longest}
      height={(size * vh) / longest}
      viewBox={def.box}
      focusable="false"
      role={labelled ? 'img' : undefined}
      aria-hidden={labelled ? undefined : 'true'}
      aria-label={labelled ? title : undefined}
      {...def.base}
    >
      {def.body}
    </svg>
  )
}
