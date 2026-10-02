// The trainer runs fully offline on the Raspberry Pi's own access point. These
// guards fail the moment frontend source reaches for the network for anything
// other than the trainer backend itself: a CDN stylesheet, a Google Fonts URL, a
// remote icon or image, an analytics script.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { sentenceCase } from './text.js'

const root = fileURLToPath(new URL('..', import.meta.url))

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) walk(full, out)
    else out.push(full)
  }
  return out
}

const sourceFiles = walk(join(root, 'src')).filter(
  (file) => /\.(js|jsx|css)$/.test(file) && !/\.test\.js$/.test(file) && !/\.generated\.js$/.test(file),
)

// Hosts a page may legitimately name: the backend on this machine or on the
// Raspberry Pi's private network, and the XML namespace URI an inline <svg>
// declares (never fetched).
const ALLOWED_HOST =
  /^(localhost|127\.0\.0\.1|www\.w3\.org|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(?::\d+)?$/

test('no source file references a remote host', () => {
  const offenders = []
  for (const file of [...sourceFiles, join(root, 'index.html')]) {
    const text = readFileSync(file, 'utf8')
    for (const match of text.matchAll(/https?:\/\/([^\s/'"`)<>]+)/g)) {
      if (!ALLOWED_HOST.test(match[1])) offenders.push(`${file.replace(root, '')}: ${match[0]}`)
    }
  }
  assert.deepEqual(offenders, [])
})

test('every font face is a bundled local file', () => {
  const css = readFileSync(join(root, 'src/styles/fonts.css'), 'utf8')
  const sources = [...css.matchAll(/url\(([^)]+)\)/g)].map((m) => m[1].replace(/['"]/g, ''))
  assert.ok(sources.length >= 8, 'the Inter, Roboto Condensed and JetBrains Mono faces are declared')
  for (const source of sources) {
    assert.match(source, /^\.\.\/assets\/fonts\/[\w-]+\.woff2$/, `font source ${source} is not a bundled file`)
  }
  for (const family of ['Inter', 'Roboto Condensed', 'JetBrains Mono']) {
    assert.ok(css.includes(`font-family: '${family}'`), `${family} is bundled`)
  }
})

test('the page does not pull a script, stylesheet or font from elsewhere', () => {
  const html = readFileSync(join(root, 'index.html'), 'utf8')
  assert.doesNotMatch(html, /<(script|link)[^>]+(src|href)=["']https?:/i)
})

test('backend error text is shown as a sentence', () => {
  assert.equal(sentenceCase('student number must be 1-32 letters'), 'Student number must be 1-32 letters')
  assert.equal(sentenceCase('  already registered '), 'Already registered')
  assert.equal(sentenceCase(''), '')
  assert.equal(sentenceCase(undefined), '')
})

// Blockly loads its zoom/trash sprite, cursors and sounds from
// https://static.blockly.com/media/ unless `media` says otherwise. That is a
// third-party fetch the trainer cannot make (it has no Internet), so they are
// served from the app's own public/ folder.
test('Blockly is injected with local media and no sounds', () => {
  const source = readFileSync(join(root, 'src/components/BlocklyWorkspace.jsx'), 'utf8')
  assert.match(source, /media:\s*`\$\{import\.meta\.env\.BASE_URL\}blockly-media\/`/, 'media must point at the app itself')
  assert.match(source, /sounds:\s*false/, 'sounds must be off (their files are not shipped)')
})

test('public/blockly-media is a faithful copy of the installed Blockly media (minus sounds)', () => {
  const shipped = join(root, 'public/blockly-media')
  const upstream = join(root, 'node_modules/blockly/media')
  const files = readdirSync(shipped)
  assert.ok(files.includes('sprites.svg'), 'the zoom/trash sprite must be shipped')
  assert.ok(!files.some((name) => /\.(mp3|ogg|wav)$/.test(name)), 'no sound files are shipped')
  for (const name of files) {
    assert.deepEqual(readFileSync(join(shipped, name)), readFileSync(join(upstream, name)), `${name} differs from node_modules/blockly/media`)
  }
})
