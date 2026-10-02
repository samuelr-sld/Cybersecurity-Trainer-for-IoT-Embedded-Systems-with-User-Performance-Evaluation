import logoSvg from '../assets/icons/CyberTrainer.svg?raw'

// The CT mark, read from the supplied src/assets/icons/CyberTrainer.svg so that
// file stays the single source of truth. The file is a 1600x1000 canvas holding
// one traced path moved by `transform`; this crops the viewBox to the mark's
// own bounds (the same crop the design screens use) so it scales cleanly.
const LOGO_PATH = /\sd="([^"]+)"/.exec(logoSvg)?.[1] ?? ''
const LOGO_TRANSFORM = /transform="([^"]+)"/.exec(logoSvg)?.[1]
const LOGO_VIEWBOX = '524.5 336 548.5 325.7'
const LOGO_RATIO = 325.7 / 548.5

export default function Logo({ width = 46, className }) {
  return (
    <svg
      className={className ? `logo ${className}` : 'logo'}
      width={width}
      height={Math.round(width * LOGO_RATIO)}
      viewBox={LOGO_VIEWBOX}
      role="img"
      aria-label="CyberTrainer"
    >
      <path d={LOGO_PATH} fill="currentColor" transform={LOGO_TRANSFORM} />
    </svg>
  )
}
