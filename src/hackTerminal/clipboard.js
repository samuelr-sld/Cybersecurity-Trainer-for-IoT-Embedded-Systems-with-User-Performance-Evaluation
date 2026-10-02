// Copy text to the clipboard from a page that may not be a "secure context".
//
// The trainer is served from the Raspberry Pi over plain http on its own access
// point (e.g. http://192.168.50.1:8000). Browsers expose `navigator.clipboard`
// only on https or localhost, so there the modern API is simply absent and the
// legacy `execCommand('copy')` path is the one that works. Resolves true when
// the text was copied, false when neither path could.
export async function copyToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through to the legacy path
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(area)
  }
}
