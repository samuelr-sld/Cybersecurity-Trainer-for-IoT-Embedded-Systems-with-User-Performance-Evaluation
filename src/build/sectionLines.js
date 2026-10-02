// Line numbers for the stacked firmware sections in Build Mode's code view.
//
// A firmware file is an ordered sequence of segments whose concatenation is the
// whole source (backend/app/build/models.py `FirmwareFile.render`), so the line
// a section starts on is simply the number of lines in the sections before it.
// Pure and presentation-only: it reads segment text and changes nothing.

/**
 * @param {Array<{text?: string}>} segments
 * @returns {Array<{segment: object, start: number, lines: string[]}>}
 *   `start` is the 1-based number of the segment's first line; `lines` are its
 *   lines without the trailing newline that ends the last one.
 */
export function numberSegments(segments) {
  let next = 1
  return segments.map((segment) => {
    const text = segment.text ?? ''
    const body = text.endsWith('\n') ? text.slice(0, -1) : text
    const lines = text === '' ? [] : body.split('\n')
    const start = next
    next += lines.length
    return { segment, start, lines }
  })
}
