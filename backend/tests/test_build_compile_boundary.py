"""COMPILE IS THE PERSISTENCE BOUNDARY — the backend half of that contract.

Build Mode no longer has a student-facing "Save Section" step. A Blockly edit
stays in the browser until COMPILE (or FLASH) submits it, and the frontend
chain (`src/build/compileChain.js`, tested by `compileChain.test.js`) sends,
in order: the existing `edit_section_blocks` frame, then `compile` once the
backend acknowledged that edit, then — for FLASH — `flash` once that compile
succeeded with no newer edit made meanwhile.

These tests drive exactly that frame sequence over the REAL `/ws/build`
socket, against Panel 1's REAL firmware with its declared security region,
and pin what the backend guarantees no matter what a client does:

* a security-region edit that still carries opaque C++ is rejected, and the
  rejection leaves the workspace byte-for-byte unchanged — so a failed attempt
  cannot poison the next one (A–D, L);
* an accepted edit is what the compiler is handed, and every accepted edit
  after it is too (E, F);
* any edit after a green build makes that build stale, and the backend refuses
  to flash a stale build even if a client asks it to (G, H, J, K);
* the full edit -> compile -> flash sequence uploads the CURRENT source (I);
* every OTHER editable section still carries opaque source through untouched
  (M).

No real `arduino-cli`, no board: the compiler and flasher are fakes installed
on `default_service` exactly as `test_build_websocket.py` installs them. The
fake compiler reads back the source it was handed from the materialized
sketch directory, which is the only thing a real compile would ever see.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from app.build import BuildWorkspace, SerialDevice, board_info_from_fqbn, load_sketch_project
from app.build.blockly_bridge import program_to_blockly
from app.build.compiler import CompileFailureCategory, CompileOutcome
from app.build.flasher import DeviceDetectOutcome, FlashOutcome
from app.build.program_source import program_for_source
from app.build.semantic import (
    CallStatement,
    ComparisonValue,
    ConditionalStatement,
    LiteralValue,
    SemanticProgram,
    SemanticSection,
    SemanticType,
    SymbolValue,
    UnsupportedReason,
    UnsupportedStatement,
)
from app.build.service import default_service
from app.build_project_selection import BuildProjectSelection, BuildProjectSource
from app.build_sessions import build_session_manager
from app.hardware import DeviceMonitor
from app.panels.service import PanelResourceStatus

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PANEL_ONE = "smart-home-mqtt-control"
PANEL_SKETCH = BACKEND / "panels" / PANEL_ONE / "firmware" / "smart_home_mqtt_control"
SKETCH_NAME = "smart_home_mqtt_control.ino"
SECURITY = "helper_applyCommand"
OTHER_EDITABLE = "setup"

START_TOKEN = "START PANEL1-CMD-AUTH-K7"
STOP_TOKEN = "STOP PANEL1-CMD-AUTH-K7"
OLD_UNAUTHENTICATED_STOP = 'else if (message == "STOP")'


# --- fakes -------------------------------------------------------------------


class _RecordingCompiler:
    """Returns a canned outcome and records the SOURCE it was handed."""

    def __init__(self, success: bool = True) -> None:
        self.success = success
        self.sources: list[str] = []

    async def run_compile(self, request) -> CompileOutcome:
        self.sources.append((request.sketch_dir / SKETCH_NAME).read_text(encoding="utf-8"))
        if self.success:
            return CompileOutcome.ok(exit_code=0, stdout="ok", stderr="", duration_seconds=0.1)
        return CompileOutcome.failed(
            CompileFailureCategory.COMPILER_ERROR,
            exit_code=1,
            stdout="",
            stderr="error: expected ';'",
            duration_seconds=0.1,
        )


_ESP32 = SerialDevice(
    port="COM7",
    protocol="serial",
    board_name="ESP32 Dev Module",
    board_fqbn="esp32:esp32:esp32",
    has_usb_id=True,
)


class _RecordingFlasher:
    """One fake ESP32; records the SOURCE of every artifact it uploads."""

    def __init__(self) -> None:
        self.flashed_sources: list[str] = []

    async def detect_devices(self, _request) -> DeviceDetectOutcome:
        return DeviceDetectOutcome(devices=(_ESP32,))

    async def run_flash(self, request) -> FlashOutcome:
        self.flashed_sources.append(
            (request.sketch_dir / SKETCH_NAME).read_text(encoding="utf-8")
        )
        return FlashOutcome.ok(
            exit_code=0, stdout="Hash of data verified.", stderr="", duration_seconds=1.0, port="COM7"
        )


@pytest.fixture
def compiler(monkeypatch: pytest.MonkeyPatch) -> _RecordingCompiler:
    fake = _RecordingCompiler()
    monkeypatch.setattr(default_service, "_compiler", fake)
    return fake


@pytest.fixture
def flasher(monkeypatch: pytest.MonkeyPatch) -> _RecordingFlasher:
    fake = _RecordingFlasher()
    monkeypatch.setattr(default_service, "_flasher", fake)
    monkeypatch.setattr(
        default_service, "_monitor", DeviceMonitor(detector=fake, cache_seconds=0.0)
    )
    return fake


def _panel_one_workspace(editable: tuple[str, ...] = (SECURITY,)) -> BuildWorkspace:
    return BuildWorkspace(
        load_sketch_project(
            PANEL_SKETCH,
            project_id="smart-home-mqtt-control-firmware",
            scenario_id=PANEL_ONE,
            module_id=PANEL_ONE,
            firmware_name="Smart Home MQTT Control System",
            board=board_info_from_fqbn("esp32:esp32:esp32"),
            editable_section_ids=editable,
            security_region_id=SECURITY,
        )
    )


@pytest.fixture
def connect(monkeypatch: pytest.MonkeyPatch, compiler, flasher):
    """Open `/ws/build` on Panel 1's firmware; yields (socket, session)."""

    def _open(editable: tuple[str, ...] = (SECURITY,)):
        live = _panel_one_workspace(editable)
        monkeypatch.setattr(
            "app.build_websocket.select_build_project",
            lambda: BuildProjectSelection(
                workspace=live,
                source=BuildProjectSource.PANEL_PACKAGE,
                panel_status=PanelResourceStatus.READY,
                panel_id=PANEL_ONE,
            ),
        )
        return live

    from app.main import app

    with TestClient(app) as client:

        class _Opener:
            def __call__(self, editable: tuple[str, ...] = (SECURITY,)):
                _open(editable)
                return client.websocket_connect("/ws/build")

        yield _Opener()


def _session_of(socket):
    """Consume the bootstrap frames; return the live `BuildSession`."""
    session_frame = socket.receive_json()
    assert session_frame["type"] == "session"
    for expected in ("build_session_started", "workspace_loaded"):
        assert socket.receive_json()["event"] == expected
    assert socket.receive_json()["type"] == "state"
    return build_session_manager._sessions[session_frame["session_id"]]


# --- the Blockly payloads a student's editor would send ----------------------


def _gate(command: str, call: str) -> ConditionalStatement:
    """A student-authored `if (message == "<command>") { <call>(); }` block."""
    return ConditionalStatement(
        condition=ComparisonValue(
            left=SymbolValue(name="message"),
            operator="==",
            right=LiteralValue(value=command, value_type=SemanticType.TEXT),
        ),
        body=(CallStatement(function_name=call),),
    )


def _current_security_section(live_source: str) -> SemanticSection:
    section = program_for_source(live_source).section(SECURITY)
    assert section is not None
    return section


def _submission(session, *statements) -> dict:
    """The `{workspace, preserved}` the editor would submit for these statements.

    Produced by B4 exactly as `section_blockly` produces it, so the payload is
    what the real frontend holds — including the preserved-fragment sidecar a
    statement Blockly cannot draw travels in.
    """
    current = _current_security_section(session.workspace.full_source(SKETCH_NAME))
    section = SemanticSection(
        section_id=SECURITY, operation=current.operation, statements=statements
    )
    represented = program_to_blockly(SemanticProgram(sections=(section,))).section(SECURITY)
    assert represented is not None
    return represented.to_representation()


def _original_unauthenticated_branch(session):
    """The committed `else if (message == "STOP")`, carried back as OPAQUE source.

    P3 draws the real `else if` as blocks, so the firmware itself no longer
    holds it opaque. The hazard this fixture models is unchanged, though: a
    client that resubmits that branch as text the toolbox never drew (a stale
    or hand-built payload) must not get it past the ownership rule.
    """
    return UnsupportedStatement(
        text='else if (message == "STOP") {' + chr(10) + '    motorStop();' + chr(10) + '  }',
        reason=UnsupportedReason.NOT_A_CALL,
    )


def _incomplete(session) -> dict:
    """An authenticated START drawn as blocks, but the old unauthenticated
    STOP still riding along as preserved opaque C++ — never cleared."""
    return _submission(
        session, _gate(START_TOKEN, "motorStart"), _original_unauthenticated_branch(session)
    )


def _complete(session, *, start: str = START_TOKEN, stop: str = STOP_TOKEN) -> dict:
    """Full ownership: both gates drawn as blocks, nothing opaque left."""
    return _submission(session, _gate(start, "motorStart"), _gate(stop, "motorStop"))


def _submit(socket, payload: dict, section_id: str = SECURITY) -> dict:
    """Send the chain's edit step. Returns the error frame, or the events."""
    socket.send_json(
        {
            "type": "edit_section_blocks",
            "path": SKETCH_NAME,
            "section_id": section_id,
            "workspace": payload["workspace"],
            "preserved": payload["preserved"],
        }
    )
    first = socket.receive_json()
    if first["type"] == "error":
        return {"rejected": first["message"]}
    events = [first["event"]]
    while True:
        frame = socket.receive_json()
        if frame["type"] == "state":
            return {"events": events, "state": frame["data"]}
        events.append(frame["event"])


def _compile(socket) -> dict:
    """Send the chain's compile step; return the final state."""
    socket.send_json({"type": "compile"})
    assert socket.receive_json()["event"] == "compile_started"
    assert socket.receive_json()["type"] == "state"
    finished = socket.receive_json()
    assert finished["event"] in ("compile_succeeded", "compile_failed")
    state = socket.receive_json()
    assert state["type"] == "state"
    return state["data"]


def _flash(socket) -> dict:
    """Send the chain's flash step. Returns the error frame or the events."""
    socket.send_json({"type": "flash"})
    first = socket.receive_json()
    if first["type"] == "error":
        return {"refused": first["message"]}
    events = [first["event"]]
    while True:
        frame = socket.receive_json()
        if frame["type"] == "state":
            return {"events": events, "state": frame["data"]}
        events.append(frame["event"])


def _security_text(session) -> str:
    return session.workspace.region_source(SKETCH_NAME, SECURITY)


# --- A. rejected, and the backend is unchanged -------------------------------


def test_a_incomplete_security_region_edit_is_rejected_and_changes_nothing(connect, compiler) -> None:
    with connect() as socket:
        session = _session_of(socket)
        before_fingerprint = session.workspace.fingerprint()
        before_source = session.workspace.full_source(SKETCH_NAME)
        events_before = len(session.events)

        answer = _submit(socket, _incomplete(session))

        assert "security region" in answer["rejected"]
        assert session.workspace.fingerprint() == before_fingerprint
        assert session.workspace.full_source(SKETCH_NAME) == before_source
        assert session.dirty is False
        assert len(session.events) == events_before, "a rejection records no edit event"
        assert compiler.sources == [], "the chain never compiles after a rejected edit"


# --- B. the SAME workspace, corrected, is accepted ---------------------------


def test_b_the_same_workspace_corrected_is_accepted_on_the_second_submission(connect) -> None:
    with connect() as socket:
        session = _session_of(socket)
        rejected = _incomplete(session)
        assert _submit(socket, rejected).get("rejected")

        # The student keeps the very same blocks and CLEARs the opaque
        # fragment — the only change is the preserved sidecar.
        corrected = {"workspace": rejected["workspace"], "preserved": []}
        answer = _submit(socket, corrected)

        assert answer["events"] == ["code_edited", "security_region_edited"]
        text = _security_text(session)
        assert START_TOKEN in text
        assert OLD_UNAUTHENTICATED_STOP not in text


# --- C. invalid, invalid: both rejected --------------------------------------


def test_c_invalid_then_invalid_are_both_rejected_with_no_drift(connect) -> None:
    with connect() as socket:
        session = _session_of(socket)
        baseline = session.workspace.fingerprint()

        assert _submit(socket, _incomplete(session)).get("rejected")
        assert session.workspace.fingerprint() == baseline
        assert _submit(socket, _incomplete(session)).get("rejected")
        assert session.workspace.fingerprint() == baseline
        assert OLD_UNAUTHENTICATED_STOP in _security_text(session)


# --- D. invalid, then valid: accepted ----------------------------------------


def test_d_invalid_then_valid_is_accepted(connect, compiler) -> None:
    with connect() as socket:
        session = _session_of(socket)
        assert _submit(socket, _incomplete(session)).get("rejected")

        answer = _submit(socket, _complete(session))
        assert "code_edited" in answer["events"]
        state = _compile(socket)
        assert state["compile_status"] == "succeeded"
        assert len(compiler.sources) == 1


# --- E. valid, then a modified valid: accepted, and the latest one compiles --


def test_e_valid_then_modified_valid_compiles_the_latest(connect, compiler) -> None:
    with connect() as socket:
        session = _session_of(socket)
        assert "code_edited" in _submit(socket, _complete(session))["events"]
        _compile(socket)

        revised_stop = "STOP PANEL1-CMD-AUTH-REVISED"
        assert "code_edited" in _submit(socket, _complete(session, stop=revised_stop))["events"]
        _compile(socket)

        assert STOP_TOKEN in compiler.sources[0]
        assert revised_stop in compiler.sources[1]
        assert STOP_TOKEN not in compiler.sources[1]


# --- F. the compiled C++ is the CURRENT Blockly ------------------------------


def test_f_the_compiler_is_handed_source_generated_from_the_current_blocks(connect, compiler) -> None:
    with connect() as socket:
        session = _session_of(socket)
        _submit(socket, _incomplete(session))  # rejected; must not leak in
        _submit(socket, _complete(session))
        state = _compile(socket)

        assert state["compile_status"] == "succeeded"
        compiled = compiler.sources[-1]
        assert f'"{START_TOKEN}"' in compiled
        assert f'"{STOP_TOKEN}"' in compiled
        assert OLD_UNAUTHENTICATED_STOP not in compiled
        # What was compiled is exactly what the workspace now renders.
        assert compiled == session.workspace.full_source(SKETCH_NAME)


# --- G. an edit after a green build makes it stale ---------------------------


def test_g_an_edit_after_a_successful_compile_makes_the_build_stale(connect) -> None:
    with connect() as socket:
        session = _session_of(socket)
        _submit(socket, _complete(session))
        assert _compile(socket)["flash_ready"] is True

        answer = _submit(socket, _complete(session, stop="STOP SOMETHING-NEWER"))
        assert answer["state"]["flash_ready"] is False
        assert session.compiled_artifact.fingerprint != session.workspace.fingerprint()


# --- H. a stale artifact cannot be flashed -----------------------------------


def test_h_the_backend_refuses_to_flash_a_stale_artifact(connect, flasher) -> None:
    with connect() as socket:
        session = _session_of(socket)
        _submit(socket, _complete(session))
        _compile(socket)
        _submit(socket, _complete(session, stop="STOP SOMETHING-NEWER"))

        answer = _flash(socket)
        assert "compile again before flashing" in answer["refused"]
        assert flasher.flashed_sources == []


# --- I. edit -> compile -> flash uploads the CURRENT source -------------------


def test_i_the_full_chain_flashes_the_current_workspace(connect, compiler, flasher) -> None:
    with connect() as socket:
        session = _session_of(socket)
        # An older green build exists first, so "something is compiled" is
        # true throughout — the chain must still rebuild before uploading.
        _submit(socket, _complete(session, stop="STOP OLDER-BUILD"))
        _compile(socket)

        assert "code_edited" in _submit(socket, _complete(session))["events"]
        assert _compile(socket)["flash_ready"] is True
        answer = _flash(socket)

        assert answer["events"] == ["flash_started", "flash_succeeded"]
        assert len(flasher.flashed_sources) == 1
        assert flasher.flashed_sources[0] == session.workspace.full_source(SKETCH_NAME)
        assert f'"{STOP_TOKEN}"' in flasher.flashed_sources[0]
        assert "STOP OLDER-BUILD" not in flasher.flashed_sources[0]


# --- J. a failed compile leaves nothing to flash ------------------------------


def test_j_after_a_failed_compile_the_backend_refuses_to_flash(connect, compiler, flasher) -> None:
    with connect() as socket:
        session = _session_of(socket)
        _submit(socket, _complete(session))
        _compile(socket)  # a green build exists...
        compiler.success = False
        _submit(socket, _complete(session, stop="STOP DOES-NOT-COMPILE"))
        state = _compile(socket)  # ...and is discarded by this failed one

        assert state["compile_status"] == "failed"
        assert state["flash_ready"] is False
        assert session.compiled_artifact is None
        assert "compile the workspace successfully" in _flash(socket)["refused"]
        assert flasher.flashed_sources == []


# --- K. an edit that lands during the chain blocks the old build --------------


def test_k_an_edit_accepted_after_the_compile_blocks_flashing_that_compile(
    connect, flasher
) -> None:
    """The frontend refuses to send `flash` when an edit appeared mid-chain;
    this pins the backend's own guarantee underneath it, should any client
    send it anyway: once a newer edit has been accepted, the just-finished
    build no longer matches and cannot be uploaded."""
    with connect() as socket:
        session = _session_of(socket)
        _submit(socket, _complete(session))
        assert _compile(socket)["flash_ready"] is True
        _submit(socket, _complete(session, stop="STOP EDITED-DURING-CHAIN"))

        assert "compile again" in _flash(socket)["refused"]
        assert flasher.flashed_sources == []


# --- L. ownership stays enforced, even after an accepted edit -----------------


def test_l_ownership_is_still_enforced_after_an_accepted_edit(connect) -> None:
    with connect() as socket:
        session = _session_of(socket)
        opaque_record = _incomplete(session)["preserved"]
        assert opaque_record and opaque_record[0]["understoodByTheIr"] is False

        assert "code_edited" in _submit(socket, _complete(session))["events"]
        accepted = session.workspace.fingerprint()

        # Re-attach the old opaque branch beside otherwise-valid blocks: an
        # earlier acceptance buys no leniency.
        smuggled = {"workspace": _complete(session)["workspace"], "preserved": opaque_record}
        assert "security region" in _submit(socket, smuggled)["rejected"]
        assert session.workspace.fingerprint() == accepted


# --- M. other editable sections still carry opaque source ---------------------


def test_m_a_non_security_section_still_preserves_opaque_source(connect) -> None:
    with connect(editable=(SECURITY, OTHER_EDITABLE)) as socket:
        session = _session_of(socket)
        socket.send_json(
            {"type": "section_blockly", "path": SKETCH_NAME, "section_id": OTHER_EDITABLE}
        )
        representation = socket.receive_json()["data"]
        carried = [record["text"] for record in representation["preserved"]]
        assert any(record["understoodByTheIr"] is False for record in representation["preserved"])

        answer = _submit(socket, representation, section_id=OTHER_EDITABLE)

        assert answer["events"] == ["code_edited"]
        source = session.workspace.region_source(SKETCH_NAME, OTHER_EDITABLE)
        for text in carried:
            assert text.splitlines()[0].strip() in source
