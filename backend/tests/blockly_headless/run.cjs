// usage: node run.cjs <jsdom dir> <bundle.js> <cases.json> <out.json>
// cases.json: {"states": {name: workspaceState}, "flags": [blockType],
//              "edits": {name: {"state": workspaceState, "ops": [...]}},
//              "inspect": {name: workspaceState}}  (see entry.mjs)
const fs = require('fs')
const { JSDOM } = require(process.argv[2])
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  runScripts: 'outside-only',
  pretendToBeVisual: true,
})
dom.window.eval(fs.readFileSync(process.argv[3], 'utf8'))
const cases = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'))
const out = { saved: {}, errors: {}, flags: {} }
for (const [name, state] of Object.entries(cases.states || {})) {
  try {
    out.saved[name] = dom.window.roundTrip(state)
  } catch (error) {
    out.errors[name] = String((error && error.message) || error)
  }
}
for (const [name, edit] of Object.entries(cases.edits || {})) {
  try {
    out.saved[name] = dom.window.editThenSave(edit.state, edit.ops)
  } catch (error) {
    out.errors[name] = String((error && error.message) || error)
  }
}
out.inspected = {}
for (const [name, state] of Object.entries(cases.inspect || {})) {
  try {
    out.inspected[name] = dom.window.inspectBlocks(state)
  } catch (error) {
    out.errors[name] = String((error && error.message) || error)
  }
}
for (const type of cases.flags || []) {
  out.flags[type] = dom.window.blockFlags(type)
}
fs.writeFileSync(process.argv[5], JSON.stringify(out))
