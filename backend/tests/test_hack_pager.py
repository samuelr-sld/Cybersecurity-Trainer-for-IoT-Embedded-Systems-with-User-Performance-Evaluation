"""WebSocket-level integration tests for the generic output pager.

tests/test_pager.py covers `Pager`/`PagerAction` as pure logic; this file
proves the wiring in app/websocket.py on top of it: that `strings` output is
actually paged over `/ws/hack`, that pager keystrokes are consumed correctly
(including being routed around `default_router.dispatch` entirely), and that
normal command input still works before, during (for unrelated keys), and
after a pager session.

The simulated (no real firmware artifact attached) `strings` path is used
throughout — see app/scenarios/environmental.py::_firmware_strings, which
always returns exactly these 8 lines once firmware has been "extracted" —
because it is deterministic with no hardware/process fixtures required. The
terminal row count is shrunk with an ordinary `resize` frame so pagination
triggers on that small, fixed set of lines instead of needing hundreds of
lines of real firmware output.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.scenarios.environmental import EnvironmentalMonitoringScenario

STRINGS_LINES = EnvironmentalMonitoringScenario()._firmware_strings()
assert len(STRINGS_LINES) == 8


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _open_session(ws) -> None:
    assert ws.receive_json()["type"] == "session"
    assert ws.receive_json()["type"] == "output"


def _send_input(ws, data: str) -> None:
    ws.send_json({"type": "input", "data": data})


def _resize(ws, cols: int, rows: int) -> None:
    ws.send_json({"type": "resize", "cols": cols, "rows": rows})
    reply = ws.receive_json()
    assert reply["type"] == "output"
    assert "resize accepted" in reply["data"]


def _extract_firmware(ws) -> None:
    """Prerequisite for `strings`: run esptool.py and drain its 3 frames."""
    _send_input(ws, "esptool.py read_flash 0x0 0x400000 firmware.bin")
    for _ in range(3):  # output, event, state
        ws.receive_json()


def _run_strings(ws) -> dict:
    """Run `strings firmware.bin` and return its output frame.

    Only for results that do NOT page: see `_start_paged_strings` below for
    the case that does, where a `pager_start` action arrives first.
    """
    _send_input(ws, "strings firmware.bin")
    output = ws.receive_json()
    assert output["type"] == "output"
    return output


def _start_paged_strings(ws) -> dict:
    """Run `strings firmware.bin` expecting it to page; return the first page.

    `pager_start` arrives BEFORE the output frame it introduces — see the
    "PAGING" note in app/websocket.py::_render — so the frontend can learn a
    pager just started before deciding whether that output should be
    followed by its own shell prompt.
    """
    _send_input(ws, "strings firmware.bin")
    action = ws.receive_json()
    assert action == {"type": "action", "action": "pager_start"}
    output = ws.receive_json()
    assert output["type"] == "output"
    return output


def _drain_events_and_state(ws, count: int) -> None:
    frames = [ws.receive_json() for _ in range(count)]
    assert [f["type"] for f in frames] == ["event", "event", "event", "state"]


def _assert_help_still_works(ws) -> None:
    _send_input(ws, "help")
    reply = ws.receive_json()
    assert reply["type"] == "output"
    assert "Available commands:" in reply["data"]


# --- output shorter than the terminal is not paged --------------------------


def test_output_shorter_than_terminal_is_sent_in_one_frame_no_pager(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _extract_firmware(ws)

        # Default terminal (24 rows -> page_size 23) comfortably fits all 8
        # lines: no pager_start action, no session.pager parked.
        output = _run_strings(ws)
        for line in STRINGS_LINES:
            assert line in output["data"]
        assert "-- More --" not in output["data"]

        _drain_events_and_state(ws, 4)

        # No pager is active: an ordinary keystroke-shaped input is just a
        # (bad) command line, handled normally, not consumed by a pager.
        _assert_help_still_works(ws)


# --- strings output is paged when it does not fit ---------------------------


def test_strings_output_is_paged_when_it_does_not_fit(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _extract_firmware(ws)
        _resize(ws, 80, 5)  # page_size = max(1, 5 - 1) = 4

        output = _start_paged_strings(ws)
        assert output["data"].count("\r\n") == 4  # 4 lines, each CRLF-terminated
        for line in STRINGS_LINES[:4]:
            assert line in output["data"]
        for line in STRINGS_LINES[4:]:
            assert line not in output["data"]
        assert output["data"].endswith(
            "-- More -- Press Enter/Space for next page, q/Ctrl+C to exit"
        )

        _drain_events_and_state(ws, 4)


# --- pager input is consumed correctly: Enter/Space advance -----------------


@pytest.mark.parametrize("advance_key", ["\r", "\n", "\r\n", " "])
def test_advance_keys_deliver_the_next_and_final_page_then_restore_input(
    client: TestClient, advance_key: str
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _extract_firmware(ws)
        _resize(ws, 80, 5)  # page_size = 4 -> exactly two pages of 8 lines

        _start_paged_strings(ws)
        _drain_events_and_state(ws, 4)

        _send_input(ws, advance_key)
        second = ws.receive_json()
        assert second["type"] == "output"
        for line in STRINGS_LINES[4:]:
            assert line in second["data"]
        assert "-- More --" not in second["data"]  # final page: no more prompt

        end_action = ws.receive_json()
        assert end_action == {"type": "action", "action": "pager_end"}

        # Normal command input works immediately afterwards.
        _assert_help_still_works(ws)


# --- pager input is consumed correctly: q / Ctrl+C exit early ---------------


@pytest.mark.parametrize("exit_key", ["q", "Q", "\x03"])
def test_exit_keys_end_the_pager_early_with_no_further_output(
    client: TestClient, exit_key: str
) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _extract_firmware(ws)
        _resize(ws, 80, 3)  # page_size = 2 -> four pages of 8 lines

        _start_paged_strings(ws)
        _drain_events_and_state(ws, 4)

        _send_input(ws, exit_key)
        closing = ws.receive_json()
        assert closing == {"type": "output", "data": "\r\n"}
        end_action = ws.receive_json()
        assert end_action == {"type": "action", "action": "pager_end"}

        # The remaining 6 lines were never sent.
        _assert_help_still_works(ws)


# --- unrelated input is ignored: does not advance, is not echoed -----------


def test_unrelated_keystrokes_do_not_advance_the_pager(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _extract_firmware(ws)
        _resize(ws, 80, 5)  # page_size = 4

        _start_paged_strings(ws)
        _drain_events_and_state(ws, 4)

        # None of these keystrokes produce any frame at all, so the very next
        # frame received below must be caused by the Space that follows them.
        for junk in ("a", "1", "\t", "\x1b", "Z"):
            _send_input(ws, junk)

        _send_input(ws, " ")
        second = ws.receive_json()
        assert second["type"] == "output"
        for line in STRINGS_LINES[4:]:
            assert line in second["data"]
        assert ws.receive_json()["action"] == "pager_end"


# --- a pageable result that fits exactly one page needs no pager -----------


def test_exact_one_page_result_sends_no_pager_frames(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _extract_firmware(ws)
        _resize(ws, 80, 9)  # page_size = 8, exactly STRINGS_LINES' length

        output = _run_strings(ws)
        assert "-- More --" not in output["data"]
        for line in STRINGS_LINES:
            assert line in output["data"]

        # Next frame is the command's own event stream, not a pager_start.
        second = ws.receive_json()
        assert second["type"] == "event"


# --- resize while a pager is active changes the next page's size ----------


def test_resize_mid_pager_changes_the_next_page_size(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _extract_firmware(ws)
        _resize(ws, 80, 3)  # page_size = 2

        _start_paged_strings(ws)
        _drain_events_and_state(ws, 4)

        # Widen the terminal while the pager still has 6 lines queued.
        _resize(ws, 80, 9)  # page_size becomes 8 -> the rest fits in one page

        _send_input(ws, " ")
        second = ws.receive_json()
        assert second["type"] == "output"
        for line in STRINGS_LINES[2:]:
            assert line in second["data"]
        assert "-- More --" not in second["data"]
        assert ws.receive_json()["action"] == "pager_end"


# --- a short error result is never paged, even though it's pageable --------


def test_short_error_output_before_a_page_size_is_not_paged(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as ws:
        _open_session(ws)
        _resize(ws, 80, 3)  # page_size = 2

        # `strings` before firmware has been extracted fails with exactly 2
        # lines — `pageable=True` either way (see strings.py), but 2 lines
        # fits this page size exactly, so no pager should start.
        output = _run_strings(ws)
        assert "No such file or directory" in output["data"]
        assert "-- More --" not in output["data"]
        _assert_help_still_works(ws)
