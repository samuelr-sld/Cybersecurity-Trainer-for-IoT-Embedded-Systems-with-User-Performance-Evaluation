import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import {
  CHAIN_INTENT,
  CHAIN_NOTICE,
  CHAIN_PHASE,
  createChainDriver,
  transmitVia,
} from './compileChain.js'

// These drive the SAME driver and `transmitVia` Build Mode uses
// (src/screens/BuildMode.jsx), with a fake socket in place of
// src/hooks/useBuildSocket.js. Every frame the chain "sends" is recorded, so
// each test asserts exactly what would have gone over the wire, in order.

const SECURITY = 'helper_applyCommand'
const OPAQUE_STOP = {
  index: 1,
  text: 'else if (message == "STOP") { motorStop(); }',
  reason: 'unsupported_statement',
  understoodByTheIr: false,
}
const workspace = (label) => ({ blocks: { languageVersion: 0, blocks: [{ type: 'fn', id: label }] } })

function harness({ connected = true } = {}) {
  const frames = []
  const socket = { open: connected }
  const record = (frame) => {
    if (!socket.open) return false
    frames.push(frame)
    return true
  }
  const senders = {
    sendEditSectionBlocks: (path, sectionId, ws, preserved) =>
      record({ type: 'edit_section_blocks', path, section_id: sectionId, workspace: ws, preserved }),
    sendEditRegion: (path, regionId, source) => record({ type: 'edit_region', path, region_id: regionId, source }),
    sendCompile: () => record({ type: 'compile' }),
    sendFlash: () => record({ type: 'flash' }),
  }
  const driver = createChainDriver()
  driver.attach((send, draft) => transmitVia(senders, send, draft))
  // What `section_blockly` answered for Panel 1's security region: the
  // drawable blocks, plus the old unauthenticated branch as opaque source.
  driver.loadBaseline({
    kind: 'blocks',
    path: 'panel1.ino',
    sectionId: SECURITY,
    workspace: workspace('baseline'),
    preserved: [OPAQUE_STOP],
  })
  return { driver, frames, socket, types: () => frames.map((frame) => frame.type) }
}

// --- 1. Compile submits the CURRENT workspace ------------------------------

test('compile submits the current workspace, not an earlier one', () => {
  const { driver, frames } = harness()
  driver.edit({ workspace: workspace('first') })
  driver.edit({ workspace: workspace('latest') })
  driver.request(CHAIN_INTENT.COMPILE, false)

  assert.equal(frames.length, 1)
  assert.equal(frames[0].type, 'edit_section_blocks')
  assert.deepEqual(frames[0].workspace, workspace('latest'))
  assert.equal(frames[0].section_id, SECURITY)
  assert.equal(driver.chain.phase, CHAIN_PHASE.SUBMITTING)
})

test('compile sends the edit first and compile only after the backend acknowledges it', () => {
  const { driver, types } = harness()
  driver.edit({ workspace: workspace('a') })
  driver.request(CHAIN_INTENT.COMPILE, false)
  assert.deepEqual(types(), ['edit_section_blocks'])

  driver.editAcknowledged()
  assert.deepEqual(types(), ['edit_section_blocks', 'compile'])
  assert.equal(driver.draftVersion, null, 'the acknowledged draft is now the backend state')

  driver.compileFinished(true)
  assert.equal(driver.chain.phase, CHAIN_PHASE.IDLE)
})

test('with no local edit, compile goes straight to compile', () => {
  const { driver, types } = harness()
  driver.request(CHAIN_INTENT.COMPILE, false)
  assert.deepEqual(types(), ['compile'])
})

// --- 2. A failed submission never clears newer edits ------------------------

test('a rejected submission ends the chain and keeps the draft dirty', () => {
  const { driver, types } = harness()
  driver.edit({ workspace: workspace('incomplete') })
  driver.request(CHAIN_INTENT.COMPILE, false)
  const outcome = driver.error()

  assert.equal(outcome.notice, CHAIN_NOTICE.EDIT_REJECTED)
  assert.deepEqual(types(), ['edit_section_blocks'], 'no compile after a rejected edit')
  assert.equal(driver.chain.phase, CHAIN_PHASE.IDLE)
  assert.notEqual(driver.draftVersion, null)
  assert.deepEqual(driver.draft.workspace, workspace('incomplete'))
})

test('an edit made while the submission is in flight stays dirty after the acknowledgement', () => {
  const { driver, frames } = harness()
  driver.edit({ workspace: workspace('submitted') })
  driver.request(CHAIN_INTENT.COMPILE, false)
  driver.edit({ workspace: workspace('newer') })
  driver.editAcknowledged()

  assert.notEqual(driver.draftVersion, null, 'the newer edit is not marked as submitted')
  assert.deepEqual(driver.draft.workspace, workspace('newer'))
  // The compile builds what the backend accepted; the newer edit waits for
  // the next COMPILE rather than being silently dropped.
  assert.deepEqual(frames[0].workspace, workspace('submitted'))
  assert.equal(frames[1].type, 'compile')
})

test('an edit made while the submission is in flight survives a rejection', () => {
  const { driver } = harness()
  driver.edit({ workspace: workspace('submitted') })
  driver.request(CHAIN_INTENT.COMPILE, false)
  driver.edit({ workspace: workspace('newer') })
  driver.error()
  assert.notEqual(driver.draftVersion, null)
  assert.deepEqual(driver.draft.workspace, workspace('newer'))
})

// --- 3. The corrected workspace is what the retry submits -------------------

test('after an ownership rejection, the retry submits the corrected current workspace', () => {
  const { driver, frames, types } = harness()

  // Incomplete: new blocks, but the opaque old branch still rides along.
  driver.edit({ workspace: workspace('authenticated-start-only') })
  driver.request(CHAIN_INTENT.COMPILE, false)
  assert.deepEqual(frames[0].preserved, [OPAQUE_STOP])
  driver.error()

  // The student fixes the SAME workspace: adds the missing STOP branch as
  // blocks and CLEARs the opaque fragment themselves.
  driver.edit({ workspace: workspace('authenticated-start-and-stop') })
  driver.edit({ preserved: [] })
  driver.request(CHAIN_INTENT.COMPILE, false)

  assert.equal(frames[1].type, 'edit_section_blocks')
  assert.deepEqual(frames[1].workspace, workspace('authenticated-start-and-stop'))
  assert.deepEqual(frames[1].preserved, [])

  driver.editAcknowledged()
  driver.compileFinished(true)
  assert.deepEqual(types(), ['edit_section_blocks', 'edit_section_blocks', 'compile'])
  assert.equal(driver.draftVersion, null)
})

test('the chain never clears preserved fragments on its own', () => {
  const { driver, frames } = harness()
  driver.edit({ workspace: workspace('x') })
  driver.request(CHAIN_INTENT.COMPILE, false)
  driver.error()
  driver.request(CHAIN_INTENT.COMPILE, false)
  // Retried unchanged: the opaque fragment is still submitted, so the
  // backend's ownership rule sees exactly what the student left.
  assert.deepEqual(frames[1].preserved, [OPAQUE_STOP])
})

test('a CLEAR alone is an edit that compile submits', () => {
  const { driver, frames } = harness()
  driver.edit({ preserved: [] })
  driver.request(CHAIN_INTENT.COMPILE, false)
  assert.equal(frames[0].type, 'edit_section_blocks')
  assert.deepEqual(frames[0].workspace, workspace('baseline'))
  assert.deepEqual(frames[0].preserved, [])
})

test('a legacy text edit goes through the same chain as edit_region', () => {
  const { driver, frames } = harness()
  driver.loadBaseline({ kind: 'text', path: 'panel1.ino', sectionId: 'globals', source: 'int a;' })
  driver.edit({ source: 'int b;' })
  driver.request(CHAIN_INTENT.COMPILE, false)
  assert.deepEqual(frames[0], { type: 'edit_region', path: 'panel1.ino', region_id: 'globals', source: 'int b;' })
})

// --- 4 / 5. Flash compiles first when needed, in order ----------------------

test('flash with an unsubmitted edit runs edit -> compile -> flash, in that order', () => {
  const { driver, types } = harness()
  driver.edit({ workspace: workspace('remediated') })
  driver.request(CHAIN_INTENT.FLASH, true) // flash_ready from an OLDER build must not matter
  assert.deepEqual(types(), ['edit_section_blocks'])
  driver.editAcknowledged()
  assert.deepEqual(types(), ['edit_section_blocks', 'compile'])
  assert.equal(driver.chain.phase, CHAIN_PHASE.COMPILING)
  driver.compileFinished(true)
  assert.deepEqual(types(), ['edit_section_blocks', 'compile', 'flash'])
  assert.equal(driver.chain.phase, CHAIN_PHASE.FLASHING)
  driver.flashFinished()
  assert.equal(driver.chain.phase, CHAIN_PHASE.IDLE)
})

test('flash with a clean editor but a stale backend build compiles first', () => {
  const { driver, types } = harness()
  driver.request(CHAIN_INTENT.FLASH, false)
  assert.deepEqual(types(), ['compile'])
  driver.compileFinished(true)
  assert.deepEqual(types(), ['compile', 'flash'])
})

test('flash with a clean editor and a synchronized build flashes directly', () => {
  const { driver, types } = harness()
  driver.request(CHAIN_INTENT.FLASH, true)
  assert.deepEqual(types(), ['flash'])
})

// --- 6. Never flash after a failed compile or a rejected edit ---------------

test('a failed automatic compile never sends flash', () => {
  const { driver, types } = harness()
  driver.edit({ workspace: workspace('broken') })
  driver.request(CHAIN_INTENT.FLASH, true)
  driver.editAcknowledged()
  const outcome = driver.compileFinished(false)
  assert.equal(outcome.notice, CHAIN_NOTICE.COMPILE_FAILED_NO_FLASH)
  assert.deepEqual(types(), ['edit_section_blocks', 'compile'])
  assert.equal(driver.chain.phase, CHAIN_PHASE.IDLE)
})

test('a rejected edit during flash never compiles or flashes', () => {
  const { driver, types } = harness()
  driver.edit({ workspace: workspace('incomplete') })
  driver.request(CHAIN_INTENT.FLASH, true)
  driver.error()
  assert.deepEqual(types(), ['edit_section_blocks'])
})

test('a refused compile during flash never flashes', () => {
  const { driver, types } = harness()
  driver.request(CHAIN_INTENT.FLASH, false)
  driver.error()
  driver.compileFinished(true) // a late event must not revive the chain
  assert.deepEqual(types(), ['compile'])
})

// --- 7. A newer edit during the chain blocks the automatic flash ------------

test('an edit made during the automatic compile blocks the flash', () => {
  const { driver, types } = harness()
  driver.edit({ workspace: workspace('v1') })
  driver.request(CHAIN_INTENT.FLASH, true)
  driver.editAcknowledged()
  driver.edit({ workspace: workspace('v2-made-while-compiling') })
  const outcome = driver.compileFinished(true)
  assert.equal(outcome.notice, CHAIN_NOTICE.EDITED_DURING_CHAIN_NO_FLASH)
  assert.deepEqual(types(), ['edit_section_blocks', 'compile'])
  assert.notEqual(driver.draftVersion, null)
})

test('an edit made during the submission also blocks the flash', () => {
  const { driver, types } = harness()
  driver.edit({ workspace: workspace('v1') })
  driver.request(CHAIN_INTENT.FLASH, true)
  driver.edit({ workspace: workspace('v2-made-while-submitting') })
  driver.editAcknowledged()
  driver.compileFinished(true)
  assert.deepEqual(types(), ['edit_section_blocks', 'compile'])
})

// --- chain hygiene -----------------------------------------------------------

test('a second request while a chain runs is ignored', () => {
  const { driver, types } = harness()
  driver.edit({ workspace: workspace('a') })
  driver.request(CHAIN_INTENT.COMPILE, false)
  driver.request(CHAIN_INTENT.FLASH, true)
  assert.deepEqual(types(), ['edit_section_blocks'])
})

test('a send that cannot leave ends the chain instead of hanging it', () => {
  const { driver, socket } = harness()
  socket.open = false
  driver.edit({ workspace: workspace('a') })
  const outcome = driver.request(CHAIN_INTENT.COMPILE, false)
  assert.equal(outcome.sendFailed, true)
  assert.equal(driver.chain.phase, CHAIN_PHASE.IDLE)
  assert.notEqual(driver.draftVersion, null)
})

test('an unattached driver fails its sends cleanly', () => {
  const driver = createChainDriver()
  const outcome = driver.request(CHAIN_INTENT.COMPILE, false)
  assert.equal(outcome.sendFailed, true)
  assert.equal(driver.chain.phase, CHAIN_PHASE.IDLE)
})

// --- 8. Save Section is no longer part of the student workflow --------------

test('Build Mode offers no separate Save Section or Security Test control', () => {
  const source = readFileSync(new URL('../screens/BuildMode.jsx', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /SAVE SECTION/)
  assert.doesNotMatch(source, /SECURITY TEST/)
  assert.doesNotMatch(source, /\bsaveSection\b/)
  // The edit senders are reachable only through the chain's transmit.
  const direct = source.match(/\bsendEdit(SectionBlocks|Region)\(/g) || []
  assert.deepEqual(direct, [], 'no control may send an edit outside the compile chain')
})
