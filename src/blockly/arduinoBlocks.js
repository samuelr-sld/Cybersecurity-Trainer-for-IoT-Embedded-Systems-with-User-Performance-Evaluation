import * as Blockly from 'blockly/core'

/**
 * Minimal ESP32/Arduino block set — Blockly Phase 1 POC (see CLAUDE.md).
 *
 * Five block types only, matching backend/app/build/blink.py's LED Blink
 * program exactly: `arduino_setup`/`arduino_loop` (the two function
 * containers every sketch needs), and three statement blocks that can be
 * dropped inside them — `pinmode`, `digitalwrite`, `delay`. This is
 * deliberately not the final five-module cybersecurity block library; it
 * exists only to prove BLOCKLY -> GENERATED C++ -> EXISTING COMPILE ->
 * EXISTING FLASH end to end. See `src/blockly/arduinoGenerator.js` for how
 * these become real C++ text.
 *
 * `arduino_setup`/`arduino_loop` are "hat" blocks (no previous/next
 * connection) — they anchor a stack rather than chain into one, the same
 * shape Blockly's own event blocks use, since a sketch has at most one of
 * each. Nothing here enforces "at most one": the generator just concatenates
 * every top-level block in workspace order, so a student who creates two
 * `setup` blocks gets two `void setup() {}` functions and a real compiler
 * error — a truthful outcome, not a blocked action.
 */

// `LED_BUILTIN` is undefined for many generic "ESP32 Dev Module" board
// variants' Arduino core (confirmed with a real compile against this
// project's own board target, esp32:esp32:esp32: "'LED_BUILTIN' was not
// declared in this scope") — unlike an Uno, there is no single on-board LED
// pin every ESP32 dev board agrees on. `2` is what
// backend/app/build/blink.py's own default firmware already uses and is
// confirmed working on real hardware, so it is the block default here too;
// the PIN field stays a free-text field a student can still change to
// `LED_BUILTIN` (or any GPIO number) if their specific board defines it.
const DEFAULT_PIN = '2'

const PIN_MODES = [
  ['OUTPUT', 'OUTPUT'],
  ['INPUT', 'INPUT'],
  ['INPUT_PULLUP', 'INPUT_PULLUP'],
]

const DIGITAL_VALUES = [
  ['HIGH', 'HIGH'],
  ['LOW', 'LOW'],
]

const COMPARISON_OPERATORS = [
  ['=', '=='],
  ['≠', '!='],
]

//: Block types this module defines. The toolbox is no longer built here: it
//: comes from the master block catalog (`./catalog/`), which names these
//: eight types as its IMPLEMENTED blocks — `catalog.test.js` fails if the two
//: lists ever disagree. The first five are the original Blockly POC set; the
//: last three are the no-device/Blockly-integration correction's own
//: dedicated additions — see `backend/app/blockly/definitions/programming.py`
//: for why they exist beside (not instead of) the still-CATALOGED generic
//: `functions.define`/`functions.call`/`logic.if_else`.
export const ARDUINO_BLOCK_TYPES = [
  'arduino_setup',
  'arduino_loop',
  'pinmode',
  'digitalwrite',
  'delay',
  'function_implementation',
  'call_existing_function',
  'if_equals',
]

let registered = false

/**
 * Define the five Arduino block types on the global `Blockly.Blocks`
 * registry. Idempotent — safe to call from every `BlocklyWorkspace` mount
 * (e.g. React Strict Mode's dev-only double-invoke) without redefining or
 * erroring.
 */
export function registerArduinoBlocks() {
  if (registered) return
  registered = true

  Blockly.Blocks['arduino_setup'] = {
    init() {
      this.appendDummyInput().appendField('setup')
      this.appendStatementInput('DO')
      this.setPreviousStatement(false)
      this.setNextStatement(false)
      this.setColour(210)
      this.setTooltip('Runs once when the ESP32 starts — void setup() { ... }')
    },
  }

  Blockly.Blocks['arduino_loop'] = {
    init() {
      this.appendDummyInput().appendField('loop')
      this.appendStatementInput('DO')
      this.setPreviousStatement(false)
      this.setNextStatement(false)
      this.setColour(210)
      this.setTooltip('Runs repeatedly forever — void loop() { ... }')
    },
  }

  Blockly.Blocks['pinmode'] = {
    init() {
      this.appendDummyInput()
        .appendField('set pin')
        .appendField(new Blockly.FieldTextInput(DEFAULT_PIN), 'PIN')
        .appendField('mode')
        .appendField(new Blockly.FieldDropdown(PIN_MODES), 'MODE')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(65)
      this.setTooltip('pinMode(pin, mode);')
    },
  }

  Blockly.Blocks['digitalwrite'] = {
    init() {
      this.appendDummyInput()
        .appendField('set pin')
        .appendField(new Blockly.FieldTextInput(DEFAULT_PIN), 'PIN')
        .appendField('to')
        .appendField(new Blockly.FieldDropdown(DIGITAL_VALUES), 'VALUE')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(65)
      this.setTooltip('digitalWrite(pin, value);')
    },
  }

  Blockly.Blocks['delay'] = {
    init() {
      this.appendDummyInput()
        .appendField('wait')
        .appendField(new Blockly.FieldNumber(1000, 0), 'MS')
        .appendField('ms')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(65)
      this.setTooltip('delay(milliseconds);')
    },
  }

  // --- no-device/Blockly-integration correction -----------------------------
  // The one container a student never drags from the toolbox: it IS the
  // section they clicked, pre-placed on the canvas by BlocklyWorkspace.jsx
  // when it loads a section's `workspace` state (backend/app/build/
  // section_blockly.py). Its C++ signature (name, return type, parameters)
  // is fixed by the firmware and preserved server-side
  // (`SemanticSection.signature`) — this block never shows or edits it, only
  // the body. A "hat" block like arduino_setup/arduino_loop for the same
  // reason: it anchors a stack, it does not chain into one.
  Blockly.Blocks['function_implementation'] = {
    init() {
      this.appendDummyInput().appendField('function body')
      this.appendStatementInput('BODY')
      this.setPreviousStatement(false)
      this.setNextStatement(false)
      this.setColour(290)
      this.setTooltip(
        'The body of this firmware function. Its name, return type and parameters are fixed by the firmware.',
      )
    },
  }

  // Calls an existing function this firmware already defines elsewhere
  // (`motorStart()`, `chirpBuzzer()`, ...) — never a new function, and never
  // with arguments (see backend/app/build/semantic/models.py::CallStatement).
  Blockly.Blocks['call_existing_function'] = {
    init() {
      this.appendDummyInput()
        .appendField('call')
        .appendField(new Blockly.FieldTextInput('functionName'), 'NAME')
        .appendField('()')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(290)
      this.setTooltip('Calls an existing function this firmware defines, with no arguments.')
    },
  }

  // An authorization-style gate: `if (LEFT OPERATOR "RIGHT") { DO }`, no
  // `else` — see backend/app/build/semantic/models.py::ConditionalStatement
  // for why two independent `if_equals` blocks express an `if`/`else if`
  // chain exactly as well as one block with a growing mutator would. LEFT is
  // always read as a named reference in scope (a parameter, e.g. `message`);
  // RIGHT is always a fixed text value to compare it against.
  Blockly.Blocks['if_equals'] = {
    init() {
      this.appendDummyInput()
        .appendField('if')
        .appendField(new Blockly.FieldTextInput('message'), 'LEFT')
        .appendField(new Blockly.FieldDropdown(COMPARISON_OPERATORS), 'OPERATOR')
        .appendField('"')
        .appendField(new Blockly.FieldTextInput(''), 'RIGHT')
        .appendField('"')
      this.appendStatementInput('DO')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(210)
      this.setTooltip(
        'Runs the body only when LEFT (a named value in scope) equals or differs from the fixed text on the right.',
      )
    },
  }
}
