"""A real-hardware `esptool.py read_flash` can never poison its Hack session.

THE LIVE FAILURE THIS PINS. In a Chrome E2E run with a real ESP32 on COM3,
`esptool.py read_flash 0x0 0x400000 firmware.bin` never returned a result,
and a later `serial-status` in the same session never returned either,
while a new session stayed responsive. Root cause, reproduced against the
real `esptool.exe`:

* The ESP32 core's `esptool.exe` is a PyInstaller one-file bundle: a
  bootloader PARENT that runs the real tool as a CHILD holding the same
  stdout/stderr pipes.
* A 4 MiB read at esptool's default 115200 baud takes ~6 minutes, so the
  240s timeout fired. `subprocess.run(timeout=...)` then killed only the
  parent and, on Windows, called `communicate()` with NO timeout — which
  waited on pipes the orphaned child still held, for as long as it ran.
* A session answers one command at a time, so every later command in that
  session queued behind the hung one.

THE DOUBLE. `fake_esptool` below is the same SHAPE as the real bundle — a
parent that runs the work in a child holding the inherited pipes — driven by
`sys.executable` so it is portable. Everything else on the path is real:
`/ws/hack`, the router, the `esptool.py` handler, `EsptoolFlashReader`, and
`app/build/process.py::run_capture`. Only the executable at argv[0] is
swapped. Live hardware coverage stays in `test_hardware_in_the_loop.py`.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from app import config
from app.build import process
from app.commands import CommandContext
from app.commands.registry import build_default_registry
from app.commands.router import CommandRouter
from app.hardware import (
    DeviceState,
    DeviceStatus,
    default_flash_reader,
    device_monitor,
    flash_reader,
    serial_representations,
)
from app.main import app
from app.sessions import HackSession, session_manager

REAL_PORT = "COM3"

#: A short timeout for the "esptool hangs" cases. The bound a caller can wait
#: is `timeout + 2 * KILL_GRACE_SECONDS`; the assertions allow a margin on
#: top for interpreter start-up on a loaded CI machine.
SHORT_TIMEOUT = 1.5
BOUND = SHORT_TIMEOUT + 2 * process.KILL_GRACE_SECONDS + 5.0

_FAKE_ESPTOOL = textwrap.dedent(
    """
    import json, subprocess, sys, time

    instructions, argv = sys.argv[1], sys.argv[2:]
    with open(instructions, encoding="utf-8") as handle:
        spec = json.load(handle)

    if spec.get("role") != "child":
        # PARENT — PyInstaller's bootloader: run the real work in a child
        # that inherits stdout/stderr, and wait for it.
        with open(instructions + ".argv.json", "w", encoding="utf-8") as handle:
            json.dump(argv, handle)
        child_spec = dict(spec, role="child")
        child_path = instructions + ".child.json"
        with open(child_path, "w", encoding="utf-8") as handle:
            json.dump(child_spec, handle)
        sys.exit(subprocess.call([sys.executable, __file__, child_path, *argv]))

    # CHILD — the real tool. Heartbeat while alive, so a test can prove it died.
    heartbeat = spec["heartbeat"]
    def beat():
        with open(heartbeat, "a", encoding="utf-8") as handle:
            handle.write(".")

    beat()
    if spec["mode"] == "hang":
        while True:
            beat()
            time.sleep(0.1)

    offset, size, out_path = argv[-3], int(argv[-2], 0), argv[-1]
    payload = (b"cybertrainer/smart-home/motor/control\\x00" * (size // 38 + 1))[:size]
    with open(out_path, "wb") as handle:
        handle.write(payload)
    time.sleep(spec.get("sleep", 0))
    sys.stdout.write(f"Read {size} bytes at {offset}\\n")
    sys.exit(0)
    """
)


@pytest.fixture
def fake_esptool(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Route the REAL flash reader's argv through the PyInstaller-shaped fake.

    Returns `configure(mode, sleep=0)`, which sets the behaviour and hands
    back the heartbeat file of the child process.
    """
    script = tmp_path / "fake_esptool.py"
    script.write_text(_FAKE_ESPTOOL, encoding="utf-8")
    instructions = tmp_path / "instructions.json"
    heartbeat = tmp_path / "heartbeat.txt"

    real_run_capture = process.run_capture

    async def run_through_fake(args, *, timeout_seconds):
        # argv[0] is the esptool binary; everything after it is exactly what
        # `EsptoolFlashReader` built, passed through untouched.
        argv = [sys.executable, str(script), str(instructions), *args[1:]]
        return await real_run_capture(argv, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(
        flash_reader,
        "_process_api",
        lambda: (process.ProcessTimedOut, run_through_fake),
    )
    monkeypatch.setattr(default_flash_reader, "_executable", "esptool")

    def configure(mode: str, *, sleep: float = 0) -> pathlib.Path:
        instructions.write_text(
            json.dumps({"mode": mode, "sleep": sleep, "heartbeat": str(heartbeat)}),
            encoding="utf-8",
        )
        return heartbeat

    configure.argv_file = pathlib.Path(str(instructions) + ".argv.json")
    return configure


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch):
    """The shared device state reports a real board — the real-hardware path."""
    monkeypatch.setattr(
        device_monitor,
        "_state",
        DeviceState(
            status=DeviceStatus.CONNECTED,
            port=REAL_PORT,
            board="ESP32 Dev Module",
            mac="20:9b:a9:88:0b:e4",
            panel="SMART HOME MQTT CONTROL SYSTEM",
            port_aliases=serial_representations(REAL_PORT),
        ),
    )
    yield
    device_monitor.reset()


@pytest.fixture
def short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "HARDWARE_FLASH_READ_TIMEOUT_SECONDS", SHORT_TIMEOUT)


def assert_process_dead(heartbeat: pathlib.Path) -> None:
    """The child (the real work, in the PyInstaller shape) is no longer running."""
    assert heartbeat.exists(), "the fake esptool child never started"
    before = heartbeat.stat().st_size
    time.sleep(1.0)
    assert heartbeat.stat().st_size == before, "esptool child still running (orphaned)"


# --- process layer: the exact defect, with no Hack Mode at all --------------


def test_a_timeout_kills_the_whole_tree_and_returns_within_its_bound(
    fake_esptool, tmp_path: pathlib.Path
) -> None:
    """Before the fix this never returned: the orphaned child held the pipes."""
    heartbeat = fake_esptool("hang")
    argv = [
        sys.executable,
        str(tmp_path / "fake_esptool.py"),
        str(tmp_path / "instructions.json"),
        "read_flash", "0x0", "0x10", str(tmp_path / "out.bin"),
    ]

    started = time.monotonic()
    with pytest.raises(process.ProcessTimedOut):
        asyncio.run(process.run_capture(argv, timeout_seconds=SHORT_TIMEOUT))

    assert time.monotonic() - started < BOUND
    assert_process_dead(heartbeat)


def test_a_process_tree_that_exits_by_itself_propagates_its_result(
    fake_esptool, tmp_path: pathlib.Path
) -> None:
    fake_esptool("ok")
    argv = [
        sys.executable,
        str(tmp_path / "fake_esptool.py"),
        str(tmp_path / "instructions.json"),
        "read_flash", "0x0", "0x10", str(tmp_path / "out.bin"),
    ]

    result = asyncio.run(process.run_capture(argv, timeout_seconds=30.0))

    assert result.exit_code == 0
    assert b"Read 16 bytes at 0x0" in result.stdout
    assert (tmp_path / "out.bin").read_bytes()[:11] == b"cybertraine"


def test_cancelling_the_caller_kills_the_tree(fake_esptool, tmp_path: pathlib.Path) -> None:
    heartbeat = fake_esptool("hang")
    argv = [
        sys.executable,
        str(tmp_path / "fake_esptool.py"),
        str(tmp_path / "instructions.json"),
        "read_flash", "0x0", "0x10", str(tmp_path / "out.bin"),
    ]

    async def cancel_mid_run():
        task = asyncio.create_task(process.run_capture(argv, timeout_seconds=300.0))
        for _ in range(100):
            if heartbeat.exists():
                break
            await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_mid_run())
    # The kill runs off the event loop; give it a moment before checking.
    deadline = time.monotonic() + process.KILL_GRACE_SECONDS
    while time.monotonic() < deadline:
        size = heartbeat.stat().st_size
        time.sleep(0.5)
        if heartbeat.stat().st_size == size:
            break
    assert_process_dead(heartbeat)


# --- router level: results, recovery, and the activity log ------------------


def _router_session() -> tuple[CommandRouter, CommandContext]:
    return CommandRouter(build_default_registry()), CommandContext(
        session=HackSession(session_id="flash-lifecycle")
    )


def test_a_successful_real_read_reaches_the_session(fake_esptool, board) -> None:
    fake_esptool("ok")
    router, context = _router_session()

    result = asyncio.run(router.dispatch("esptool.py read_flash 0x0 0x1000 firmware.bin", context))

    assert result.exit_code == 0, result.lines
    assert "Read 4096 bytes at 0x0." in result.lines
    assert context.session.firmware_artifact is not None
    assert len(context.session.firmware_artifact.data) == 0x1000


def test_the_read_ignores_the_students_baud_and_holds_off_mac_probes(
    fake_esptool, board, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_esptool("ok")
    holds_seen: list[int] = []
    real_read = default_flash_reader.read_flash

    async def spy(request):
        holds_seen.append(device_monitor._probe_holds)  # noqa: SLF001
        return await real_read(request)

    monkeypatch.setattr(default_flash_reader, "read_flash", spy)
    router, context = _router_session()

    asyncio.run(router.dispatch("esptool.py --baud 9600 read_flash 0x0 0x10 f.bin", context))

    argv = json.loads(fake_esptool.argv_file.read_text(encoding="utf-8"))
    # The student's --baud is ignored, as before: the argv is unchanged.
    assert argv == ["--port", REAL_PORT, "read_flash", "0x0", "0x10", argv[-1]]
    assert holds_seen == [1]
    assert device_monitor._probe_holds == 0  # noqa: SLF001 - released afterwards


def test_a_timed_out_read_reports_and_the_session_keeps_working(
    fake_esptool, board, short_timeout
) -> None:
    """B + C + F + G at the router, where the activity log is written."""
    heartbeat = fake_esptool("hang")
    router, context = _router_session()

    started = time.monotonic()
    timed_out = asyncio.run(
        router.dispatch("esptool.py read_flash 0x0 0x400000 firmware.bin", context)
    )
    assert time.monotonic() - started < BOUND
    assert timed_out.exit_code == 1
    assert "could not read flash (timeout)" in timed_out.lines[0]
    assert context.session.firmware_artifact is None
    assert_process_dead(heartbeat)

    status = asyncio.run(router.dispatch("serial-status", context))
    assert status.exit_code == 0
    assert any(REAL_PORT in line for line in status.lines)

    helped = asyncio.run(router.dispatch("help", context))  # a simulated tool
    assert helped.exit_code == 0 and helped.lines

    fake_esptool("ok")
    retried = asyncio.run(
        router.dispatch("esptool.py read_flash 0x0 0x1000 firmware.bin", context)
    )
    assert retried.exit_code == 0

    recorder = context.session.recorder
    assert [(c.name, c.exit_code) for c in recorder.commands] == [
        ("esptool.py", 1),
        ("serial-status", 0),
        ("help", 0),
        ("esptool.py", 0),
    ]
    # A timeout is not a transition: only the successful read produced events.
    assert recorder.events and all(e.sequence > recorder.commands[2].sequence for e in recorder.events)
    sequences = [row.sequence for row in recorder.timeline]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)


# --- over the real /ws/hack endpoint ----------------------------------------


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _open(ws) -> None:
    assert ws.receive_json()["type"] == "session"
    assert ws.receive_json()["type"] == "output"


def _command(ws, line: str) -> list[dict]:
    """Every frame one command produced, delimited by a `resize` sentinel.

    `resize` is answered with one output line, in order, by the same message
    loop — so everything before that answer belongs to `line`.
    """
    ws.send_json({"type": "input", "data": line})
    ws.send_json({"type": "resize", "cols": 80, "rows": 24})
    frames = []
    while True:
        frame = ws.receive_json()
        if frame["type"] == "output" and "resize accepted" in frame["data"]:
            return frames
        frames.append(frame)


def _output(frames: list[dict]) -> str:
    return "".join(f["data"] for f in frames if f["type"] == "output")


def test_websocket_success_timeout_then_serial_status_in_one_session(
    client: TestClient, fake_esptool, board, short_timeout
) -> None:
    """The live Chrome sequence, end to end, in ONE session."""
    with client.websocket_connect("/ws/hack") as ws:
        _open(ws)

        fake_esptool("ok")
        ok = _command(ws, "esptool.py read_flash 0x0 0x1000 firmware.bin")
        assert "Read 4096 bytes at 0x0." in _output(ok)
        ok_events = [f for f in ok if f["type"] == "event"]

        heartbeat = fake_esptool("hang")
        started = time.monotonic()
        failed = _command(ws, "esptool.py read_flash 0x0 0x400000 firmware.bin")
        assert time.monotonic() - started < BOUND
        assert "could not read flash (timeout)" in _output(failed)
        assert not [f for f in failed if f["type"] == "event"]
        assert_process_dead(heartbeat)

        status = _command(ws, "serial-status")
        assert REAL_PORT in _output(status)

        nmap = _command(ws, "help")
        assert _output(nmap)

    # Event frames still carry the durable log's server-side sequence numbers.
    sequences = [f["sequence"] for f in ok_events]
    assert sequences == sorted(sequences)


def test_tearing_the_socket_down_mid_read_leaks_neither_session_nor_process(
    client: TestClient, fake_esptool, board
) -> None:
    """E: the server cancels the endpoint on disconnect; the tree dies with it."""
    heartbeat = fake_esptool("hang")
    before = asyncio.run(session_manager.count())

    with client.websocket_connect("/ws/hack") as ws:
        _open(ws)
        ws.send_json({"type": "input", "data": "esptool.py read_flash 0x0 0x400000 f.bin"})
        deadline = time.monotonic() + 20
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert heartbeat.exists(), "the read never started"
        assert asyncio.run(session_manager.count()) == before + 1

    assert asyncio.run(session_manager.count()) == before
    deadline = time.monotonic() + process.KILL_GRACE_SECONDS + 2
    while time.monotonic() < deadline:
        size = heartbeat.stat().st_size
        time.sleep(0.5)
        if heartbeat.stat().st_size == size:
            break
    assert_process_dead(heartbeat)

    # And the next session is fully usable.
    with client.websocket_connect("/ws/hack") as ws:
        _open(ws)
        assert REAL_PORT in _output(_command(ws, "serial-status"))


# --- the socket stays alive while a long command runs -----------------------
#
# The live failure's SECOND cause, which TestClient cannot show (it has no
# real transport). uvicorn pauses reading a WebSocket as soon as one frame
# waits for the app, and the endpoint used to stop calling `receive()` while
# a command ran — so the browser's 10s `hardware_status` poll froze the
# socket, keepalive pongs went unread, and uvicorn closed it (1011) ~40s into
# a real flash read. This runs a REAL uvicorn server with a 1s keepalive and
# a 6s command, which reproduces that closure within seconds on the old loop.


@pytest.fixture
def live_server():
    import socket
    import threading

    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            ws_ping_interval=1.0,
            ws_ping_timeout=1.0,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"ws://127.0.0.1:{port}/ws/hack"
    server.should_exit = True
    thread.join(timeout=10)


def test_a_long_command_does_not_let_keepalive_kill_the_socket(
    live_server: str, fake_esptool, board
) -> None:
    import websockets

    fake_esptool("ok", sleep=6.0)  # ~6 keepalive intervals

    async def scenario() -> list[dict]:
        received: list[dict] = []
        # The CLIENT does not ping (a browser never does); only uvicorn's own
        # keepalive is in play, exactly as in Chrome.
        async with websockets.connect(live_server, ping_interval=None) as ws:
            for _ in range(2):  # session + banner
                received.append(json.loads(await ws.recv()))
            await ws.send(json.dumps({"type": "input", "data": "esptool.py read_flash 0x0 0x1000 f.bin"}))
            await asyncio.sleep(0.5)
            # A frame queued behind the running command — what the frontend's
            # poll does, and what used to pause uvicorn's reading.
            await ws.send(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
            await ws.send(json.dumps({"type": "input", "data": "serial-status"}))
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                received.append(frame)
                if frame["type"] == "output" and REAL_PORT in frame["data"] and "port:" in frame["data"]:
                    return received

    frames = asyncio.run(scenario())
    outputs = [f["data"] for f in frames if f["type"] == "output"]
    read = next(i for i, text in enumerate(outputs) if "Read 4096 bytes at 0x0." in text)
    resized = next(i for i, text in enumerate(outputs) if "resize accepted" in text)
    status = next(i for i, text in enumerate(outputs) if "port:" in text)
    # Still strictly in order, one command at a time.
    assert read < resized < status
