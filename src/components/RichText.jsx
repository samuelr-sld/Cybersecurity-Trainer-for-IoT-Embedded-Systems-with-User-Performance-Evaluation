import { splitInlineCode } from '../hackTerminal/briefingModel'

/**
 * Hint and guide text with `backtick` spans shown as code. Text children only:
 * nothing is parsed as markup, so the backend's wording can never inject HTML,
 * and nothing here can run a command - a span is displayed, never executed.
 */
export default function RichText({ text }) {
  return splitInlineCode(text).map((part, index) =>
    part.code ? <code key={index}>{part.text}</code> : <span key={index}>{part.text}</span>,
  )
}
