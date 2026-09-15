"""MAC address spelling — pure, dependency-free.

Split out of `identity.py` (which reads a MAC from real hardware with
esptool) so that `panels.py` — the registry that *interprets* a MAC — can
normalize one without importing anything that knows how to reach a board.
`identity.py` re-exports `normalize_mac`, so every existing import of it
from there keeps working unchanged.
"""

from __future__ import annotations

import re

#: Canonical form: six lower-case hex octets separated by colons. Kept as a
#: pattern string used with module-level `re.fullmatch` for the same reason
#: `identity.py::_MAC_PATTERN` is — see that constant's comment.
_CANONICAL_MAC = r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}"


def normalize_mac(raw: str | None) -> str | None:
    """Canonical lower-case colon form, or None if this is not a MAC.

    One spelling everywhere — the panel registry, the wire, and the UI all
    use it — so a lookup can never miss because a tool changed case or
    separator. Accepts the `-` separated spelling some tools emit, and
    surrounding whitespace. Anything else (a bare hex string, a wrong octet
    count, a non-hex digit) is rejected rather than coerced: a guessed MAC
    could resolve to the wrong panel.
    """
    if not raw:
        return None
    candidate = raw.strip().replace("-", ":").lower()
    if re.fullmatch(_CANONICAL_MAC, candidate):
        return candidate
    return None
