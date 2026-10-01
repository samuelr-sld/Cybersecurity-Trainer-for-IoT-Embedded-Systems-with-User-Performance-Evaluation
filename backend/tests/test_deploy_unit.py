"""The shipped systemd unit keeps the promises the deployment decisions made.

A static read of `deploy/cybertrainer-backend.service`: this cannot prove
systemd accepts it (`systemd-analyze verify` does, on the Pi), but it pins the
properties that must never drift silently — who it runs as, that it can reach
the serial port and the Arduino toolchain, that it restarts, and above all
that no secret lives in it and no file is *required*.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parents[2] / "deploy" / "cybertrainer-backend.service"


def _directives() -> dict[str, list[str]]:
    """`Key=value` lines (comments and section headers dropped), repeats kept."""
    found: dict[str, list[str]] = {}
    for line in UNIT.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text.startswith("["):
            continue
        key, _, value = text.partition("=")
        found.setdefault(key.strip(), []).append(value.strip())
    return found


@pytest.fixture(scope="module")
def unit() -> dict[str, list[str]]:
    assert UNIT.is_file(), f"missing {UNIT}"
    return _directives()


def test_it_runs_as_the_trainer_user_in_the_dialout_group(unit) -> None:
    assert unit["User"] == ["arvis"]
    assert "dialout" in " ".join(unit["SupplementaryGroups"]).split()


def test_home_is_set_so_arduino_cli_and_esptool_are_found(unit) -> None:
    assert "HOME=/home/arvis" in unit["Environment"]


def test_it_starts_the_backend_from_the_repo_venv_on_all_interfaces_port_8000(unit) -> None:
    (command,) = unit["ExecStart"]
    assert command.startswith("/home/arvis/Embedded-IoT-Cybersecurity-Trainer/backend/.venv/bin/python -m uvicorn ")
    assert "app.main:app" in command
    assert "--host 0.0.0.0" in command
    assert "--port 8000" in command
    assert "--reload" not in command
    assert unit["WorkingDirectory"] == ["/home/arvis/Embedded-IoT-Cybersecurity-Trainer/backend"]


def test_it_serves_the_prebuilt_frontend_from_a_configured_directory(unit) -> None:
    frontend = [v for v in unit["Environment"] if v.startswith("TRAINER_FRONTEND_DIST=")]
    assert len(frontend) == 1
    assert frontend[0].split("=", 1)[1].startswith("/home/arvis/")


def test_the_runtime_does_not_depend_on_the_offline_provisioning_bundle(unit) -> None:
    # The bundle is a one-time install artifact with a checksum manifest; the
    # running service must live entirely under the repository.
    directive_text = " ".join(f"{k}={v}" for k, vs in unit.items() for v in vs)
    assert "trainer-offline-bundle" not in directive_text
    (command,) = unit["ExecStart"]
    repo = "/home/arvis/Embedded-IoT-Cybersecurity-Trainer/"
    assert command.startswith(repo)
    assert unit["WorkingDirectory"][0].startswith(repo)
    (frontend,) = [v for v in unit["Environment"] if v.startswith("TRAINER_FRONTEND_DIST=")]
    assert frontend.split("=", 1)[1].startswith(repo)


def test_it_starts_after_the_access_point_and_the_broker(unit) -> None:
    # AP (network-online) -> Mosquitto -> backend. The broker is wanted, not
    # required: the backend must still start without it.
    after = " ".join(unit["After"]).split()
    assert "network-online.target" in after
    assert "mosquitto.service" in after
    assert "mosquitto.service" in " ".join(unit["Wants"]).split()
    assert "Requires" not in unit


def test_it_restarts_whenever_the_process_exits(unit) -> None:
    assert unit["Restart"] == ["always"]


def test_every_environment_file_is_optional(unit) -> None:
    # A leading "-" makes a missing file a no-op; without it a not-yet-
    # provisioned lab would stop the service from starting at all.
    files = unit["EnvironmentFile"]
    assert files, "an environment file mechanism must exist for TRAINER_LAB_* values"
    assert all(path.startswith("-/") for path in files)


def test_it_does_not_depend_on_lab_env_local(unit) -> None:
    text = UNIT.read_text(encoding="utf-8")
    directive_lines = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any("lab.env.local" in line for line in directive_lines)


def test_no_secret_value_is_written_into_the_unit(unit) -> None:
    for key, values in unit.items():
        for value in values:
            if key == "Environment":
                name = value.split("=", 1)[0]
                assert not name.startswith("TRAINER_LAB_"), "lab secrets must not be inline"
                assert not re.search(r"(PASSWORD|TOKEN|SECRET)", name, re.IGNORECASE)
    assert "TRAINER_LAB_" not in " ".join(f"{k}={v}" for k, vs in unit.items() for v in vs)
