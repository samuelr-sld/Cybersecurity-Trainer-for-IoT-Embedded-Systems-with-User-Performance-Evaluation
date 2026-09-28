"""`strings -n <min-len>` over a real captured firmware artifact.

A real ESP32 dump is mostly machine code, and at `strings(1)`'s default
minimum of 4 a large share of its output is instruction bytes that merely
happen to be printable. `-n` is real `strings`' own answer to that; these
tests pin that it reaches the real-artifact path and is validated.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.commands import CommandContext
from app.commands.registry import build_default_registry
from app.commands.router import CommandRouter
from app.hardware.flash_reader import FirmwareArtifact
from app.sessions import HackSession

#: Short printable runs (code-like noise) around one long configuration string.
IMAGE = b"\x00a!Fp\x00=Q0@x\x00cybertrainer/smart-home/motor/control\x00ab1\x00"


def _context() -> tuple[CommandRouter, CommandContext]:
    session = HackSession(session_id="strings-min-length")
    session.scenario.state.discovery.firmware_extracted = True
    session.firmware_artifact = FirmwareArtifact(
        data=IMAGE,
        offset=0,
        size=len(IMAGE),
        port="COM3",
        captured_at=datetime.now(timezone.utc),
    )
    return CommandRouter(build_default_registry()), CommandContext(session=session)


def _run(line: str):
    router, context = _context()
    return asyncio.run(router.dispatch(line, context))


def test_default_minimum_keeps_short_noise_runs() -> None:
    result = _run("strings firmware.bin")
    assert result.lines == ("a!Fp", "=Q0@x", "cybertrainer/smart-home/motor/control")


@pytest.mark.parametrize("line", ["strings -n 8 firmware.bin", "strings -n8 firmware.bin"])
def test_a_higher_minimum_drops_the_noise(line: str) -> None:
    result = _run(line)
    assert result.exit_code == 0
    assert result.lines == ("cybertrainer/smart-home/motor/control",)


@pytest.mark.parametrize(
    "line",
    [
        "strings -n firmware.bin",
        "strings -n 0 firmware.bin",
        "strings -n abc firmware.bin",
        "strings -n 999 firmware.bin",
        "strings -x firmware.bin",
        "strings -n 8",
    ],
)
def test_invalid_usage_is_refused(line: str) -> None:
    assert _run(line).exit_code == 2
