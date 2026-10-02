// Backend error details are terse lowercase phrases ("student number must be
// 1-32 letters, digits or hyphens"); show them as sentences.
export function sentenceCase(text) {
  const value = String(text ?? '').trim()
  return value ? value[0].toUpperCase() + value.slice(1) : value
}
