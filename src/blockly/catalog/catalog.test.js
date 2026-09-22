import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ARDUINO_BLOCK_TYPES } from '../arduinoBlocks.js'
import { FULL_TOOLBOX, MASTER_CATALOG, POPULATED_TOOLBOX } from './masterCatalog.generated.js'

// Blockly itself cannot be exercised under plain Node (`blockly/core` exposes
// only a default export there), so these tests check the catalog data, the
// toolboxes built from it, and that the catalog and `arduinoBlocks.js` agree
// on which block types exist. The Python suite (backend/tests/
// test_blockly_catalog.py) owns the catalog's structural rules.

const { categories, blocks } = MASTER_CATALOG
const categoryEntries = (toolbox) => toolbox.contents.filter((item) => item.kind === 'category')
const toolboxTypes = (toolbox) => categoryEntries(toolbox).flatMap((entry) => entry.contents.map((item) => item.type))

test('category and block ids are unique and blocks reference a real category', () => {
  const categoryIds = categories.map((category) => category.id)
  assert.equal(new Set(categoryIds).size, categoryIds.length)
  const blockIds = blocks.map((block) => block.id)
  assert.equal(new Set(blockIds).size, blockIds.length)
  for (const block of blocks) {
    assert.ok(categoryIds.includes(block.categoryId), `${block.id} has unknown category ${block.categoryId}`)
    assert.match(block.semanticOperation, /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/)
  }
})

test('the catalog and arduinoBlocks.js define exactly the same Blockly types', () => {
  const cataloged = blocks.filter((block) => block.blocklyType !== null).map((block) => block.blocklyType)
  assert.deepEqual([...cataloged].sort(), [...ARDUINO_BLOCK_TYPES].sort())
})

test('only implemented blocks carry a Blockly type and a generator id', () => {
  for (const block of blocks) {
    const implemented = block.status === 'implemented'
    assert.equal(block.blocklyType !== null, block.status !== 'cataloged', block.id)
    assert.equal(block.generatorId !== null, implemented, block.id)
  }
})

test('the full toolbox lists every category once, in catalog order', () => {
  assert.deepEqual(
    categoryEntries(FULL_TOOLBOX).map((entry) => entry.name),
    categories.map((category) => category.displayName),
  )
})

test('the populated toolbox holds only categories with a usable block', () => {
  const entries = categoryEntries(POPULATED_TOOLBOX)
  assert.ok(entries.length > 0)
  for (const entry of entries) assert.ok(entry.contents.length > 0, entry.name)
  assert.deepEqual(toolboxTypes(POPULATED_TOOLBOX).sort(), [...ARDUINO_BLOCK_TYPES].sort())
  assert.deepEqual(toolboxTypes(FULL_TOOLBOX).sort(), [...ARDUINO_BLOCK_TYPES].sort())
})
