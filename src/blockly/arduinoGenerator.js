import * as Blockly from 'blockly/core'

/**
 * The Blockly -> C++ layer for the Phase 1 Blockly POC (see CLAUDE.md).
 *
 * Deliberately its own small module, kept conceptually separate from both
 * the block definitions (`arduinoBlocks.js`) and the existing Build Mode
 * compile pipeline: this file's only job is
 *
 *     Blockly workspace -> arduinoGenerator.workspaceToCode(workspace) -> C++ text
 *
 * What happens to that C++ text next (syncing it into the backend
 * BuildWorkspace, compiling with the real Arduino CLI, flashing) is entirely
 * `src/screens/BuildMode.jsx`'s existing `pendingEdit`/`compile()`/`flash()`
 * machinery — this generator has no knowledge of any of that, and nothing
 * about the existing compile/flash pipeline knows Blockly exists.
 *
 * Built with `Blockly.Generator`, the same base class Blockly's own
 * JavaScript/Python/etc. generators use — not a bespoke code-emission
 * scheme — following Blockly's documented custom-generator pattern
 * (`forBlock` handlers + `scrub_` for statement chaining).
 */

export const arduinoGenerator = new Blockly.Generator('Arduino')

arduinoGenerator.INDENT = '  '

// No value/expression blocks exist yet (every block here is a statement),
// so no ORDER_* precedence table is needed — only `forBlock`+`scrub_`.

arduinoGenerator.forBlock['arduino_setup'] = function (block, generator) {
  const body = generator.statementToCode(block, 'DO')
  return `void setup() {\n${body}}\n\n`
}

arduinoGenerator.forBlock['arduino_loop'] = function (block, generator) {
  const body = generator.statementToCode(block, 'DO')
  return `void loop() {\n${body}}\n`
}

arduinoGenerator.forBlock['pinmode'] = function (block) {
  const pin = block.getFieldValue('PIN')
  const mode = block.getFieldValue('MODE')
  return `pinMode(${pin}, ${mode});\n`
}

arduinoGenerator.forBlock['digitalwrite'] = function (block) {
  const pin = block.getFieldValue('PIN')
  const value = block.getFieldValue('VALUE')
  return `digitalWrite(${pin}, ${value});\n`
}

arduinoGenerator.forBlock['delay'] = function (block) {
  const ms = block.getFieldValue('MS')
  return `delay(${ms});\n`
}

// Standard Blockly generator plumbing: chains a statement block to whatever
// follows it via its `nextConnection`, and applies `statementToCode`'s
// indentation to each line — the same job `Blockly.JavaScript`'s own
// `scrub_` does. Without this, only the first block in an
// setup()/loop()/etc. stack would ever be emitted.
arduinoGenerator.scrub_ = function (block, code, thisOnly) {
  const nextBlock = block.nextConnection && block.nextConnection.targetBlock()
  const nextCode = thisOnly ? '' : arduinoGenerator.blockToCode(nextBlock)
  return code + nextCode
}

/**
 * Generate the current workspace's full C++ source. A workspace with no
 * `arduino_setup`/`arduino_loop` blocks yet produces incomplete/empty text
 * (no functions at all) — that is a truthful reflection of an unfinished
 * program, not something this layer papers over; a real compile against it
 * fails with a real, honest Arduino CLI error.
 */
export function generateArduinoCode(workspace) {
  return arduinoGenerator.workspaceToCode(workspace)
}
