// Bundled by tests/test_real_blockly.py. Loads the REAL src/blockly/arduinoBlocks.js
// block definitions into Blockly's own headless Workspace, so a workspace state
// the backend produces is checked against the definitions a student's browser uses.
import * as Blockly from '__BLOCKLY__'
import { registerArduinoBlocks } from '__BLOCKS__'

registerArduinoBlocks()

globalThis.roundTrip = (state) => {
  const workspace = new Blockly.Workspace()
  try {
    Blockly.serialization.workspaces.load(state, workspace)
    return Blockly.serialization.workspaces.save(workspace)
  } finally {
    workspace.dispose()
  }
}

// Loads a state, then EDITS it through Blockly's own block API - the calls a
// drag-and-drop in the browser ends in (newBlock, setFieldValue, connect) - and
// saves it with the real serializer. `ops` is plain data, one step each:
//
//   {op: 'new', ref, type, fields}            create a block from the toolbox
//   {op: 'value', parent, input, child}       plug a value block into a socket
//   {op: 'statement', parent, input, child}   drop a stack into a statement input
//   {op: 'next', parent, child}               stack a block under another
//   {op: 'unplug', block}                     lift ONE block out of its stack,
//                                             healing the stack around it
//   {op: 'rename', root, type, field, from, to}
//                                             retype a field on every matching
//                                             block under `root` (inclusive)
//
// A block is named by the `ref` it was created with, or `id:<block id>` for one
// the loaded state already held. Every step is checked: a connection Blockly's
// own connection checker refuses, or a field value a dropdown rejects, throws
// instead of being silently dropped.
globalThis.editThenSave = (state, ops) => {
  const workspace = new Blockly.Workspace()
  try {
    Blockly.serialization.workspaces.load(state, workspace)
    const refs = {}
    const get = (ref) => {
      const block = ref.startsWith('id:') ? workspace.getBlockById(ref.slice(3)) : refs[ref]
      if (!block) throw new Error(`no block ${ref}`)
      return block
    }
    const input = (block, name) => {
      const found = block.getInput(name)
      if (!found || !found.connection) throw new Error(`${block.type} has no input ${name}`)
      return found.connection
    }
    const attached = (parent, child, what) => {
      if (child.getParent() !== parent) throw new Error(`Blockly refused ${what}`)
    }
    const setField = (block, name, value) => {
      block.setFieldValue(value, name)
      if (block.getFieldValue(name) !== value) {
        throw new Error(`${block.type}.${name} refused ${JSON.stringify(value)}`)
      }
    }
    for (const op of ops) {
      if (op.op === 'new') {
        const block = workspace.newBlock(op.type)
        for (const [name, value] of Object.entries(op.fields || {})) setField(block, name, value)
        refs[op.ref] = block
      } else if (op.op === 'value') {
        const parent = get(op.parent)
        const child = get(op.child)
        input(parent, op.input).connect(child.outputConnection)
        attached(parent, child, `${child.type} in ${parent.type}.${op.input}`)
      } else if (op.op === 'statement') {
        const parent = get(op.parent)
        const child = get(op.child)
        input(parent, op.input).connect(child.previousConnection)
        attached(parent, child, `${child.type} in ${parent.type}.${op.input}`)
      } else if (op.op === 'next') {
        const parent = get(op.parent)
        const child = get(op.child)
        parent.nextConnection.connect(child.previousConnection)
        attached(parent, child, `${child.type} under ${parent.type}`)
      } else if (op.op === 'unplug') {
        get(op.block).unplug(true)
      } else if (op.op === 'rename') {
        const matches = get(op.root)
          .getDescendants(false)
          .filter((block) => block.type === op.type && block.getFieldValue(op.field) === op.from)
        if (!matches.length) throw new Error(`no ${op.type} with ${op.field}=${op.from}`)
        for (const block of matches) setField(block, op.field, op.to)
      } else {
        throw new Error(`unknown op ${op.op}`)
      }
    }
    return Blockly.serialization.workspaces.save(workspace)
  } finally {
    workspace.dispose()
  }
}

// Loads a state and reports what each block with an id DRAWS: its inputs, in
// order, with every field's displayed value and whether that field is
// editable or serialized. Used for the read-only signature header (P4.2).
globalThis.inspectBlocks = (state) => {
  const workspace = new Blockly.Workspace()
  try {
    Blockly.serialization.workspaces.load(state, workspace)
    const out = {}
    for (const block of workspace.getAllBlocks(false)) {
      out[block.id] = {
        type: block.type,
        inputs: block.inputList.map((input) => ({
          name: input.name,
          fields: input.fieldRow.map((field) => ({
            value: String(field.getValue() ?? field.getText()),
            editable: field.EDITABLE,
            serializable: field.SERIALIZABLE,
          })),
        })),
      }
    }
    return out
  } finally {
    workspace.dispose()
  }
}

// Reports how a block behaves when a student tries to edit it (used for the
// read-only preserved_source block).
globalThis.blockFlags = (type) => {
  const workspace = new Blockly.Workspace()
  try {
    const block = workspace.newBlock(type)
    return {
      editable: block.isEditable(),
      movable: block.isMovable(),
      deletable: block.isDeletable(),
    }
  } finally {
    workspace.dispose()
  }
}
