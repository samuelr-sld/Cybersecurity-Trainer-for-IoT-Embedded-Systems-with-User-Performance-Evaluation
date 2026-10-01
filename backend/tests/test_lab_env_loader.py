"""The lab env bootstrap in app/config.py: file -> os.environ -> lab_secret.

Every test hands the loader a private file and a private environ dict, so none
depends on (or touches) the developer's real backend/lab.env.local.
"""

from __future__ import annotations

import logging

from app import config

GUEST = "TRAINER_LAB_PANEL1_GUEST_PASSWORD"
TOKEN = "TRAINER_LAB_PANEL1_COMMAND_TOKEN"


def write(tmp_path, text: str):
    path = tmp_path / "lab.env.local"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_lab_values_when_file_present(tmp_path) -> None:
    env: dict[str, str] = {}
    path = write(tmp_path, f"{GUEST}=g-secret\n{TOKEN} = 't-secret'\n")
    assert config.load_lab_env_file(path, env) == 2
    assert env == {GUEST: "g-secret", TOKEN: "t-secret"}


def test_existing_environment_wins(tmp_path) -> None:
    env = {GUEST: "from-deployment"}
    path = write(tmp_path, f"{GUEST}=from-file\n{TOKEN}=from-file\n")
    assert config.load_lab_env_file(path, env) == 1
    assert env[GUEST] == "from-deployment"
    assert env[TOKEN] == "from-file"


def test_missing_file_is_harmless(tmp_path) -> None:
    env: dict[str, str] = {}
    assert config.load_lab_env_file(tmp_path / "nope.env", env) == 0
    assert env == {}


def test_blank_and_comment_lines_and_junk_are_ignored(tmp_path) -> None:
    env: dict[str, str] = {}
    path = write(
        tmp_path,
        f"﻿# comment\n\n   \n   # indented\nno-equals-line\n=novalue\nexport {GUEST}=x\n",
    )
    assert config.load_lab_env_file(path, env) == 1
    assert env == {GUEST: "x"}


def test_only_lab_prefixed_names_are_imported(tmp_path) -> None:
    env: dict[str, str] = {}
    path = write(
        tmp_path,
        f"PATH=/evil\nAWS_SECRET_ACCESS_KEY=k\nTRAINER_HOST=0.0.0.0\n"
        f"TRAINER_LAB_BAD-NAME=x\n{TOKEN}=ok\n",
    )
    config.load_lab_env_file(path, env)
    assert env == {TOKEN: "ok"}


def test_values_are_never_logged_or_printed(tmp_path, caplog, capsys) -> None:
    env: dict[str, str] = {}
    path = write(tmp_path, f"{GUEST}=super-secret-value\n")
    with caplog.at_level(logging.DEBUG):
        config.load_lab_env_file(path, env)
    out = capsys.readouterr()
    assert "super-secret-value" not in caplog.text + out.out + out.err


def test_path_variable_override_and_disable(tmp_path) -> None:
    path = write(tmp_path, f"{GUEST}=x\n")
    env = {config.LAB_ENV_PATH_VARIABLE: str(path)}
    assert config.load_lab_env_file(None, env) == 1
    assert env[GUEST] == "x"
    disabled = {config.LAB_ENV_PATH_VARIABLE: ""}
    assert config.load_lab_env_file(None, disabled) == 0


def test_default_location_is_backend_dir_not_cwd() -> None:
    assert config.DEFAULT_LAB_ENV_FILE.name == "lab.env.local"
    assert config.DEFAULT_LAB_ENV_FILE.parent.name == "backend"


def test_lab_secret_semantics_unchanged(monkeypatch) -> None:
    monkeypatch.setenv(GUEST, "v")
    monkeypatch.setenv("PATH_LIKE_SECRET", "nope")
    assert config.lab_secret(GUEST) == "v"
    assert config.lab_secret("PATH_LIKE_SECRET") == ""
    assert config.lab_secret(None) == ""  # type: ignore[arg-type]
    monkeypatch.delenv(TOKEN, raising=False)
    assert config.lab_secret(TOKEN) == ""
