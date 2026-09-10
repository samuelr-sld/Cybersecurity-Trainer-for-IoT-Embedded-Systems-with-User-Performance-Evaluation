"""Phase 3C verification: the Arduino CLI flasher adapter.

Three layers of coverage, mirroring `test_build_compiler.py`:

1. Behaviour of `ArduinoCliFlasher` against a small fake "arduino-cli" this
   file writes to a temp file and drives through `sys.executable` (a real,
   portable, always-present native executable — no dependency on arduino-cli
   being installed and, crucially for this phase, no dependency on a
   physical ESP32 being plugged in). This covers a successful upload, a real
   exit-code failure, stdout/stderr capture, timeout handling, a missing
   executable, device discovery finding zero/one/several devices, a device
   that disappears mid-upload, and that arguments reach the subprocess as a
   literal argv array — with the right FQBN, the selected port, and the
   already-built artifact directory — rather than through any shell.
2. Pure parsing/selection tests for the `board list` JSON shapes the Arduino
   CLI emits, since that is what decides "is an ESP32 connected".
3. A static safety check on `app/build/flasher.py` itself: every execution
   primitive the rest of the backend forbids stays forbidden there too,
   *except* the one, deliberate, by-name exception — `subprocess` — and the
   module never uses `shell=True` or `create_subprocess_shell`.

There is deliberately no fake "hardware success" path anywhere in this file:
a real upload to a real board is verified by plugging one in and using Build
Mode, never by a test asserting against a script that pretends to be a
bootloader. What these tests verify is that the adapter drives the toolchain
correctly and reports whatever it says truthfully.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
import sys
import textwrap
import tokenize

import pytest

from app import config
from app.build.flasher import (
    ArduinoCliFlasher,
    DeviceDetectRequest,
    FlashFailureCategory,
    FlashRequest,
    SerialDevice,
    parse_board_list,
)

FLASHER_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "app" / "build" / "flasher.py"
)

FQBN = "esp32:esp32:esp32"


def run(coro):
    return asyncio.run(coro)


# --- a small, portable fake "arduino-cli" -----------------------------------
#
# Invoked as: <sys.executable> <this script> <instructions.json> <upload|board> ...
#
# It writes every argv it received to "<instructions.json>.argv.json" (for
# the argument-safety tests), then behaves as instructions.json says. The
# "board list" branch prints canned JSON so device discovery can be driven
# without any real serial hardware.

_FAKE_CLI_SCRIPT = textwrap.dedent(
    """
    import json
    import sys
    import time

    instructions_path = sys.argv[1]
    with open(instructions_path, encoding="utf-8") as handle:
        instructions = json.load(handle)

    with open(instructions_path + ".argv.json", "w", encoding="utf-8") as handle:
        json.dump(sys.argv, handle)

    subcommand = sys.argv[2] if len(sys.argv) > 2 else ""

    if subcommand == "board":
        listing = instructions.get("board_list")
        if listing is None:
            sys.stdout.write(instructions.get("board_list_raw", "{}"))
        else:
            json.dump(listing, sys.stdout)
        sys.stderr.write(instructions.get("board_list_stderr", ""))
        sys.exit(instructions.get("board_list_exit_code", 0))

    sleep_seconds = instructions.get("sleep", 0)
    if sleep_seconds:
        time.sleep(sleep_seconds)

    sys.stdout.write(instructions.get("stdout", ""))
    sys.stderr.write(instructions.get("stderr", ""))
    sys.exit(instructions.get("exit_code", 0))
    """
)


@pytest.fixture
def fake_cli_script(tmp_path: pathlib.Path) -> pathlib.Path:
    script_path = tmp_path / "fake_arduino_cli.py"
    script_path.write_text(_FAKE_CLI_SCRIPT, encoding="utf-8")
    return script_path


def make_flasher(script_path: pathlib.Path, instructions_path: pathlib.Path) -> ArduinoCliFlasher:
    """An ArduinoCliFlasher that runs the fake script via sys.executable."""
    return ArduinoCliFlasher(
        sys.executable, prefix_args=(str(script_path), str(instructions_path))
    )


def write_instructions(path: pathlib.Path, **instructions) -> pathlib.Path:
    path.write_text(json.dumps(instructions), encoding="utf-8")
    return path


def make_request(tmp_path: pathlib.Path, **overrides) -> FlashRequest:
    defaults = dict(
        sketch_dir=tmp_path / "sketch",
        build_path=tmp_path / "build",
        fqbn=FQBN,
        port="COM7",
        timeout_seconds=5.0,
    )
    defaults.update(overrides)
    defaults["sketch_dir"].mkdir(parents=True, exist_ok=True)
    defaults["build_path"].mkdir(parents=True, exist_ok=True)
    return FlashRequest(**defaults)


def detect_request(**overrides) -> DeviceDetectRequest:
    defaults = dict(fqbn=FQBN, timeout_seconds=5.0)
    defaults.update(overrides)
    return DeviceDetectRequest(**defaults)


def port_entry(address: str, *, fqbn: str | None = None, vid: str | None = "0x10c4",
               protocol: str = "serial") -> dict:
    """One `detected_ports` entry in the shape arduino-cli 1.x emits."""
    port: dict = {"address": address, "label": address, "protocol": protocol}
    if vid is not None:
        port["properties"] = {"vid": vid, "pid": "0xea60"}
    entry: dict = {"port": port}
    if fqbn is not None:
        entry["matching_boards"] = [{"name": "ESP32 Dev Module", "fqbn": fqbn}]
    return entry


def read_argv(tmp_path: pathlib.Path) -> list[str]:
    return json.loads((tmp_path / "instructions.json.argv.json").read_text(encoding="utf-8"))


# --- 1: successful upload ----------------------------------------------------


def test_successful_upload_reports_success(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json", exit_code=0, stdout="Hash of data verified.\n"
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.run_flash(make_request(tmp_path)))

    assert outcome.success is True
    assert outcome.category is FlashFailureCategory.NONE
    assert outcome.exit_code == 0
    assert outcome.port == "COM7"
    assert "Hash of data verified." in outcome.stdout


# --- 2: real exit-code failure ----------------------------------------------


def test_nonzero_exit_code_reports_upload_error(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        exit_code=1,
        stderr="A fatal error occurred: MD5 of file does not match data in flash!\n",
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.run_flash(make_request(tmp_path)))

    assert outcome.success is False
    assert outcome.category is FlashFailureCategory.UPLOAD_ERROR
    assert outcome.exit_code == 1
    assert "MD5 of file does not match" in outcome.stderr


# --- 3: stdout/stderr capture (both at once) --------------------------------


def test_stdout_and_stderr_are_both_captured(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        exit_code=0,
        stdout="Connecting....\nWriting at 0x00010000...\n",
        stderr="esptool.py v4.5\n",
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.run_flash(make_request(tmp_path)))

    assert "Connecting" in outcome.stdout
    assert "Writing at 0x00010000" in outcome.stdout
    assert "esptool.py v4.5" in outcome.stderr


# --- 4: timeout handling -----------------------------------------------------


def test_upload_timeout_kills_the_process_and_reports_truthfully(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(tmp_path / "instructions.json", sleep=10, exit_code=0)
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.run_flash(make_request(tmp_path, timeout_seconds=0.3)))

    assert outcome.success is False
    assert outcome.category is FlashFailureCategory.TIMEOUT
    # Returned promptly (bounded by the timeout), not after the fake
    # script's full 10-second sleep — the process was really killed.
    assert outcome.duration_seconds < 5
    # A timed-out upload is never reported as a success, whatever the
    # process might have printed before it was killed.
    assert outcome.exit_code is None


def test_device_detection_timeout_is_reported_and_not_read_as_no_device(
    tmp_path: pathlib.Path,
) -> None:
    # The fake CLI's "board" branch answers immediately, so drive this
    # timeout through a script that only ever sleeps.
    slow_script = tmp_path / "slow_cli.py"
    slow_script.write_text(
        textwrap.dedent(
            """
            import time
            time.sleep(10)
            """
        ),
        encoding="utf-8",
    )
    flasher = ArduinoCliFlasher(sys.executable, prefix_args=(str(slow_script),))

    outcome = run(flasher.detect_devices(detect_request(timeout_seconds=0.3)))

    assert outcome.ok is False
    assert outcome.category is FlashFailureCategory.TIMEOUT
    assert outcome.devices == ()


# --- 5: Arduino CLI missing / unavailable -----------------------------------


def test_missing_executable_reports_toolchain_unavailable_on_upload(
    tmp_path: pathlib.Path,
) -> None:
    flasher = ArduinoCliFlasher(str(tmp_path / "no-such-arduino-cli-binary"))

    outcome = run(flasher.run_flash(make_request(tmp_path)))

    assert outcome.success is False
    assert outcome.category is FlashFailureCategory.TOOLCHAIN_UNAVAILABLE
    assert outcome.exit_code is None


def test_missing_executable_reports_toolchain_unavailable_on_detection(
    tmp_path: pathlib.Path,
) -> None:
    """A missing toolchain must never masquerade as 'no ESP32 connected'."""
    flasher = ArduinoCliFlasher(str(tmp_path / "no-such-arduino-cli-binary"))

    outcome = run(flasher.detect_devices(detect_request()))

    assert outcome.ok is False
    assert outcome.category is FlashFailureCategory.TOOLCHAIN_UNAVAILABLE
    assert outcome.devices == ()


def test_failing_board_list_is_an_internal_error_not_an_empty_device_list(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        board_list_exit_code=1,
        board_list_stderr="unable to enumerate serial ports\n",
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.detect_devices(detect_request()))

    assert outcome.ok is False
    assert outcome.category is FlashFailureCategory.INTERNAL_ERROR
    assert outcome.devices == ()


def test_unreadable_board_list_output_is_an_internal_error(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json", board_list_raw="not json at all"
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.detect_devices(detect_request()))

    assert outcome.category is FlashFailureCategory.INTERNAL_ERROR


# --- 6: no device detected ---------------------------------------------------


def test_no_connected_devices_yields_an_empty_but_successful_detection(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Zero devices is a truthful answer, not a discovery failure."""
    instructions = write_instructions(
        tmp_path / "instructions.json", board_list={"detected_ports": []}
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.detect_devices(detect_request()))

    assert outcome.ok is True
    assert outcome.category is FlashFailureCategory.NONE
    assert outcome.devices == ()


def test_one_connected_device_is_detected(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        board_list={"detected_ports": [port_entry("COM7", fqbn=FQBN)]},
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.detect_devices(detect_request()))

    assert outcome.ok is True
    assert [device.port for device in outcome.devices] == ["COM7"]
    assert outcome.devices[0].board_fqbn == FQBN


# --- 7: multiple ambiguous devices ------------------------------------------


def test_two_compatible_devices_are_both_returned_rather_than_one_being_chosen(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The adapter reports the ambiguity; it never picks a board to flash."""
    instructions = write_instructions(
        tmp_path / "instructions.json",
        board_list={
            "detected_ports": [
                port_entry("COM7", fqbn=FQBN),
                port_entry("COM9", fqbn=FQBN),
            ]
        },
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.detect_devices(detect_request()))

    assert outcome.ok is True
    assert sorted(device.port for device in outcome.devices) == ["COM7", "COM9"]


# --- 8: device disappears during upload -------------------------------------


def test_device_that_disappears_mid_upload_is_categorized_as_disconnected(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Detection saw the port; the upload then failed because it went away."""
    instructions = write_instructions(
        tmp_path / "instructions.json",
        exit_code=1,
        stderr=(
            "serial.serialutil.SerialException: could not open port 'COM7': "
            "FileNotFoundError(2, 'The system cannot find the file specified.')\n"
        ),
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.run_flash(make_request(tmp_path)))

    assert outcome.success is False
    assert outcome.category is FlashFailureCategory.DEVICE_DISCONNECTED
    assert outcome.port == "COM7"
    # The real stderr is preserved so a student sees what actually happened.
    assert "could not open port" in outcome.stderr


def test_a_failed_upload_is_never_reported_as_success(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Even with reassuring stdout, the exit code alone decides success."""
    instructions = write_instructions(
        tmp_path / "instructions.json",
        exit_code=2,
        stdout="Writing at 0x00010000... (100 %)\nHash of data verified.\n",
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.run_flash(make_request(tmp_path)))

    assert outcome.success is False
    assert outcome.exit_code == 2


# --- 9, 10, 11 & 12: safe argument construction, FQBN, port, artifact ------


def test_upload_arguments_reach_the_process_as_a_literal_array(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """A hostile-looking port is one literal argv element, never shell text."""
    instructions = write_instructions(tmp_path / "instructions.json", exit_code=0)
    flasher = make_flasher(fake_cli_script, instructions)
    hostile_port = "COM7; rm -rf / && echo pwned"

    run(flasher.run_flash(make_request(tmp_path, port=hostile_port)))

    argv = read_argv(tmp_path)
    # The hostile string arrived intact as exactly one argv element,
    # immediately after "--port", never split on the shell metacharacters it
    # contains and never interpreted — nothing this process ran was a shell.
    assert hostile_port in argv
    assert argv[argv.index("--port") + 1] == hostile_port


def test_upload_command_carries_fqbn_selected_port_and_built_artifact(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(tmp_path / "instructions.json", exit_code=0)
    flasher = make_flasher(fake_cli_script, instructions)
    request = make_request(tmp_path)

    run(flasher.run_flash(request))

    argv = read_argv(tmp_path)
    assert "upload" in argv
    # The board target comes from the project's BoardInfo, verbatim.
    assert argv[argv.index("--fqbn") + 1] == FQBN
    # The port is the one that was selected, verbatim.
    assert argv[argv.index("--port") + 1] == "COM7"
    # `--input-dir` points at the already-built artifact, which is what
    # makes this an upload of what was compiled rather than a fresh build.
    assert argv[argv.index("--input-dir") + 1] == str(request.build_path)
    assert str(request.sketch_dir) in argv
    # Nothing compiles here.
    assert "compile" not in argv


def test_detection_command_is_a_plain_json_board_listing(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json", board_list={"detected_ports": []}
    )
    flasher = make_flasher(fake_cli_script, instructions)

    run(flasher.detect_devices(detect_request()))

    argv = read_argv(tmp_path)
    assert "board" in argv
    assert "list" in argv
    assert argv[argv.index("--format") + 1] == "json"
    # Detection never writes to a device.
    assert "upload" not in argv


def test_temporary_artifact_paths_are_passed_through_untouched(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Paths with spaces (a normal Windows temp path) survive as one argument."""
    instructions = write_instructions(tmp_path / "instructions.json", exit_code=0)
    flasher = make_flasher(fake_cli_script, instructions)
    spaced = tmp_path / "a path with spaces"
    request = make_request(
        tmp_path, sketch_dir=spaced / "sketch", build_path=spaced / "build"
    )

    outcome = run(flasher.run_flash(request))

    assert outcome.success is True
    argv = read_argv(tmp_path)
    assert argv[argv.index("--input-dir") + 1] == str(request.build_path)
    assert str(request.sketch_dir) in argv


# --- board list parsing / device selection ----------------------------------


def test_parse_board_list_reads_the_modern_detected_ports_shape() -> None:
    devices = parse_board_list(json.dumps({"detected_ports": [port_entry("COM7", fqbn=FQBN)]}))
    assert devices == (
        SerialDevice(
            port="COM7",
            protocol="serial",
            board_name="ESP32 Dev Module",
            board_fqbn=FQBN,
            has_usb_id=True,
        ),
    )


def test_parse_board_list_reads_the_legacy_flat_list_shape() -> None:
    payload = json.dumps(
        [
            {
                "address": "/dev/ttyUSB0",
                "protocol": "serial",
                "boards": [{"name": "ESP32 Dev Module", "FQBN": FQBN}],
            }
        ]
    )
    devices = parse_board_list(payload)
    assert [device.port for device in devices] == ["/dev/ttyUSB0"]
    assert devices[0].board_fqbn == FQBN


def test_parse_board_list_rejects_unreadable_output() -> None:
    with pytest.raises(ValueError):
        parse_board_list("<html>not json</html>")


def test_detection_ignores_non_serial_ports(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    instructions = write_instructions(
        tmp_path / "instructions.json",
        board_list={
            "detected_ports": [
                port_entry("192.168.1.50", fqbn=None, vid=None, protocol="mdns"),
                port_entry("COM7"),
            ]
        },
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.detect_devices(detect_request()))

    assert [device.port for device in outcome.devices] == ["COM7"]


def test_detection_prefers_a_positively_identified_esp32_over_other_serial_ports(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """One identified ESP32 next to an unrelated USB serial device is not ambiguous."""
    instructions = write_instructions(
        tmp_path / "instructions.json",
        board_list={
            "detected_ports": [
                port_entry("COM3", fqbn="arduino:avr:uno"),
                port_entry("COM7", fqbn=FQBN),
            ]
        },
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.detect_devices(detect_request()))

    assert [device.port for device in outcome.devices] == ["COM7"]


def test_detection_falls_back_to_usb_serial_ports_when_no_board_is_identified(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """A classic ESP32 behind a CP2102 bridge reports no matching board at all."""
    instructions = write_instructions(
        tmp_path / "instructions.json",
        board_list={
            "detected_ports": [
                port_entry("COM1", vid=None),  # built-in/virtual port, no USB id
                port_entry("COM7"),  # USB-UART bridge, unidentified board
            ]
        },
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.detect_devices(detect_request()))

    assert [device.port for device in outcome.devices] == ["COM7"]


def test_two_unidentified_usb_serial_ports_stay_ambiguous(
    fake_cli_script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """No signal separates them, so the adapter refuses to narrow to one."""
    instructions = write_instructions(
        tmp_path / "instructions.json",
        board_list={"detected_ports": [port_entry("COM7"), port_entry("COM9")]},
    )
    flasher = make_flasher(fake_cli_script, instructions)

    outcome = run(flasher.detect_devices(detect_request()))

    assert sorted(device.port for device in outcome.devices) == ["COM7", "COM9"]


# --- flasher.py static safety ------------------------------------------------

FORBIDDEN_NAMES = frozenset(
    {
        "subprocess",
        "os",
        "pty",
        "system",
        "popen",
        "spawn",
        "execl",
        "execv",
        "execve",
        "eval",
        "exec",
        "compile",
        "shell",
        "socket",
        "importlib",
        "__import__",
    }
)


def _code_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    with tokenize.open(path) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.NAME:
                names.add(token.string)
    return names


def test_flasher_module_stays_safe_and_delegates_execution() -> None:
    # NAME tokens only — this deliberately does not scan comments/docstrings
    # as raw text, so prose *describing* the forbidden APIs (as this
    # module's own docstring does) cannot produce a false positive here.
    names = _code_names(FLASHER_PATH)

    # This module builds the argv and reads the result; `app/build/process.py`
    # is the only file allowed to spawn anything. So the *full* forbidden set
    # applies here with no exception at all — including "subprocess" itself,
    # the bare identifier "shell" that a literal `shell=True` keyword argument
    # would require, and "compile": this module uploads, it never builds.
    offenders = sorted(names & FORBIDDEN_NAMES)
    assert offenders == [], f"execution primitive in flasher.py: {offenders}"

    # The delegation is real, not vacuous: the module actually calls the
    # sanctioned primitive, and never the shell-string-taking asyncio API.
    assert "run_capture" in names
    assert "create_subprocess_shell" not in names


# --- optional: real arduino-cli, only when actually available --------------
#
# DEVICE DISCOVERY ONLY. There is deliberately no automated test here that
# uploads to a board: flashing is a physical, wearing operation on someone's
# hardware, and a test suite must not write firmware to whatever happens to
# be plugged into the machine running it. Real upload verification is done
# by hand through Build Mode with a known board attached. `board list` is
# read-only and safe to run unattended.

_REAL_ARDUINO_CLI = shutil.which(config.ARDUINO_CLI_PATH)

_SKIP_REASON = (
    f"arduino-cli not available via config.ARDUINO_CLI_PATH={config.ARDUINO_CLI_PATH!r}; "
    "set TRAINER_ARDUINO_CLI_PATH to a working arduino-cli to run this test"
)


@pytest.mark.skipif(_REAL_ARDUINO_CLI is None, reason=_SKIP_REASON)
def test_real_device_discovery_runs_and_answers_truthfully() -> None:
    """The real `board list` is readable, whether or not a board is attached.

    Asserts the *shape* of the answer, not its content: on a machine with no
    ESP32 connected this must be a successful discovery reporting zero
    devices — never an error, and never a fabricated device.
    """
    flasher = ArduinoCliFlasher(config.ARDUINO_CLI_PATH)

    outcome = run(
        flasher.detect_devices(
            DeviceDetectRequest(
                fqbn=FQBN,
                timeout_seconds=config.BUILD_DEVICE_DETECT_TIMEOUT_SECONDS,
            )
        )
    )

    assert outcome.ok is True, outcome.stderr
    assert outcome.category is FlashFailureCategory.NONE
    for device in outcome.devices:
        assert isinstance(device.port, str) and device.port
