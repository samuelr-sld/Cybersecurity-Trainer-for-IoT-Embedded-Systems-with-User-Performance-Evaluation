"""Parsing of MQTT publish payloads for the Environmental Monitoring target.

The simulated device consumes small environmental telemetry payloads. Two
shapes are accepted, both easy to type through the command parser:

    JSON object      {"temperature": 150}
    key=value pairs  temperature=150

JSON is what the device's own telemetry looks like (see the engine's
`mosquitto_sub` output); the `key=value` form exists because typing raw JSON
through the terminal requires wrapping it in single quotes, which is an easy
thing for a student to get wrong.

This is pure, safe parsing: `json.loads` on a plain string and a small manual
splitter. There is no `eval`, no object hooks, and no code path that could
execute the payload — a payload is data that the simulated device inspects,
never anything that runs.
"""

from __future__ import annotations

import json
import re
from typing import Any

# key=value groups are split on commas or whitespace.
_PAIR_SEPARATOR = r"[,\s]+"


def _to_number(value: Any) -> Any:
    """Coerce a value to int/float when it cleanly is one, else leave it.

    Booleans are intentionally left as booleans so they fail the engine's
    numeric check — `{"temperature": true}` is not a valid reading.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return value


def parse_env_payload(message: str | None) -> dict[str, Any] | None:
    """Parse an environmental payload into a field->value mapping.

    Returns None when the message is not a shape we recognise at all. A
    returned dict is not necessarily *valid* — the caller still checks that
    the field it cares about (temperature) is present and numeric. Separating
    "unparseable" from "parsed but wrong" keeps the device's rejection of bad
    data explicit.
    """
    if message is None:
        return None
    text = message.strip()
    if not text:
        return None

    if text[0] in "{[":
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        if not isinstance(parsed, dict):
            return None
        return {str(key): _to_number(value) for key, value in parsed.items()}

    if "=" in text:
        result: dict[str, Any] = {}
        for pair in re.split(_PAIR_SEPARATOR, text):
            if not pair:
                continue
            if "=" not in pair:
                return None
            key, _, raw = pair.partition("=")
            key = key.strip()
            if not key:
                return None
            result[key] = _to_number(raw.strip())
        return result or None

    return None


def is_reading(value: Any) -> bool:
    """True for a value the device would accept as a sensor reading."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)
