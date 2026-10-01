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

// The TYPE dropdown of `variables_declare`: the visible label is the C++
// type a student recognises, the stored value is the backend's language-
// neutral type token (backend/app/build/blockly_bridge/structural.py).
const DECLARATION_TYPES = [
  ['String', 'text'],
  ['int', 'number'],
  ['bool', 'boolean'],
  ['unsigned long', 'unsigned_long'],
  ['unsigned int', 'unsigned_int'],
  ['long', 'long'],
  ['float', 'float'],
  ['double', 'double'],
  ['byte', 'byte'],
  ['uint8_t', 'uint8_t'],
  ['uint16_t', 'uint16_t'],
  ['uint32_t', 'uint32_t'],
]

// The QUALIFIER dropdown of `variables_declare`: what precedes the type in C++.
const DECLARATION_QUALIFIERS = [
  ['(no qualifier)', 'none'],
  ['static', 'static'],
  ['const', 'const'],
  ['static const', 'static_const'],
]

// The operator of `variables_set`. Kept as chosen: `+=` is never `=`.
const ASSIGNMENT_OPERATORS = [
  ['=', '='],
  ['+=', '+='],
  ['-=', '-='],
]

// The operator of `variables_update`.
const UPDATE_OPERATORS = [
  ['++', '++'],
  ['--', '--'],
]

// How many argument sockets a call block has (ARG0..ARG3). Restated from
// backend/app/build/blockly_bridge/structural.py::CALL_ARGUMENT_INPUTS, which a
// backend test pins against the catalog; a call with more arguments is shown as
// read-only C++ instead.
const CALL_ARGUMENT_COUNT = 4

// Appends the ARG0..ARGn value sockets and the closing bracket to a call block.
function appendCallArguments(block) {
  for (let index = 0; index < CALL_ARGUMENT_COUNT; index += 1) {
    const input = block.appendValueInput(`ARG${index}`)
    if (index > 0) input.appendField(',')
  }
  block.appendDummyInput().appendField(')')
  block.setInputsInline(true)
}

//: Block types this module defines. The toolbox is no longer built here: it
//: comes from the master block catalog (`./catalog/`), which names these
//: types as its IMPLEMENTED blocks — `catalog.test.js` fails if the two
//: lists ever disagree. The first five are the original Blockly POC set; the
//: next three are the no-device/Blockly-integration correction's own
//: dedicated additions — see `backend/app/blockly/definitions/programming.py`
//: for why they exist beside (not instead of) the still-CATALOGED generic
//: `functions.define`/`functions.call`/`logic.if_else`. The rest are the
//: catalog's GENERIC value/statement blocks (variables, text, math,
//: comparisons, `if`, `return`) — small composable pieces, none of them
//: specific to any panel.
export const ARDUINO_BLOCK_TYPES = [
  'arduino_setup',
  'arduino_loop',
  'pinmode',
  'digitalwrite',
  'delay',
  'function_implementation',
  'call_existing_function',
  'if_equals',
  'variables_declare',
  'variables_set',
  'call_method',
  'call_function_value',
  'logic_true',
  'logic_false',
  'variables_get',
  'text_literal',
  'text_index_of',
  'text_substring',
  'text_length',
  'math_number',
  'math_add',
  'logic_equal',
  'logic_not_equal',
  'logic_less_equal',
  'logic_less',
  'logic_greater',
  'logic_greater_equal',
  'logic_and',
  'logic_or',
  'logic_not',
  'logic_ternary',
  'for_loop',
  'variables_update',
  'logic_if',
  'return_void',
]

// A two-operand value block: `A <symbol> B`, both sockets value inputs.
function binaryValueBlock(symbol, output, colour, tooltip) {
  return {
    init() {
      this.appendValueInput('A')
      this.appendValueInput('B').appendField(symbol)
      this.setInputsInline(true)
      this.setOutput(true, output)
      this.setColour(colour)
      this.setTooltip(tooltip)
    },
  }
}

// The parameters a signature description lists, keeping only well-formed
// entries — the description is display data, so anything unexpected is
// simply not drawn rather than trusted.
function signatureParameters(signature) {
  if (!signature || !Array.isArray(signature.parameters)) return []
  return signature.parameters.filter(
    (p) => p && typeof p.name === 'string' && p.name && typeof p.type === 'string' && p.type,
  )
}

// Redraws function_implementation's read-only header from its signature
// description: the declarator exactly as written, then one row per
// parameter (`parameter  message : const String &`) so a student sees the
// names a variable block can read. Plain labels only — no field here is
// editable or serialized; the description itself round-trips through
// save/loadExtraState. With no description: the original 'function body'.
function renderSignatureHeader(block) {
  for (const input of [...block.inputList]) {
    if (input.name === 'HEADER' || input.name === 'PARAMS' || input.name.startsWith('PARAM_')) {
      block.removeInput(input.name)
    }
  }
  const signature = block.signatureState_
  const rows = []
  if (!signature) {
    rows.push(block.appendDummyInput('HEADER').appendField('function body'))
  } else {
    rows.push(block.appendDummyInput('HEADER').appendField('function').appendField(signature.text))
    if (Array.isArray(signature.parameters)) {
      const parameters = signatureParameters(signature)
      if (parameters.length === 0) {
        rows.push(block.appendDummyInput('PARAMS').appendField('no parameters'))
      }
      parameters.forEach((parameter, index) => {
        rows.push(
          block
            .appendDummyInput(`PARAM_${index}`)
            .appendField('parameter')
            .appendField(parameter.name)
            .appendField(`: ${parameter.type}`),
        )
      })
    }
  }
  for (const row of rows) block.moveInputBefore(row.name, 'BODY')
}

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
        .appendField(new Blockly.FieldTextInput('1000'), 'MS')
        .appendField('ms')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(65)
      this.setTooltip('delay(milliseconds); - a number, or the name of a constant such as BUZZER_CHIRP_MS.')
    },
  }

  // --- no-device/Blockly-integration correction -----------------------------
  // The one container a student never drags from the toolbox: it IS the
  // section they clicked, pre-placed on the canvas by BlocklyWorkspace.jsx
  // when it loads a section's `workspace` state (backend/app/build/
  // section_blockly.py). Its C++ signature (name, return type, parameters)
  // is fixed by the firmware and preserved server-side
  // (`SemanticSection.signature`), which is what the generated C++ always
  // uses. The block SHOWS it as a read-only header (P4.2): the backend sends a
  // description in `extraState` (backend/app/build/blockly_bridge/
  // signature_display.py), kept through save/load by save/loadExtraState and
  // drawn with plain labels — nothing here can edit it, and the backend never
  // reads it back. With no description the header is the plain 'function
  // body' label it always was. A "hat" block like arduino_setup/arduino_loop
  // for the same reason: it anchors a stack, it does not chain into one.
  Blockly.Blocks['function_implementation'] = {
    init() {
      this.signatureState_ = null
      this.appendDummyInput('HEADER').appendField('function body')
      this.appendStatementInput('BODY')
      this.setPreviousStatement(false)
      this.setNextStatement(false)
      this.setColour(290)
      this.setTooltip(() => {
        const names = signatureParameters(this.signatureState_).map((p) => p.name)
        return (
          'The body of this firmware function. Its name, return type and parameters are fixed by the firmware' +
          ' and cannot be changed here.' +
          (names.length ? ` Read a parameter (${names.join(', ')}) with a variable block.` : '')
        )
      })
    },
    saveExtraState() {
      return this.signatureState_ ? { signature: this.signatureState_ } : null
    },
    loadExtraState(state) {
      const signature = state && state.signature
      this.signatureState_ =
        signature && typeof signature === 'object' && typeof signature.text === 'string' ? signature : null
      renderSignatureHeader(this)
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
        .appendField('(')
      appendCallArguments(this)
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(290)
      this.setTooltip('Calls a function this firmware defines, passing the arguments in the sockets, in order.')
    },
  }

  // A call on an OBJECT: `message.trim();`, `client.publish(A, B);`. The
  // receiver is part of the meaning, so it is its own field.
  Blockly.Blocks['call_method'] = {
    init() {
      this.appendDummyInput()
        .appendField('call method')
        .appendField(new Blockly.FieldTextInput('object'), 'RECEIVER')
        .appendField('.')
        .appendField(new Blockly.FieldTextInput('method'), 'METHOD')
        .appendField('(')
      appendCallArguments(this)
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(290)
      this.setTooltip('Calls a method on an object, passing the arguments in the sockets, in order.')
    },
  }

  // A call used as a value: `millis()`.
  Blockly.Blocks['call_function_value'] = {
    init() {
      this.appendDummyInput()
        .appendField('call')
        .appendField(new Blockly.FieldTextInput('functionName'), 'NAME')
        .appendField('(')
      appendCallArguments(this)
      this.setOutput(true, null)
      this.setColour(290)
      this.setTooltip('Calls a function and uses the value it returns.')
    },
  }

  // Assignment: `x = v;` and `x += v;`. The operator is kept as chosen.
  Blockly.Blocks['variables_set'] = {
    init() {
      this.appendValueInput('VALUE')
        .appendField('set')
        .appendField(new Blockly.FieldTextInput('name'), 'NAME')
        .appendField(new Blockly.FieldDropdown(ASSIGNMENT_OPERATORS), 'OPERATOR')
      this.setInputsInline(true)
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(330)
      this.setTooltip('Stores a value in an existing variable, or adds to / subtracts from it.')
    },
  }

  Blockly.Blocks['logic_true'] = {
    init() {
      this.appendDummyInput().appendField('true')
      this.setOutput(true, null)
      this.setColour(210)
      this.setTooltip('The boolean value true.')
    },
  }

  Blockly.Blocks['logic_false'] = {
    init() {
      this.appendDummyInput().appendField('false')
      this.setOutput(true, null)
      this.setColour(210)
      this.setTooltip('The boolean value false.')
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

  // --- generic value/statement blocks ---------------------------------------
  // Each maps to one semantic construct server-side (see backend/app/build/
  // blockly_bridge/structural.py and the catalog's `text.*` operations). Value
  // blocks plug into value sockets; nothing here emits C++ the backend uses —
  // the workspace JSON is sent to the backend, which generates the firmware.

  Blockly.Blocks['variables_declare'] = {
    init() {
      this.appendDummyInput()
        .appendField('declare')
        .appendField(new Blockly.FieldDropdown(DECLARATION_QUALIFIERS), 'QUALIFIER')
        .appendField(new Blockly.FieldDropdown(DECLARATION_TYPES), 'TYPE')
        .appendField(new Blockly.FieldTextInput('name'), 'NAME')
      // Optional: an empty socket declares the variable without a value.
      this.appendValueInput('INITIAL').appendField('=')
      this.setInputsInline(true)
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(330)
      this.setTooltip('Declares a variable of a type, optionally static or const, with an optional initial value.')
    },
  }

  Blockly.Blocks['variables_get'] = {
    init() {
      this.appendDummyInput().appendField(new Blockly.FieldTextInput('name'), 'NAME')
      this.setOutput(true, null)
      this.setColour(330)
      this.setTooltip('Reads a variable, parameter or constant by name.')
    },
  }

  Blockly.Blocks['text_literal'] = {
    init() {
      this.appendDummyInput()
        .appendField('"')
        .appendField(new Blockly.FieldTextInput('text'), 'VALUE')
        .appendField('"')
      this.setOutput(true, 'String')
      this.setColour(160)
      this.setTooltip('A fixed piece of text.')
    },
  }

  Blockly.Blocks['text_index_of'] = {
    init() {
      this.appendValueInput('SEARCH').appendField('position of')
      this.appendValueInput('TEXT').appendField('in')
      this.setInputsInline(true)
      this.setOutput(true, 'Number')
      this.setColour(160)
      this.setTooltip('Where SEARCH first appears in TEXT, counting from 0 — or -1 if it does not.')
    },
  }

  Blockly.Blocks['text_substring'] = {
    init() {
      this.appendValueInput('TEXT').appendField('text of')
      this.appendValueInput('FROM').appendField('from')
      this.appendValueInput('TO').appendField('to')
      this.setInputsInline(true)
      this.setOutput(true, 'String')
      this.setColour(160)
      this.setTooltip('The part of TEXT from position FROM up to (not including) TO.')
    },
  }

  Blockly.Blocks['text_length'] = {
    init() {
      this.appendValueInput('TEXT').appendField('length of')
      this.setInputsInline(true)
      this.setOutput(true, 'Number')
      this.setColour(160)
      this.setTooltip('The number of characters in TEXT.')
    },
  }

  Blockly.Blocks['math_number'] = {
    init() {
      this.appendDummyInput().appendField(new Blockly.FieldNumber(0), 'VALUE')
      this.setOutput(true, 'Number')
      this.setColour(230)
      this.setTooltip('A number.')
    },
  }

  Blockly.Blocks['math_add'] = binaryValueBlock('+', 'Number', 230, 'A plus B.')
  Blockly.Blocks['logic_equal'] = binaryValueBlock('=', 'Boolean', 30, 'True when A equals B.')
  Blockly.Blocks['logic_not_equal'] = binaryValueBlock('≠', 'Boolean', 30, 'True when A differs from B.')
  Blockly.Blocks['logic_less_equal'] = binaryValueBlock(
    '≤',
    'Boolean',
    30,
    'True when A is less than or equal to B.',
  )

  Blockly.Blocks['logic_greater'] = binaryValueBlock('>', 'Boolean', 30, 'True when A is greater than B.')
  Blockly.Blocks['logic_less'] = binaryValueBlock('<', 'Boolean', 30, 'True when A is less than B.')
  Blockly.Blocks['logic_greater_equal'] = binaryValueBlock(
    '≥',
    'Boolean',
    30,
    'True when A is greater than or equal to B.',
  )

  // Logical connectives: both operands are booleans. `&&` binds tighter than
  // `||`; the backend writes parentheses from the tree, so nesting one block
  // inside another is always unambiguous in the generated C++.
  Blockly.Blocks['logic_and'] = {
    init() {
      this.appendValueInput('A').setCheck('Boolean')
      this.appendValueInput('B').setCheck('Boolean').appendField('and')
      this.setInputsInline(true)
      this.setOutput(true, 'Boolean')
      this.setColour(30)
      this.setTooltip('True when both A and B are true.')
    },
  }

  Blockly.Blocks['logic_or'] = {
    init() {
      this.appendValueInput('A').setCheck('Boolean')
      this.appendValueInput('B').setCheck('Boolean').appendField('or')
      this.setInputsInline(true)
      this.setOutput(true, 'Boolean')
      this.setColour(30)
      this.setTooltip('True when A or B (or both) is true.')
    },
  }

  Blockly.Blocks['logic_not'] = {
    init() {
      this.appendValueInput('VALUE').setCheck('Boolean').appendField('not')
      this.setInputsInline(true)
      this.setOutput(true, 'Boolean')
      this.setColour(30)
      this.setTooltip('True when the value is false, and false when it is true.')
    },
  }

  // `condition ? a : b`.
  Blockly.Blocks['logic_ternary'] = {
    init() {
      this.appendValueInput('CONDITION').setCheck('Boolean').appendField('if')
      this.appendValueInput('THEN').appendField('then')
      this.appendValueInput('ELSE').appendField('else')
      this.setInputsInline(true)
      this.setOutput(true, null)
      this.setColour(30)
      this.setTooltip('Gives the THEN value when the condition is true, otherwise the ELSE value.')
    },
  }

  // if / else if / else in ONE block. DO is the body; ELSE_IF holds a single
  // nested `if` block (the next link of the chain) and ELSE the final body. A
  // chain continues with else-if OR else, never both - the backend refuses a
  // block that fills both.
  Blockly.Blocks['logic_if'] = {
    init() {
      this.appendValueInput('CONDITION').setCheck('Boolean').appendField('if')
      this.appendStatementInput('DO')
      this.appendStatementInput('ELSE_IF').appendField('else if (one if block)')
      this.appendStatementInput('ELSE').appendField('else')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(30)
      this.setTooltip(
        'Runs the body when the condition is true. Optionally continues with ONE else-if (an if block) or an else body.',
      )
    },
  }

  // A C-style for loop. INIT and STEP each hold ONE statement block (a
  // declaration or assignment; an assignment or ++/--); either may be empty.
  Blockly.Blocks['for_loop'] = {
    init() {
      this.appendStatementInput('INIT').appendField('for  start')
      this.appendValueInput('CONDITION').setCheck('Boolean').appendField('while')
      this.appendStatementInput('STEP').appendField('then step')
      this.appendStatementInput('DO').appendField('do')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(120)
      this.setTooltip(
        'for (start; while; step) { do } - runs the start once, then repeats the body and the step while the condition holds.',
      )
    },
  }

  // `x++;` / `x--;`.
  Blockly.Blocks['variables_update'] = {
    init() {
      this.appendDummyInput()
        .appendField('step')
        .appendField(new Blockly.FieldTextInput('name'), 'NAME')
        .appendField(new Blockly.FieldDropdown(UPDATE_OPERATORS), 'OPERATOR')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(330)
      this.setTooltip('Adds one to (++) or subtracts one from (--) a variable.')
    },
  }

  // Leaves the function immediately. No next connection: nothing after a
  // `return;` in the same stack could ever run.
  Blockly.Blocks['return_void'] = {
    init() {
      this.appendDummyInput().appendField('return')
      this.setPreviousStatement(true, null)
      this.setNextStatement(false)
      this.setColour(290)
      this.setTooltip('Stops this function here and returns — nothing below it runs.')
    },
  }

  // DISPLAY-ONLY. Draws one fragment of firmware C++ the semantic layer does
  // not understand yet (backend/app/build/blockly_bridge/models.py::
  // PRESERVED_BLOCK_TYPE), in the position it holds in the function body, so a
  // section is never shown as an empty container. Deliberately NOT a catalog
  // block, not in the toolbox and not in ARDUINO_BLOCK_TYPES: it has no
  // semantic operation and no generator. When a workspace comes back the
  // backend reads only its id and its position in the chain (so the fragment
  // stays where this block now sits), never its TEXT — the fragment's real
  // copy is the `preserved` list beside the workspace. Read-only, unmovable and undeletable so it cannot
  // be mistaken for, or edited like, a real block.
  Blockly.Blocks['preserved_source'] = {
    init() {
      // A serializable label: the field VALUE is the exact source (newlines
      // and all) and only the on-canvas rendering collapses whitespace, so
      // what serializes back is never the shortened display text.
      const text = new Blockly.FieldLabelSerializable('')
      text.maxDisplayLength = 160
      this.appendDummyInput().appendField('C++ (read-only)')
      this.appendDummyInput().appendField(text, 'TEXT')
      this.setPreviousStatement(true, null)
      this.setNextStatement(true, null)
      this.setColour(0)
      this.setEditable(false)
      this.setMovable(false)
      this.setDeletable(false)
      this.setTooltip(() => `C++ statement — not yet editable as blocks. Kept exactly as written:
${this.getFieldValue('TEXT')}`)
    },
  }
}
