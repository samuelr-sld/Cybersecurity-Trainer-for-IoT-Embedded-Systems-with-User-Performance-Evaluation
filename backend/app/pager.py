"""Generic application-level output pager for long command results.

Real terminal pagers (`less`, `more`) are OS processes with their own PTY.
Hack Mode's terminal is deliberately not a PTY (see the SECURITY BOUNDARY
note at the top of `app/websocket.py`) and must not become one just to page
long output such as `strings firmware.bin`, so this module is a small,
transport-agnostic substitute: it holds a command's already-produced lines
and hands them back one page at a time, on request.

This module knows nothing about WebSockets, xterm.js, sessions, or any
particular command — it is pure data and arithmetic, which is what keeps it
reusable by any future command with long output, not just `strings`:

- `app/commands/base.py`'s `CommandResult.pageable` is how a handler opts in
  (only `app/commands/handlers/strings.py` sets it today).
- `app/websocket.py` is what turns a `Pager`'s pages into `output` frames and
  a student's raw keystrokes into `PagerAction` values, and what stores the
  active `Pager` on the session between input frames (`HackSession.pager` in
  `app/sessions.py`) — a session can only ever be paging its own output.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Terminal rows reserved for the pager's own status/prompt line, so a page
#: of content never pushes that prompt off the bottom of the visible screen.
STATUS_ROWS = 1

#: Shown after every page that is not the last. Plain text with no line
#: ending of its own — `app/websocket.py` decides line endings, exactly as it
#: already does for every other piece of terminal text.
PAGER_PROMPT = "-- More -- Press Enter/Space for next page, q/Ctrl+C to exit"


def page_size_for(terminal_rows: int) -> int:
    """Usable rows for one page of content, leaving room for the prompt.

    Never less than 1: an absurdly short terminal still makes progress one
    line at a time rather than the pager stalling forever.
    """
    return max(1, terminal_rows - STATUS_ROWS)


@dataclass
class Pager:
    """Paginates one command's complete output, one page at a time.

    `lines` is the complete output the command already produced — the same
    `tuple[str, ...]` shape as `CommandResult.lines` (no terminators, one
    element per line) — and is never mutated or re-ordered. `terminal_rows`
    is the student's current terminal height; `page_size` is derived from it
    so a page always leaves room for the prompt, including after a live
    resize mid-pager (see `resize`).
    """

    lines: tuple[str, ...]
    terminal_rows: int
    position: int = 0

    @property
    def page_size(self) -> int:
        return page_size_for(self.terminal_rows)

    @property
    def has_more(self) -> bool:
        """Whether a further page remains after the last one taken."""
        return self.position < len(self.lines)

    def next_page(self) -> tuple[str, ...]:
        """Return the next page of lines and advance past it.

        Always makes progress while `has_more` is true: `page_size` is never
        less than 1, so a call here can never return an empty page and leave
        `has_more` unchanged (which would otherwise stall the pager forever).
        """
        page = self.lines[self.position : self.position + self.page_size]
        self.position += len(page)
        return page

    def resize(self, terminal_rows: int) -> None:
        """Adopt a new terminal height for every page taken from here on.

        Only future pages are affected — a page already sent is not
        reflowed, matching how a real pager behaves on a mid-session resize.
        """
        self.terminal_rows = terminal_rows


class PagerAction(str, Enum):
    """What one raw input frame means to an active pager."""

    ADVANCE = "advance"
    EXIT = "exit"
    IGNORE = "ignore"

    @classmethod
    def from_input(cls, data: str) -> "PagerAction":
        """Classify one input frame's payload while a pager is active.

        Exact-match only, against the whole payload. While a pager is active
        the frontend forwards one keystroke per frame rather than a buffered
        line (see the pager-mode branch in `src/screens/HackMode.jsx`'s
        `handleTerminalInput`), so there is no line-editing or partial-match
        concern here — just "which single key was this". Anything else,
        including an ordinary printable character a student presses out of
        habit, is ignored rather than guessed at.
        """
        if data in ("\r", "\n", "\r\n", " "):
            return cls.ADVANCE
        if data in ("q", "Q", "\x03"):
            return cls.EXIT
        return cls.IGNORE
