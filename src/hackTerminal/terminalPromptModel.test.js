import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  PROMPT_DISPLAY,
  PROMPT_TEXT,
  applyPromptFrame,
  initialPromptState,
} from './terminalPromptModel.js'

// Plain string ops rather than a regex: a regex literal matching control
// characters (even via a `\x1b` escape) trips eslint's no-control-regex,
// and there are only 3 known codes to strip here.
function stripAnsi(text) {
  return text.split('\x1b[1;32m').join('').split('\x1b[1;34m').join('').split('\x1b[0m').join('')
}

// These fold the exact frame shapes backend/app/websocket.py sends for the
// pager (see backend/tests/test_hack_pager.py for the server side) plus the
// two purely-local reasons HackMode.jsx's own input handling draws a fresh
// prompt without a round trip (a blank Enter, Ctrl+C while not paging).

test('the exact required prompt text', () => {
  assert.equal(PROMPT_TEXT, 'cybertrainer@cybertrainer: -$ ')
})

// The colored prompt HackMode.jsx actually writes to xterm (Pi-style: bold
// green user@host, bold blue path segment) must never drift from the exact
// required plain text once its color codes are stripped back out.
test('the colored prompt strips back to the exact plain prompt', () => {
  assert.equal(stripAnsi(PROMPT_DISPLAY), PROMPT_TEXT)
})

test('the colored prompt colors user@host and the path segment', () => {
  assert.ok(PROMPT_DISPLAY.includes('\x1b[1;32mcybertrainer@cybertrainer\x1b[0m'))
  assert.ok(PROMPT_DISPLAY.includes('\x1b[1;34m -\x1b[0m'))
})

// 1. normal terminal displays the prompt
test('output while no pager is active requests the prompt', () => {
  const { state, showPrompt } = applyPromptFrame(initialPromptState(), { type: 'output' })
  assert.equal(showPrompt, true)
  assert.equal(state.pagerActive, false)
})

// 3 / 4. pager activation suppresses the prompt for its own output
test('pager_start suppresses the prompt and marks the pager active', () => {
  const result = applyPromptFrame(initialPromptState(), {
    type: 'action',
    action: 'pager_start',
  })
  assert.equal(result.showPrompt, false)
  assert.equal(result.state.pagerActive, true)
})

test('output that follows pager_start does not request the prompt', () => {
  const started = applyPromptFrame(initialPromptState(), { type: 'action', action: 'pager_start' })
  const paged = applyPromptFrame(started.state, { type: 'output' })
  assert.equal(paged.showPrompt, false)
  assert.equal(paged.state.pagerActive, true)
})

// 5. pager exit restores the normal prompt
test('pager_end restores the prompt and clears the active pager', () => {
  const started = applyPromptFrame(initialPromptState(), { type: 'action', action: 'pager_start' })
  const ended = applyPromptFrame(started.state, { type: 'action', action: 'pager_end' })
  assert.equal(ended.showPrompt, true)
  assert.equal(ended.state.pagerActive, false)
})

test('the final page\'s own output frame does not also draw the prompt (no double prompt)', () => {
  // Mirrors backend/app/websocket.py's `_advance_pager`: the last page's
  // `output` frame arrives while `pagerActive` is still true, and only the
  // `pager_end` action that follows requests the prompt.
  const started = applyPromptFrame(initialPromptState(), { type: 'action', action: 'pager_start' })
  const finalPage = applyPromptFrame(started.state, { type: 'output' })
  assert.equal(finalPage.showPrompt, false)
  const ended = applyPromptFrame(finalPage.state, { type: 'action', action: 'pager_end' })
  assert.equal(ended.showPrompt, true)
})

test('clear requests the prompt', () => {
  const { showPrompt } = applyPromptFrame(initialPromptState(), { type: 'action', action: 'clear' })
  assert.equal(showPrompt, true)
})

test('clear does not itself change pager state', () => {
  const started = applyPromptFrame(initialPromptState(), { type: 'action', action: 'pager_start' })
  const { state } = applyPromptFrame(started.state, { type: 'action', action: 'clear' })
  assert.equal(state.pagerActive, true)
})

// A blank Enter or a non-pager Ctrl+C never reaches the backend at all (see
// HackMode.jsx's handleTerminalInput), so the terminal must decide locally
// that it is ready again.
test('a local-ready signal requests the prompt without a server frame', () => {
  const { showPrompt } = applyPromptFrame(initialPromptState(), { type: 'local-ready' })
  assert.equal(showPrompt, true)
})

test('an unrecognised action requests nothing', () => {
  const { showPrompt, state } = applyPromptFrame(initialPromptState(), {
    type: 'action',
    action: 'something-unknown',
  })
  assert.equal(showPrompt, false)
  assert.equal(state.pagerActive, false)
})
