// Splits a file name for display so the project tree can shorten the stem with
// an ellipsis while the extension always stays visible
// ("smart_home_mqtt_c….ino"): in a 236px column, the tail is what tells two
// files apart. CSS cannot ellipsize the middle of a string, so the two halves
// are drawn as separate spans (see `.tree-name` in styles/build.css).

/**
 * @param {string} name a file name such as `smart_home_mqtt_control.ino`
 * @returns {[string, string]} `[stem, extension]`; the extension keeps its dot
 *   and is empty when there is none. A leading dot (`.gitignore`) is part of
 *   the stem, not an extension, and only the last dot splits (`a.b.ino`).
 */
export function splitExtension(name) {
  const dot = name.lastIndexOf('.')
  if (dot <= 0 || dot === name.length - 1) return [name, '']
  return [name.slice(0, dot), name.slice(dot)]
}
