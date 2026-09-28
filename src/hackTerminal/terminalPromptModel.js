/**
 * Pure decision logic for Hack Mode's terminal prompt.
 *
 * HackMode.jsx owns the actual xterm.js writes (there is no frontend DOM/
 * xterm test harness in this repo — see CLAUDE.md); this module holds only
 * the part of that logic that has no side effects, so it can be unit
 * tested. It answers exactly one question: given whether a pager
 * (backend/app/pager.py) currently owns the terminal's input, does the
 * thing that just happened mean the shell prompt should be (re)drawn?
 *
 * The only state that matters is `pagerActive`, because a pager owning
 * input is the one condition under which the prompt must NOT appear (see
 * the pager section of backend/app/websocket.py and its "GENERIC OUTPUT
 * PAGER" note). `applyPromptFrame` folds one incoming signal into the next
 * `pagerActive` value plus whether to draw the prompt now, mirroring the
 * backend's own frame-order guarantee: `pager_start` always arrives before
 * the `output` frame it introduces (see `_render`'s docstring), so an
 * `output` signal can trust `pagerActive` as already up to date — no
 * timing/race guessing needed on the frontend.
 */

//: The exact, literal prompt text this terminal shows when it is ready for
//: a new command. Frontend presentation only — never sent to the backend
//: and never interpreted as part of a command line.
export const PROMPT_TEXT = 'cybertrainer@cybertrainer: -$ '

// ANSI SGR codes for coloring the prompt the way a default Debian/Raspberry
// Pi bash prompt does: bold green for `user@host`, a reset, bold blue for
// the short path-like segment, then another reset before the plain `$ `.
const ANSI_BOLD_GREEN = '\x1b[1;32m'
const ANSI_BOLD_BLUE = '\x1b[1;34m'
const ANSI_RESET = '\x1b[0m'

//: `PROMPT_TEXT`, colored for xterm — HackMode.jsx writes this, never
//: `PROMPT_TEXT` directly, so the terminal gets Pi-style color. Built by
//: wrapping pieces of `PROMPT_TEXT` itself (not a separately hand-typed
//: string) so it can never drift from the plain text the pager and the
//: tests below reason about — see the "the colored prompt strips back to
//: the exact plain prompt" test.
export const PROMPT_DISPLAY =
  `${ANSI_BOLD_GREEN}cybertrainer@cybertrainer${ANSI_RESET}:` +
  `${ANSI_BOLD_BLUE} -${ANSI_RESET}$ `

export function initialPromptState() {
  return { pagerActive: false }
}

/**
 * `frame` is one of:
 *   { type: 'output' }                                    - a server `output` frame was just written
 *   { type: 'action', action: 'clear'|'pager_start'|'pager_end' } - a server `action` frame
 *   { type: 'local-ready' }                                - a local, backend-free reason the
 *                                                             terminal is ready again (a blank
 *                                                             Enter, or Ctrl+C while not paging)
 *
 * Returns `{ state, showPrompt }`: `state` is the next `{ pagerActive }`,
 * and `showPrompt` says whether the caller should draw `PROMPT_TEXT` now.
 *
 * `clear` always requests the prompt: the one handler that emits it
 * (backend/app/commands/handlers/clear.py) never also emits output lines
 * for the same command, so there is no later `output` signal for that
 * dispatch that could draw it twice.
 *
 * `pager_end` also always requests the prompt, and — as long as callers
 * keep passing `pagerActive: true` while the pager owns input, which the
 * `pager_start`/`output` case below ensures — the `output` signal for that
 * same final page never redraws it, since it arrives before `pager_end`
 * flips `pagerActive` back to false (see backend/app/websocket.py's
 * `_advance_pager`/`_exit_pager`, which deliberately keep that order).
 */
export function applyPromptFrame(state, frame) {
  switch (frame.type) {
    case 'output':
      return { state, showPrompt: !state.pagerActive }
    case 'action':
      if (frame.action === 'pager_start') {
        return { state: { pagerActive: true }, showPrompt: false }
      }
      if (frame.action === 'pager_end') {
        return { state: { pagerActive: false }, showPrompt: true }
      }
      if (frame.action === 'clear') {
        return { state, showPrompt: true }
      }
      return { state, showPrompt: false }
    case 'local-ready':
      return { state, showPrompt: true }
    default:
      return { state, showPrompt: false }
  }
}
