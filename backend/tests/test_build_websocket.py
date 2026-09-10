"""Phase 3A/3B/3C verification: the `/ws/build` WebSocket protocol.

Covers connection/session announcement, the bootstrap event + state frames,
successful and rejected `edit_region` requests over the real socket,
malformed-frame handling, session isolation between connections, `compile`
requests (Phase 3B) reaching `BuildService` and their truthful success/
failure reported back as events + state, and that none of this touches Hack
Mode's `/ws/hack` session registry or state — the explicit regression
requirement for this phase.

`compile` tests use a fake compiler monkeypatched onto `default_service`
(no real subprocess, no dependency on arduino-cli) — `test_build_compiler.py`
covers the real `ArduinoCliCompiler`, and `test_build_service.py` covers
`BuildService.compile_workspace`'s own orchestration (including concurrent-
request rejection, which needs true coroutine interleaving that a single
WebSocket connection's inherently sequential message loop cannot exercise).
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from app.build import BLINK_REGION_ID
from app.build import SerialDevice
from app.build.compiler import CompileFailureCategory, CompileOutcome
from app.build.flasher import DeviceDetectOutcome, FlashFailureCategory, FlashOutcome
from app.build.service import default_service
from app.build_sessions import build_session_manager
from app.main import app
from app.models.build_messages import BUILD_PROTOCOL_VERSION
from app.sessions import session_manager as hack_session_manager


class _FakeCompiler:
    """Minimal `CompilerAdapter` double — returns a fixed outcome, no subprocess.

    Records every `CompileRequest` it was given (`self.requests`) so a test
    can inspect exactly what was about to be compiled — in particular,
    `request.sketch_dir` is a real materialized copy of the workspace at the
    moment `compile` was processed (see `BuildWorkspace.materialize`), which
    is what `test_compile_without_a_prior_save_uses_the_current_editor_source`
    below reads back to prove the *current* editor draft reached the
    compiler, not a stale/default source.
    """

    def __init__(self, outcome: CompileOutcome) -> None:
        self.outcome = outcome
        self.requests = []

    async def run_compile(self, request) -> CompileOutcome:
        self.requests.append(request)
        return self.outcome


@pytest.fixture
def fake_compile_success(monkeypatch: pytest.MonkeyPatch) -> _FakeCompiler:
    compiler = _FakeCompiler(
        CompileOutcome.ok(
            exit_code=0, stdout="Sketch uses 42 bytes.\n", stderr="", duration_seconds=1.0
        )
    )
    monkeypatch.setattr(default_service, "_compiler", compiler)
    return compiler


@pytest.fixture
def fake_compile_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        default_service,
        "_compiler",
        _FakeCompiler(
            CompileOutcome.failed(
                CompileFailureCategory.COMPILER_ERROR,
                exit_code=1,
                stdout="",
                stderr="main.ino:5:1: error: expected ';'\n",
                duration_seconds=0.5,
            )
        ),
    )


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _open_session(ws) -> tuple[str, dict]:
    """Consume `session`, the two bootstrap `event` frames, and `state`."""
    session_frame = ws.receive_json()
    assert session_frame["type"] == "session"
    assert session_frame["protocol_version"] == BUILD_PROTOCOL_VERSION

    started = ws.receive_json()
    assert started["type"] == "event"
    assert started["event"] == "build_session_started"

    loaded = ws.receive_json()
    assert loaded["type"] == "event"
    assert loaded["event"] == "workspace_loaded"

    state = ws.receive_json()
    assert state["type"] == "state"
    return session_frame["session_id"], state["data"]


def _edit(ws, path: str, region_id: str, source: str) -> None:
    ws.send_json({"type": "edit_region", "path": path, "region_id": region_id, "source": source})


def _compile(ws) -> None:
    ws.send_json({"type": "compile"})


def _flash(ws) -> None:
    ws.send_json({"type": "flash"})


def _hardware_status(ws) -> None:
    ws.send_json({"type": "hardware_status"})


# --- 1: connecting creates a session with a loaded workspace ---------------


def test_connection_creates_session_with_the_blink_poc_project_loaded(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        session_id, state = _open_session(ws)
        assert session_id
        assert state["project"]["scenario_id"] == "led-blink-poc"
        assert state["dirty"] is False
        assert state["compile_status"] == "not_started"


# --- 2: full firmware is visible as one fully editable region (Phase 1 POC)
#
# The Phase 1 LED Blink POC project is temporarily represented as a single
# EDITABLE segment covering the entire file — no LOCKED segment at all — so
# the whole source is editable like a normal code editor (see
# `app/build/blink.py`). This is a project-shape choice, not a protocol or
# workspace change: a project that *does* declare a LOCKED segment (see
# `app/build/environmental.py`) still renders one, exactly as before.


def test_state_exposes_the_entire_file_as_one_editable_segment(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _, state = _open_session(ws)
        assert set(state["files"].keys()) == {"main.ino"}
        segments = state["files"]["main.ino"]["segments"]
        assert [s["kind"] for s in segments] == ["editable"]
        assert segments[0]["region_id"] == BLINK_REGION_ID


# --- 3: editing the security region succeeds and is persisted --------------


def test_edit_region_succeeds_and_state_reflects_it(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)

        _edit(ws, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited")
        code_event = ws.receive_json()
        assert code_event == {
            "type": "event",
            "event": "code_edited",
            "data": {"path": "main.ino", "region_id": BLINK_REGION_ID},
        }
        security_event = ws.receive_json()
        assert security_event["event"] == "security_region_edited"

        state_frame = ws.receive_json()
        assert state_frame["type"] == "state"
        state = state_frame["data"]
        assert state["dirty"] is True
        editable = next(
            s for s in state["files"]["main.ino"]["segments"] if s["region_id"] == BLINK_REGION_ID
        )
        assert editable["text"] == "digitalWrite(2, LOW); // edited"


# --- 4: editing an unrecognised region is rejected over the wire -----------
#
# Named "locked_pre" for its Phase 3A origin, when the default session's
# project (Environmental Monitoring at the time) really did have a region by
# that name. The Phase 1 LED Blink POC project this session now loads is
# temporarily fully editable and has no locked region at all (see
# `app/build/blink.py`), so this id is simply unrecognised here — it still
# exercises the same wire contract (an unknown region name is rejected with
# an `error` frame, never silently accepted). Real LOCKED-region rejection
# is covered directly against `BuildWorkspace`/`BuildService` in
# `test_build_workspace.py` and `test_build_service.py`, against a project
# that still declares one.


def test_edit_locked_region_is_rejected_with_error_frame(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _, before_state = _open_session(ws)

        _edit(ws, "main.ino", "locked_pre", "#include <Evil.h>")
        reply = ws.receive_json()
        assert reply["type"] == "error"
        assert isinstance(reply["message"], str) and reply["message"]

        # No state frame followed the rejection, and a subsequent valid edit
        # proves the connection is still healthy.
        _edit(ws, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited")
        assert ws.receive_json()["type"] == "event"


def test_edit_unknown_region_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        _edit(ws, "main.ino", "does_not_exist", "x")
        reply = ws.receive_json()
        assert reply["type"] == "error"


# --- 5: malformed frames get a controlled error, connection survives -------


def test_malformed_frame_produces_controlled_error(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        ws.send_text("not json at all")
        reply = ws.receive_json()
        assert reply["type"] == "error"

        _edit(ws, "main.ino", BLINK_REGION_ID, "still works")
        assert ws.receive_json()["type"] == "event"


def test_unknown_message_type_produces_controlled_error(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        # "compile" (Phase 3B) and "flash" (Phase 3C) are real, recognised
        # messages — see the `test_compile_*` / `test_flash_*` tests below.
        # This uses a type that is not, and never has been, part of the
        # protocol: validation is not implemented in any phase so far.
        ws.send_json({"type": "validate"})
        reply = ws.receive_json()
        assert reply["type"] == "error"


def test_oversized_source_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        _edit(ws, "main.ino", BLINK_REGION_ID, "x" * 20000)
        reply = ws.receive_json()
        assert reply["type"] == "error"


def test_binary_frames_are_refused(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        ws.send_bytes(b"\x00\x01\x02")
        reply = ws.receive_json()
        assert reply["type"] == "error"


# --- 8: build session state is isolated between connections ----------------


def test_two_connections_have_isolated_build_sessions(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as first:
        first_id, _ = _open_session(first)
        with client.websocket_connect("/ws/build") as second:
            second_id, _ = _open_session(second)
            assert first_id != second_id

            _edit(first, "main.ino", BLINK_REGION_ID, "edited on first")
            first.receive_json()  # code_edited
            first.receive_json()  # security_region_edited
            first.receive_json()  # state

            second_session = build_session_manager._sessions[second_id]
            assert second_session.dirty is False
            assert "pinMode(2, OUTPUT)" in second_session.workspace.region_source(
                "main.ino", BLINK_REGION_ID
            )


# --- disconnect removes the build session -----------------------------------


def test_disconnect_removes_build_session(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _open_session(ws)
    assert build_session_manager._sessions.get(session_id) is None


# --- 10: Build Mode does not affect Hack Mode state -------------------------


def test_build_mode_does_not_touch_hack_mode_sessions(client: TestClient) -> None:
    with client.websocket_connect("/ws/hack") as hack_ws:
        hack_session_frame = hack_ws.receive_json()
        assert hack_session_frame["type"] == "session"
        hack_ws.receive_json()  # banner
        hack_id = hack_session_frame["session_id"]

        with client.websocket_connect("/ws/build") as build_ws:
            build_id, _ = _open_session(build_ws)
            _edit(build_ws, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited")
            build_ws.receive_json()
            build_ws.receive_json()
            build_ws.receive_json()

            assert build_id != hack_id
            # The Hack Mode session and its scenario are completely untouched.
            hack_session = hack_session_manager._sessions[hack_id]
            assert hack_session.scenario.snapshot()["discovery"]["firmware_extracted"] is False

        assert hack_session_manager._sessions.get(hack_id) is not None

    assert hack_session_manager._sessions.get(hack_id) is None


def test_health_endpoint_reports_both_protocol_versions(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["build_protocol_version"] == BUILD_PROTOCOL_VERSION


# --- Phase 3B: `compile` requests over the wire -----------------------------

# --- 21 & 24: compile request reaches BuildService; success is represented -


def test_compile_request_reaches_build_service_and_reports_success(
    client: TestClient, fake_compile_success: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)

        _compile(ws)
        started = ws.receive_json()
        assert started["type"] == "event"
        assert started["event"] == "compile_started"

        running = ws.receive_json()
        assert running["type"] == "state"
        assert running["data"]["compile_status"] == "running"

        succeeded = ws.receive_json()
        assert succeeded["type"] == "event"
        assert succeeded["event"] == "compile_succeeded"
        assert succeeded["data"]["exit_code"] == 0

        state = ws.receive_json()["data"]
        assert state["compile_status"] == "succeeded"
        assert state["compile_output"]["success"] is True
        assert state["compile_output"]["stdout"] == "Sketch uses 42 bytes.\n"


# --- 22 & 23: compile failure is represented correctly ----------------------


def test_compile_request_reports_failure_truthfully(
    client: TestClient, fake_compile_failure: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)

        _compile(ws)
        started = ws.receive_json()
        assert started["event"] == "compile_started"
        assert ws.receive_json()["data"]["compile_status"] == "running"

        failed = ws.receive_json()
        assert failed["type"] == "event"
        assert failed["event"] == "compile_failed"
        assert failed["data"]["category"] == "compiler_error"

        state = ws.receive_json()["data"]
        assert state["compile_status"] == "failed"
        assert state["compile_output"]["success"] is False
        assert "expected ';'" in state["compile_output"]["stderr"]


# --- 25: compile state synchronization --------------------------------------


def test_compile_state_in_frame_matches_session_snapshot(
    client: TestClient, fake_compile_success: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _open_session(ws)
        _compile(ws)
        ws.receive_json()  # compile_started
        ws.receive_json()  # state (running)
        ws.receive_json()  # compile_succeeded
        state = ws.receive_json()["data"]

        session = build_session_manager._sessions[session_id]
        assert state == session.snapshot()
        assert session.compile_status.value == "succeeded"


def test_a_failed_compile_leaves_the_editable_region_intact(
    client: TestClient, fake_compile_failure: None
) -> None:
    """The frontend can still edit and retry — the workspace is never touched."""
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        _compile(ws)
        for _ in range(4):  # compile_started, state, compile_failed, state
            ws.receive_json()

        _edit(ws, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited")
        code_event = ws.receive_json()
        assert code_event["event"] == "code_edited"


# --- Phase 3C: `flash` requests over the wire -------------------------------
#
# Same discipline as the compile tests above: a fake flasher monkeypatched
# onto `default_service` (no real subprocess, no arduino-cli, and no
# physical ESP32). `test_build_flasher.py` covers the real
# `ArduinoCliFlasher`, and `test_build_service.py` covers
# `BuildService.flash_workspace`'s own orchestration. What is verified here
# is only that the request crosses the socket and that each outcome — success,
# failure, and "nothing is plugged in" — reaches the frontend truthfully and
# distinguishably.


class _FakeFlasher:
    """Minimal `FlasherAdapter` double — canned devices and outcome."""

    def __init__(self, devices=(), outcome: FlashOutcome | None = None) -> None:
        self.devices = tuple(devices)
        self.outcome = outcome
        self.requests = []

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        return DeviceDetectOutcome(devices=self.devices)

    async def run_flash(self, request) -> FlashOutcome:
        self.requests.append(request)
        return self.outcome


_ESP32 = SerialDevice(
    port="COM7",
    protocol="serial",
    board_name="ESP32 Dev Module",
    board_fqbn="esp32:esp32:esp32",
    has_usb_id=True,
)


def _install_flasher(monkeypatch: pytest.MonkeyPatch, flasher: _FakeFlasher) -> None:
    monkeypatch.setattr(default_service, "_flasher", flasher)


@pytest.fixture
def fake_flash_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_flasher(
        monkeypatch,
        _FakeFlasher(
            devices=[_ESP32],
            outcome=FlashOutcome.ok(
                exit_code=0,
                stdout="Writing at 0x00010000...\nHash of data verified.\nLeaving...\n",
                stderr="",
                duration_seconds=15.0,
                port="COM7",
            ),
        ),
    )


@pytest.fixture
def fake_flash_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_flasher(
        monkeypatch,
        _FakeFlasher(
            devices=[_ESP32],
            outcome=FlashOutcome.failed(
                FlashFailureCategory.UPLOAD_ERROR,
                exit_code=1,
                stdout="",
                stderr="A fatal error occurred: MD5 of file does not match data in flash!\n",
                duration_seconds=8.0,
                port="COM7",
            ),
        ),
    )


@pytest.fixture
def fake_no_device(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_flasher(monkeypatch, _FakeFlasher(devices=[]))


# --- regression: a compile in flight must not look like a dead socket -------
#
# THE BUG THIS REPRODUCES. `compile_started` was created by
# `BuildService.compile_workspace` at the moment the status flipped to
# RUNNING, but it was only *appended to the list the method returns*, and
# `build_websocket.py` renders that list after the call comes back. A real
# `arduino-cli` compile takes ~60s, so the socket carried nothing at all for
# that whole minute and then delivered `compile_started`, `compile_succeeded`
# and `state` in the same instant. From the browser it was indistinguishable
# from a backend that had silently dropped the request: no incoming frames,
# no traceback, and — because `BuildMode.jsx` derives `isCompiling` from
# `state.compile_status` — a Compile button that stayed enabled the whole
# time and invited more clicks, each queueing another full compile.
#
# Every other compile test here uses a compiler double that returns
# instantly, which is exactly why none of them could see it: with a
# zero-duration compile, "sent up front" and "sent at the end" look the same.


class _BlockingCompiler:
    """Compiler double that stays inside `run_compile` until released.

    Stands in for the ~60s a real ESP32 compile takes, without one.
    """

    def __init__(self, outcome: CompileOutcome) -> None:
        self.outcome = outcome
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()

    async def run_compile(self, _request) -> CompileOutcome:
        self.entered.set()
        # `to_thread` so the event loop stays free to send frames while this
        # "compile" is in flight, exactly as the real `process.run_capture`
        # does. The cap means a regression fails the assertions below
        # instead of hanging the suite.
        await asyncio.to_thread(self.release.wait, 10.0)
        self.returned.set()
        return self.outcome


@pytest.fixture
def blocking_compiler(monkeypatch: pytest.MonkeyPatch) -> _BlockingCompiler:
    compiler = _BlockingCompiler(
        CompileOutcome.ok(
            exit_code=0, stdout="Sketch uses 42 bytes.\n", stderr="", duration_seconds=61.0
        )
    )
    monkeypatch.setattr(default_service, "_compiler", compiler)
    return compiler


def test_compile_started_and_running_state_arrive_before_the_compile_finishes(
    client: TestClient, blocking_compiler: _BlockingCompiler
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)

        _compile(ws)

        started = ws.receive_json()
        assert started["type"] == "event"
        assert started["event"] == "compile_started"

        running = ws.receive_json()
        assert running["type"] == "state"
        # What the Compile button actually reads. Without this the UI cannot
        # know a compile is under way at all.
        assert running["data"]["compile_status"] == "running"

        # The point of the test: both frames were on the wire while the
        # compiler was still running, not batched up after it returned.
        assert blocking_compiler.entered.is_set()
        assert not blocking_compiler.returned.is_set()

        blocking_compiler.release.set()

        succeeded = ws.receive_json()
        assert succeeded["type"] == "event"
        assert succeeded["event"] == "compile_succeeded"

        final = ws.receive_json()
        assert final["type"] == "state"
        assert final["data"]["compile_status"] == "succeeded"


def test_compile_started_is_not_delivered_twice(
    client: TestClient, blocking_compiler: _BlockingCompiler
) -> None:
    """Streaming it up front must not also repeat it in the final batch."""
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)

        _compile(ws)
        assert ws.receive_json()["event"] == "compile_started"
        assert ws.receive_json()["type"] == "state"
        blocking_compiler.release.set()

        rest = [ws.receive_json() for _ in range(2)]
        assert [frame["type"] for frame in rest] == ["event", "state"]
        assert rest[0]["event"] == "compile_succeeded"


def _compile_ok(ws) -> None:
    """Compile over the socket and consume the four resulting frames.

    Four, not three: `compile_started` is delivered up front with a
    `running` snapshot beside it, then `compile_succeeded` with the final
    snapshot. See
    `test_compile_started_and_running_state_arrive_before_the_compile_finishes`.
    """
    _compile(ws)
    assert ws.receive_json()["event"] == "compile_started"
    assert ws.receive_json()["type"] == "state"
    assert ws.receive_json()["event"] == "compile_succeeded"
    assert ws.receive_json()["type"] == "state"


# --- regression: EDIT -> COMPILE without SAVE compiles the current editor
# source, not the original/default one -----------------------------------
#
# The bug this reproduces: `src/screens/BuildMode.jsx`'s COMPILE button used
# to call `sendCompile()` directly, so a student who edited the code but
# never clicked SAVE had their edits sitting only in local React state —
# `arduino-cli` compiled whatever `BuildWorkspace` last held (the original
# default source, or an earlier saved edit), not what was on screen.
#
# The fix keeps SAVE and COMPILE as distinct actions — nothing here
# "auto-clicks SAVE" — but has COMPILE synchronize the current draft into
# the BuildWorkspace through the *same* `edit_region` mechanism SAVE uses,
# immediately before sending `compile`. This test reproduces exactly that
# wire sequence (`edit_region` then `compile`, no separate save-flavoured
# message exists to send) and proves the backend's real, sequential
# single-connection message loop (`app/build_websocket.py`: one client
# message, including every frame it produces, is fully handled before the
# next is even read off the socket) is what makes this race-free with no
# extra synchronization message needed.


def test_compile_without_a_prior_save_uses_the_current_editor_source(
    client: TestClient, fake_compile_success: "_FakeCompiler", fake_flash_success: None
) -> None:
    compiler = fake_compile_success
    modified_source = "void setup() {}\nvoid loop() { delay(100); }\n"

    with client.websocket_connect("/ws/build") as ws:
        _, initial_state = _open_session(ws)
        segment = initial_state["files"]["main.ino"]["segments"][0]
        region_id = segment["region_id"]
        # Sanity: this really is the untouched default LED Blink source
        # (delay(1000)), not something already edited by an earlier test.
        assert "delay(1000)" in segment["text"]
        assert modified_source != segment["text"]

        # EDIT -> COMPILE, no SAVE in between — exactly what BuildMode.jsx's
        # compile() now does when there is an unsent draft.
        _edit(ws, "main.ino", region_id, modified_source)
        assert ws.receive_json()["event"] == "code_edited"
        assert ws.receive_json()["event"] == "security_region_edited"
        edited_state = ws.receive_json()["data"]
        assert edited_state["files"]["main.ino"]["segments"][0]["text"] == modified_source

        _compile(ws)
        assert ws.receive_json()["event"] == "compile_started"
        ws.receive_json()  # running state
        assert ws.receive_json()["event"] == "compile_succeeded"
        final_state = ws.receive_json()["data"]

        # 4 & 5: the compiler actually received the modified source, and the
        # compile the backend reports is a success.
        assert final_state["compile_status"] == "succeeded"
        assert len(compiler.requests) == 1
        compiled_source = (compiler.requests[0].sketch_dir / "main.ino").read_text(
            encoding="utf-8"
        )
        assert compiled_source == modified_source
        assert "delay(1000)" not in compiled_source

        # 6 & 7: flash is available, and it is available *because* the
        # retained artifact's fingerprint matches this modified workspace —
        # not some earlier, stale compile.
        assert final_state["flash_ready"] is True

        # 8: hardware-status reporting is untouched by any of this.
        assert final_state["hardware"] == {
            "status": "not_checked",
            "board_name": None,
            "port": None,
        }

        # Close the loop the task describes: current editor source ->
        # synchronized workspace -> compile -> compiled fingerprint ->
        # flash. A flash right now must succeed against exactly the
        # modified source that was just compiled, never an old one.
        _flash(ws)
        assert ws.receive_json()["event"] == "flash_started"
        assert ws.receive_json()["event"] == "flash_succeeded"
        flashed_state = ws.receive_json()["data"]
        assert flashed_state["flash_status"] == "succeeded"
        assert flashed_state["flash_output"]["success"] is True


# --- 24 & 27: a flash request reaches BuildService; success reaches the UI --


def test_flash_request_reaches_build_service_and_reports_success(
    client: TestClient, fake_compile_success: None, fake_flash_success: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        _compile_ok(ws)

        _flash(ws)
        started = ws.receive_json()
        assert started["type"] == "event"
        assert started["event"] == "flash_started"
        assert started["data"]["fqbn"] == "esp32:esp32:esp32"

        succeeded = ws.receive_json()
        assert succeeded["type"] == "event"
        assert succeeded["event"] == "flash_succeeded"
        assert succeeded["data"]["port"] == "COM7"
        assert succeeded["data"]["exit_code"] == 0

        state = ws.receive_json()["data"]
        assert state["flash_status"] == "succeeded"
        assert state["flash_output"]["success"] is True
        assert state["flash_output"]["port"] == "COM7"
        assert "Hash of data verified." in state["flash_output"]["stdout"]
        # Compile state is untouched: the two are reported independently.
        assert state["compile_status"] == "succeeded"


# --- 28: flash failure reaches the frontend --------------------------------


def test_flash_request_reports_failure_truthfully(
    client: TestClient, fake_compile_success: None, fake_flash_failure: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        _compile_ok(ws)

        _flash(ws)
        assert ws.receive_json()["event"] == "flash_started"

        failed = ws.receive_json()
        assert failed["type"] == "event"
        assert failed["event"] == "flash_failed"
        assert failed["data"]["category"] == "upload_error"

        state = ws.receive_json()["data"]
        assert state["flash_status"] == "failed"
        assert state["flash_output"]["success"] is False
        assert "MD5 of file does not match" in state["flash_output"]["stderr"]
        # A failed flash never rewrites the compile verdict.
        assert state["compile_status"] == "succeeded"
        # ...and the student may retry: the artifact is still valid.
        assert state["flash_ready"] is True


# --- 26: the no-device result reaches the frontend as its own state --------


def test_no_connected_device_reaches_the_frontend_as_no_device(
    client: TestClient, fake_compile_success: None, fake_no_device: None
) -> None:
    """The one result this phase must never dress up as a build problem."""
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        _compile_ok(ws)

        _flash(ws)
        assert ws.receive_json()["event"] == "flash_started"

        failed = ws.receive_json()
        assert failed["event"] == "flash_failed"
        assert failed["data"]["category"] == "no_device"
        assert failed["data"]["port"] is None

        state = ws.receive_json()["data"]
        assert state["flash_status"] == "no_device"
        assert state["flash_output"]["category"] == "no_device"
        # Not a compile failure, not a toolchain failure.
        assert state["compile_status"] == "succeeded"
        assert state["compile_output"]["success"] is True


# --- 25: flash state stays in sync with the session -------------------------


def test_flash_state_in_frame_matches_session_snapshot(
    client: TestClient, fake_compile_success: None, fake_flash_success: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        session_id, _ = _open_session(ws)
        _compile_ok(ws)
        _flash(ws)
        ws.receive_json()  # flash_started
        ws.receive_json()  # flash_succeeded
        state = ws.receive_json()["data"]

        session = build_session_manager._sessions[session_id]
        assert state == session.snapshot()
        assert session.flash_status.value == "succeeded"


# --- compile -> flash integrity, enforced over the wire ---------------------


def test_flash_before_any_compile_is_rejected_over_the_wire(
    client: TestClient, fake_flash_success: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _, state = _open_session(ws)
        assert state["flash_ready"] is False

        _flash(ws)
        reply = ws.receive_json()
        assert reply["type"] == "error"
        assert isinstance(reply["message"], str) and reply["message"]

        # No state frame followed the rejection, and the connection is
        # still healthy.
        _edit(ws, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited")
        assert ws.receive_json()["type"] == "event"


def test_editing_after_a_good_compile_makes_flash_unavailable_again(
    client: TestClient, fake_compile_success: None, fake_flash_success: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        _compile_ok(ws)

        _edit(ws, "main.ino", BLINK_REGION_ID, "digitalWrite(2, LOW); // edited")
        ws.receive_json()  # code_edited
        ws.receive_json()  # security_region_edited
        state = ws.receive_json()["data"]
        assert state["flash_ready"] is False

        _flash(ws)
        reply = ws.receive_json()
        assert reply["type"] == "error"
        assert "compile again" in reply["message"]


# --- 29-32: the wire gives a client no control over the toolchain ----------


def test_a_flash_frame_carrying_extra_fields_is_rejected(client: TestClient) -> None:
    """No port, executable, binary path, or upload flag is nameable here."""
    hostile_frames = [
        {"type": "flash", "port": "COM9"},
        {"type": "flash", "executable": "C:\\\\windows\\\\system32\\\\cmd.exe"},
        {"type": "flash", "input_dir": "C:\\\\evil"},
        {"type": "flash", "fqbn": "esp32:esp32:evil"},
        {"type": "flash", "args": ["--verify"]},
    ]
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        for frame in hostile_frames:
            ws.send_json(frame)
            reply = ws.receive_json()
            assert reply["type"] == "error", frame
            # Rejected by the schema, before any service call — the message
            # is the protocol one, not a compile/flash precondition.
            assert reply["message"] == "message does not match the protocol schema"


def test_region_protection_is_unchanged_by_flashing(
    client: TestClient, fake_compile_success: None, fake_flash_success: None
) -> None:
    """Phase 3A's region protection still holds after a real flash — an edit
    naming a region that doesn't exist in this workspace is still rejected.

    Uses an unknown region id rather than a LOCKED one: the Phase 1 LED
    Blink POC project this session loads is temporarily fully editable and
    has no locked region at all right now (see `app/build/blink.py`).
    `test_build_service.py::test_edit_region_rejects_locked_region` covers
    real LOCKED-region rejection against a project that still has one.
    """
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        _compile_ok(ws)
        _flash(ws)
        for _ in range(3):  # flash_started, flash_succeeded, state
            ws.receive_json()

        _edit(ws, "main.ino", "not_a_region", "#include <Evil.h>")
        reply = ws.receive_json()
        assert reply["type"] == "error"


# --- hardware_status: real device detection independent of flash -----------
#
# Protocol version 4. Unlike `compile`/`flash`, a successful `hardware_status`
# request causes zero `BuildEvent`s (see BuildService.detect_hardware) — a
# client polling this every few seconds must never flood the Activity Log —
# so the wire contract here is exactly one `state` frame back, never an
# `event` frame first.


@pytest.fixture
def fake_multiple_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_flasher(
        monkeypatch,
        _FakeFlasher(devices=[_ESP32, SerialDevice(port="COM9", protocol="serial")]),
    )


def test_hardware_status_starts_not_checked_on_connect(client: TestClient) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _, initial_state = _open_session(ws)
        assert initial_state["hardware"] == {
            "status": "not_checked",
            "board_name": None,
            "port": None,
        }


def test_hardware_status_reports_connected_for_one_device(
    client: TestClient, fake_flash_success: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)

        _hardware_status(ws)
        reply = ws.receive_json()

        # No event frame at all — see the section note above.
        assert reply["type"] == "state"
        assert reply["data"]["hardware"] == {
            "status": "connected",
            "board_name": "ESP32 Dev Module",
            "port": "COM7",
        }


def test_hardware_status_reports_disconnected_for_no_device(
    client: TestClient, fake_no_device: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)

        _hardware_status(ws)
        state = ws.receive_json()["data"]

        assert state["hardware"] == {"status": "disconnected", "board_name": None, "port": None}


def test_hardware_status_reports_ambiguous_for_multiple_devices(
    client: TestClient, fake_multiple_devices: None
) -> None:
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)

        _hardware_status(ws)
        state = ws.receive_json()["data"]

        assert state["hardware"] == {"status": "ambiguous", "board_name": None, "port": None}


def test_hardware_status_does_not_require_a_compile_first(
    client: TestClient, fake_flash_success: None
) -> None:
    """Unlike `flash`, checking hardware presence has no compile precondition."""
    with client.websocket_connect("/ws/build") as ws:
        _, initial_state = _open_session(ws)
        assert initial_state["flash_ready"] is False
        assert initial_state["compile_status"] == "not_started"

        _hardware_status(ws)
        state = ws.receive_json()["data"]

        assert state["hardware"]["status"] == "connected"
        assert state["compile_status"] == "not_started"


def test_hardware_status_frame_carrying_extra_fields_is_rejected(client: TestClient) -> None:
    """Field-less like `compile`/`flash` — nothing on this wire can name a
    port, a board, or claim a connection state."""
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        ws.send_json({"type": "hardware_status", "port": "COM9"})
        reply = ws.receive_json()
        assert reply["type"] == "error"
        assert reply["message"] == "message does not match the protocol schema"


def test_flashing_updates_hardware_status_in_the_same_state_frame(
    client: TestClient, fake_compile_success: None, fake_flash_success: None
) -> None:
    """A flash already runs its own discovery — the frontend should not have
    to send a separate `hardware_status` request right after flashing."""
    with client.websocket_connect("/ws/build") as ws:
        _open_session(ws)
        _compile_ok(ws)

        _flash(ws)
        ws.receive_json()  # flash_started
        ws.receive_json()  # flash_succeeded
        state = ws.receive_json()["data"]

        assert state["hardware"] == {
            "status": "connected",
            "board_name": "ESP32 Dev Module",
            "port": "COM7",
        }
