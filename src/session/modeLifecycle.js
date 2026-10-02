/**
 * The three ways a student's Hack/Build session can end up being left alone,
 * kept apart on purpose:
 *
 *   page reload  the SAME backend session continues. Nothing here runs: the
 *                page is simply gone, the tab's remembered session id
 *                (activeSession.js) survives, and the mode socket re-attaches
 *                with `?session=<id>`.
 *   QUIT         end the current backend session, then the main menu.
 *   RESET        end the current backend session, then enter the same mode
 *                again. The mode is entered the way every mode entry is — through
 *                its baseline preparation (App.jsx `prepareMode`) — and the
 *                mode's own WebSocket then opens a brand new session, because
 *                no session id is remembered any more.
 *
 * Quit and Reset are explicit requests to the backend
 * (`POST /api/sessions/{mode}/{id}/end`); a socket closing never ends a session
 * (backend/app/session_residency.py). Neither is a reload and neither merely
 * clears React state: the backend session is really finished first (its
 * recorder stamped, its serial port released, its workspace dropped), and only
 * then does the screen change.
 *
 * This file is pure logic over injected functions so the sequence can be tested
 * without a DOM or a React renderer; App.jsx supplies the real ones.
 */

import { endSession } from '../api/trainerApi.js'
import { forgetSession, recallSession } from './activeSession.js'

/**
 * End the session this tab remembers for `mode`, and forget it.
 *
 * The pointer is forgotten synchronously, before the request is even sent, so
 * (a) a second call finds nothing and ends nothing — a session is never ended
 * twice from this tab — and (b) whatever opens the mode's socket next finds no
 * id to resume and gets a new session instead of the one just ended.
 *
 * Resolves once the backend has answered (best effort — see `endSession`), so a
 * caller that must not start anything until the old session is gone can wait.
 */
export function endRememberedSession(
  mode,
  { recall = recallSession, forget = forgetSession, end = endSession } = {},
) {
  const sessionId = recall(mode)
  forget(mode)
  return sessionId ? end(mode, sessionId) : Promise.resolve(null)
}

/**
 * The student-facing Quit and Reset actions.
 *
 *   endMode(mode)      ends the mode's current session (`endRememberedSession`)
 *   toMenu()           navigate to the main menu
 *   restartMode(mode)  enter `mode` again through its preparation
 *   screen             the screen the app starts on; afterwards the owner
 *                      reports every change with `screenChanged(screen)`
 *
 * Each action resolves to true when it navigated and false when it did not.
 * It does not when another action is already in flight (a double click ends
 * one session and starts one navigation, not two) or when the student has left
 * `mode` some other way while the end request was out (the hamburger menu, say)
 * — that screen change already ended the session, and this action must not drag
 * them somewhere they did not ask to go.
 */
export function createModeLifecycle({ endMode, toMenu, restartMode, screen: initialScreen = null }) {
  let inFlight = false
  let screen = initialScreen

  async function leave(mode, navigate) {
    if (inFlight) return false
    inFlight = true
    try {
      await endMode(mode)
      if (screen !== mode) return false
      navigate()
      return true
    } finally {
      inFlight = false
    }
  }

  return {
    screenChanged: (next) => {
      screen = next
    },
    quit: (mode) => leave(mode, toMenu),
    reset: (mode) => leave(mode, () => restartMode(mode)),
  }
}
