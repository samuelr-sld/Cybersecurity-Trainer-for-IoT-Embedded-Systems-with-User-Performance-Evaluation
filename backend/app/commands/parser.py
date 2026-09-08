"""Safe tokenisation of a completed terminal command line.

SECURITY BOUNDARY
-----------------
This module is a *lexer*, never an interpreter. It splits a line into a
command name and a list of literal argument strings, and that is all it does.
It performs no expansion of any kind — no variables, no globbing, no command
substitution, no redirection, no pipelines, no backslash escapes — and it
never hands anything to an operating-system shell.

Shell metacharacters are not syntax here. Encountering one *unquoted* is a
syntax error, and inside quotes it is an ordinary literal character. So
`nmap 192.168.4.1 && whoami` is rejected as unsupported input, and
`mosquitto_pub -m "a; b"` yields the single literal argument `a; b`. In
neither case does anything execute.

The grammar is deliberately tiny — whitespace-separated words, with single or
double quotes to keep spaces inside one argument:

    nmap -p 1883 192.168.4.1   ->  name="nmap", args=("-p", "1883", "192.168.4.1")
    mosquitto_sub -t "home/#"  ->  name="mosquitto_sub", args=("-t", "home/#")

A hand-written tokeniser is used rather than `shlex` on purpose. `shlex` in
POSIX mode also applies backslash-escape semantics, which would silently
mangle Windows-style paths a student might type, and its behaviour is defined
by the POSIX shell grammar this lab explicitly does not implement. Forty
lines of explicit code make the accepted grammar exactly what is written
here, with nothing inherited.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- limits ---------------------------------------------------------------
#
# The transport layer already caps frame size (see app/config.py), but the
# router must be safe when called from anywhere, so it enforces its own
# bounds rather than trusting its caller.

#: Longest accepted command line, in characters.
MAX_COMMAND_CHARS = 1024

#: Most tokens accepted on one command line.
MAX_TOKENS = 64

# --- grammar --------------------------------------------------------------

_WHITESPACE = frozenset(" \t")

_QUOTES = frozenset("'\"")

#: Characters that carry meaning in a real shell. Unquoted, they are rejected
#: as unsupported input; this lab has no pipelines, redirection, job control,
#: command substitution, or variable expansion to give them meaning.
SHELL_METACHARACTERS = frozenset("|&;<>`$()")

#: Deliberately NOT metacharacters: `#` and `+` are MQTT topic wildcards and
#: `*`/`/`/`-`/`.`/`:` appear in ordinary tool arguments. They are literal.


class CommandSyntaxError(ValueError):
    """The line could not be tokenised into a command.

    The message is written for a student's terminal and is always built from
    this module's own vocabulary — never from the student's input — so a
    hostile line cannot smuggle content back through the error path.
    """


@dataclass(frozen=True)
class ParsedCommand:
    """One tokenised command line.

    `name` is the first token, `args` the rest, and `raw` the line as typed
    (minus the trailing newline). `raw` is kept because later phases will
    record what a student actually typed; nothing interprets it.
    """

    name: str
    args: tuple[str, ...]
    raw: str

    @property
    def argv(self) -> tuple[str, ...]:
        """Name followed by arguments, the way a tool would see it."""
        return (self.name, *self.args)


def _is_control(char: str) -> bool:
    """True for C0/C7 control characters (ESC included), except whitespace.

    Rejecting these keeps ANSI escape sequences out of every value the router
    handles, so text later echoed to the terminal (a command name in a
    "command not found" line, for instance) cannot reposition the cursor or
    recolour the screen.

    TAB is a control codepoint but is ordinary whitespace on a command
    line, so it is excluded here and separates tokens like a space.
    """
    if char in _WHITESPACE:
        return False
    codepoint = ord(char)
    return codepoint < 0x20 or codepoint == 0x7F


def _tokenize(text: str) -> list[str]:
    """Split a line into literal tokens. Raises CommandSyntaxError."""
    tokens: list[str] = []
    current: list[str] = []
    started = False
    quote: str | None = None

    for char in text:
        if _is_control(char):
            raise CommandSyntaxError("unsupported control character in input")

        if quote is not None:
            # Inside quotes every character is literal, including anything
            # that would be an operator outside them.
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue

        if char in _QUOTES:
            quote = char
            started = True
            continue

        if char in _WHITESPACE:
            if started:
                tokens.append("".join(current))
                current.clear()
                started = False
            continue

        if char in SHELL_METACHARACTERS:
            raise CommandSyntaxError(f"unsupported shell operator '{char}'")

        current.append(char)
        started = True

    if quote is not None:
        raise CommandSyntaxError("unbalanced quote")

    if started:
        tokens.append("".join(current))

    return tokens


def parse(raw: str) -> ParsedCommand | None:
    """Parse one completed command line.

    Returns None for a blank line — the same thing a shell does when the user
    presses Enter on an empty prompt. Raises CommandSyntaxError for input the
    lab grammar does not accept.

    The trailing Enter is stripped here: the terminal front end sends the line
    it collected, and whether it includes CR, LF, or neither is a detail of
    that front end, not of the grammar.
    """
    text = raw.rstrip("\r\n")

    if len(text) > MAX_COMMAND_CHARS:
        raise CommandSyntaxError("command line is too long")

    tokens = _tokenize(text)
    if not tokens:
        return None

    if len(tokens) > MAX_TOKENS:
        raise CommandSyntaxError("too many arguments")

    return ParsedCommand(name=tokens[0], args=tuple(tokens[1:]), raw=text)
