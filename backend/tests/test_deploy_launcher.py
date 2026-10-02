"""The Raspberry Pi desktop launcher keeps the promises the deployment decisions made.

`deploy/cybertrainer-launcher.sh` is what a tap on the "Cybersecurity Trainer"
icon runs. These tests execute the REAL script under a POSIX `sh` against fake
`systemctl` / `sudo` / `curl` / `sleep` / browser executables placed first on
PATH, so they prove behaviour (what it starts, what it never touches, what it
opens) without a Pi, a service or a display. Static tests pin what must never
appear in it, and the installer is run against a throw-away HOME.

They cannot prove how pcmanfm or a real browser react (that was checked on the
Pi itself); they pin everything in the repository's own hands.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
LAUNCHER = DEPLOY / "cybertrainer-launcher.sh"
INSTALLER = DEPLOY / "install-desktop-launcher.sh"
TEMPLATE = DEPLOY / "cybertrainer.desktop.in"

SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(SH is None, reason="needs a POSIX sh")

PAGE = "http://localhost:8000/"
SERVICE = "cybertrainer-backend.service"


def _text(path: Path) -> str:
    # A Windows checkout may hold CRLF; the scripts are LF in the index.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# --- static: what the shipped files must (not) contain --------------------------------


def test_the_launcher_targets_localhost_and_never_the_home_network_or_the_internet() -> None:
    text = _text(LAUNCHER)
    assert f"CYBERTRAINER_URL:-{PAGE}" in text
    assert "192.168." not in text, "the production address is localhost; no LAN address belongs here"
    hosts = {m.group(1) for m in re.finditer(r"https?://([^/\s'\"}]+)", text)}
    assert hosts == {"localhost:8000"}, hosts


def test_the_launcher_never_starts_a_second_server_or_disturbs_the_service() -> None:
    code = "\n".join(line for line in _text(LAUNCHER).splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("uvicorn", "python -m", "fastapi", "nohup", "systemctl restart", "systemctl stop",
                      "systemctl reload", "systemctl enable", "systemctl disable", "daemon-reload"):
        assert forbidden not in code, forbidden
    # The one thing it may do to the service is start it, never block on the call.
    starts = re.findall(r"systemctl start[^\n]*", code)
    assert starts and all("--no-block" in s for s in starts), starts


def test_the_launcher_opens_a_normal_window_never_kiosk_or_forced_fullscreen() -> None:
    code = "\n".join(line for line in _text(LAUNCHER).splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("--kiosk", "--start-fullscreen", "--start-maximized", "--app=", "-kiosk"):
        assert forbidden not in code, forbidden


def test_nothing_in_deploy_makes_the_browser_start_at_boot() -> None:
    for path in (LAUNCHER, INSTALLER, TEMPLATE):
        text = _text(path)
        assert "autostart" not in text.lower(), path.name
        assert "X-GNOME-Autostart" not in text, path.name


def test_the_desktop_entry_template_is_the_requested_shortcut() -> None:
    entries = dict(
        line.split("=", 1) for line in _text(TEMPLATE).splitlines() if "=" in line and not line.startswith("#")
    )
    assert entries["Name"] == "Cybersecurity Trainer"
    assert entries["Type"] == "Application"
    assert entries["Terminal"] == "false"
    assert entries["Exec"] == 'sh "@LAUNCHER@"', "no executable bit needed, and the path is quoted"
    assert entries["Icon"] == "@ICON@"


def test_the_installed_icon_exists_in_the_repository() -> None:
    assert (DEPLOY.parent / "public" / "favicon.svg").is_file()


# --- behaviour: the real launcher against fakes -----------------------------------------


class Sandbox:
    """A throw-away machine: fake commands first on PATH, a HOME of its own."""

    def __init__(self, root: Path, *, state: str = "active", ready_after: int | None = 0,
                 browsers: tuple[str, ...] = ("chromium",), default_browser: str = "chromium.desktop",
                 passwordless_sudo: bool = True, start_fails: bool = False) -> None:
        self.root = root
        self.bin = root / "bin"
        self.home = root / "home"
        self.calls = root / "calls.log"
        self.bin.mkdir()
        self.home.mkdir()
        self.calls.write_text("")
        (root / "state").write_text(state + "\n")
        (root / "health").write_text("")
        self.ready_after = ready_after
        self.script = root / "launcher.sh"
        self.script.write_text(_text(LAUNCHER), encoding="utf-8", newline="\n")

        self._tool("systemctl", """
            echo "systemctl $*" >> "$CT_CALLS"
            case "$1" in
              is-active) cat "$CT_ROOT/state" ;;
              start) if [ -f "$CT_ROOT/start_fails" ]; then echo failed > "$CT_ROOT/state"; else echo active > "$CT_ROOT/state"; fi ;;
            esac
        """)
        if start_fails:
            (root / "start_fails").write_text("")
        self._tool("sudo", """
            echo "sudo $*" >> "$CT_CALLS"
            [ -f "$CT_ROOT/no_sudo" ] && exit 1
            [ "$1" = -n ] && shift
            [ "$1" = true ] && exit 0
            exec "$@"
        """)
        if not passwordless_sudo:
            (root / "no_sudo").write_text("")
        self._tool("curl", """
            echo "curl $*" >> "$CT_CALLS"
            for last; do :; done
            case "$last" in
              */health) [ -s "$CT_ROOT/health" ] ;;
              *) [ -f "$CT_ROOT/ready" ] ;;
            esac
        """)
        self._tool("sleep", """
            echo "sleep $*" >> "$CT_CALLS"
            n=$(cat "$CT_ROOT/sleeps" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$CT_ROOT/sleeps"
            if [ -n "$CT_READY_AFTER" ] && [ "$n" -ge "$CT_READY_AFTER" ]; then : > "$CT_ROOT/ready"; fi
        """)
        self._tool("xdg-settings", 'echo "$CT_DEFAULT_BROWSER"')
        for name in browsers:
            self._tool(name, f'echo "{name} $*" >> "$CT_CALLS"')
        if ready_after == 0:
            (root / "ready").write_text("")
        self.default_browser = default_browser

    def _tool(self, name: str, body: str) -> None:
        path = self.bin / name
        lines = "\n".join(line.strip() for line in body.strip().splitlines())
        path.write_text(f"#!/bin/sh\n{lines}\n", encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _path(self) -> str:
        # Only the fakes plus the few real utilities the script itself uses, so a
        # real chromium / systemctl / zenity on the machine running the tests
        # can never be picked up.
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        for name in ("date", "mkdir", "cat", "touch"):
            real = shutil.which(name)
            if real is None:
                continue
            try:
                os.symlink(real, tools / name)
            except FileExistsError:
                pass  # a second run in the same sandbox
            except (OSError, NotImplementedError):  # no symlinks (Windows): use the shell's own dir
                return os.pathsep.join([str(self.bin), str(Path(SH).parent)])
        return os.pathsep.join([str(self.bin), str(tools)])

    def run(self, **env: str) -> subprocess.CompletedProcess[str]:
        full = {
            "PATH": self._path(),
            "HOME": str(self.home),
            "CT_ROOT": str(self.root),
            "CT_CALLS": str(self.calls),
            "CT_DEFAULT_BROWSER": self.default_browser,
            "CT_READY_AFTER": "" if self.ready_after in (None, 0) else str(self.ready_after),
            "CYBERTRAINER_WAIT_SECONDS": "5",
        }
        if os.name == "nt":
            full["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        full.update(env)
        return subprocess.run([SH, str(self.script)], env=full, capture_output=True, text=True, timeout=60)

    def log(self) -> list[str]:
        return [line for line in self.calls.read_text().splitlines() if line]

    def service_calls(self) -> list[str]:
        return [line for line in self.log() if line.startswith("systemctl ") and "is-active" not in line]


@pytest.fixture
def sandbox(tmp_path):
    def make(**kwargs) -> Sandbox:
        return Sandbox(tmp_path, **kwargs)

    return make


@needs_sh
def test_a_running_service_is_left_alone_and_the_page_opens_in_the_browser(sandbox) -> None:
    box = sandbox(state="active")
    done = box.run()
    assert done.returncode == 0, done.stderr
    assert box.service_calls() == [], "an active service must not be started, stopped or restarted"
    assert not any(line.startswith("sudo") for line in box.log()), "no privileges needed when nothing starts"
    assert box.log()[-1] == f"chromium --password-store=basic --new-window {PAGE}"
    assert not any(line.startswith("sleep") for line in box.log()), "an instant answer needs no wait"


@needs_sh
def test_a_stopped_service_is_started_once_and_waited_for_before_the_browser_opens(sandbox) -> None:
    box = sandbox(state="inactive", ready_after=3)
    done = box.run()
    assert done.returncode == 0, done.stderr
    assert box.service_calls() == [f"systemctl start --no-block {SERVICE}"]
    assert f"sudo -n systemctl start --no-block {SERVICE}" in box.log(), "passwordless sudo is preferred"
    log = box.log()
    sleeps = [i for i, line in enumerate(log) if line.startswith("sleep")]
    opened = [i for i, line in enumerate(log) if line.startswith("chromium")]
    assert len(sleeps) == 3 and len(opened) == 1
    assert opened[0] > sleeps[-1], "the browser opens only after the page answered"


@needs_sh
def test_a_service_that_is_still_starting_is_not_started_again(sandbox) -> None:
    box = sandbox(state="activating", ready_after=2)
    assert box.run().returncode == 0
    assert box.service_calls() == []


@needs_sh
def test_without_passwordless_sudo_it_asks_systemctl_directly(sandbox) -> None:
    box = sandbox(state="inactive", ready_after=1, passwordless_sudo=False)
    assert box.run().returncode == 0
    assert box.service_calls() == [f"systemctl start --no-block {SERVICE}"]
    assert f"sudo -n systemctl start --no-block {SERVICE}" not in box.log()


@needs_sh
def test_a_page_that_never_answers_ends_with_an_error_and_no_browser(sandbox) -> None:
    box = sandbox(state="active", ready_after=None)
    done = box.run(CYBERTRAINER_WAIT_SECONDS="3")
    assert done.returncode != 0
    assert "did not answer" in done.stderr
    assert not any(line.startswith("chromium") for line in box.log())
    assert box.service_calls() == [], "an unresponsive but active service is reported, never restarted"


@needs_sh
def test_a_running_backend_without_a_served_screen_is_named_as_such(sandbox) -> None:
    box = sandbox(state="active", ready_after=None)
    (box.root / "health").write_text("ok")
    done = box.run(CYBERTRAINER_WAIT_SECONDS="2")
    assert done.returncode != 0
    assert "not being served" in done.stderr


@needs_sh
def test_a_service_that_fails_to_start_fails_fast(sandbox) -> None:
    box = sandbox(state="inactive", ready_after=None, start_fails=True)
    done = box.run(CYBERTRAINER_WAIT_SECONDS="30")
    assert done.returncode != 0
    assert "failed to start" in done.stderr
    assert sum(line.startswith("sleep") for line in box.log()) <= 1, "it must not wait out the timeout"


@needs_sh
def test_the_desktop_default_browser_is_used_when_installed(sandbox) -> None:
    box = sandbox(browsers=("chromium", "firefox"), default_browser="firefox.desktop")
    assert box.run().returncode == 0
    assert box.log()[-1] == f"firefox --new-window {PAGE}", "firefox gets no Chromium-only flag"


@needs_sh
def test_a_missing_default_falls_back_to_the_first_installed_known_browser(sandbox) -> None:
    box = sandbox(browsers=("firefox",), default_browser="chromium.desktop")
    assert box.run().returncode == 0
    assert box.log()[-1] == f"firefox --new-window {PAGE}"


@needs_sh
def test_an_unfamiliar_browser_is_just_given_the_address(sandbox) -> None:
    box = sandbox(browsers=("epiphany-browser",), default_browser="")
    assert box.run().returncode == 0
    assert box.log()[-1] == f"epiphany-browser {PAGE}"


@needs_sh
def test_no_browser_at_all_is_an_error_not_a_guess(sandbox) -> None:
    box = sandbox(browsers=(), default_browser="")
    done = box.run()
    assert done.returncode != 0
    assert "No web browser" in done.stderr


@needs_sh
def test_the_browser_can_be_overridden_for_troubleshooting(sandbox) -> None:
    box = sandbox(browsers=("chromium", "firefox"))
    assert box.run(CYBERTRAINER_BROWSER="firefox").returncode == 0
    assert box.log()[-1].startswith("firefox ")


# --- the installer ---------------------------------------------------------------------


def _install(home: Path, *args: str, path_extra: Path | None = None) -> subprocess.CompletedProcess[str]:
    # Only the utilities the installer itself uses, so a real pcmanfm / gio /
    # desktop-file-validate on the machine running the tests is never picked up;
    # a fake `pcmanfm` is added by the test that wants one.
    tools = home.parent / f"{home.name}-tools"
    tools.mkdir(exist_ok=True)
    parts = [str(tools)]
    for name in ("dirname", "rm", "mkdir", "sed", "mv", "chmod", "grep", "cat"):
        try:
            os.symlink(shutil.which(name), tools / name)
        except FileExistsError:
            pass  # the installer is run more than once against the same home
        except (OSError, NotImplementedError, TypeError):  # no symlinks (Windows): use the shell's own dir
            parts = [str(Path(SH).parent)]
            break
    if path_extra is not None:
        parts.insert(0, str(path_extra))
    env = {"PATH": os.pathsep.join(parts), "HOME": str(home)}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    return subprocess.run([SH, str(INSTALLER), *args], env=env, capture_output=True, text=True, timeout=60)


def _files_under(home: Path) -> set[str]:
    return {str(p.relative_to(home)).replace("\\", "/") for p in home.rglob("*") if p.is_file()}


@needs_sh
def test_the_installer_writes_the_desktop_icon_and_menu_entry_with_this_checkouts_paths(tmp_path) -> None:
    done = _install(tmp_path)
    assert done.returncode == 0, done.stderr
    desktop = tmp_path / "Desktop" / "cybertrainer.desktop"
    menu = tmp_path / ".local" / "share" / "applications" / "cybertrainer.desktop"
    assert _files_under(tmp_path) == {"Desktop/cybertrainer.desktop", ".local/share/applications/cybertrainer.desktop"}
    text = desktop.read_text()
    assert not any("@" in line for line in text.splitlines() if not line.startswith("#")), "every placeholder is filled in"
    assert text == menu.read_text()
    exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))
    assert exec_line.endswith('deploy/cybertrainer-launcher.sh"') and exec_line.startswith('Exec=sh "')
    icon = next(line for line in text.splitlines() if line.startswith("Icon="))
    assert icon.endswith("public/favicon.svg")
    assert "localhost" not in text and "192.168" not in text, "the entry only names the launcher"


@needs_sh
def test_the_installer_can_be_run_again_and_uninstalls_cleanly(tmp_path) -> None:
    assert _install(tmp_path).returncode == 0
    before = {p: (tmp_path / p).read_text() for p in _files_under(tmp_path)}
    assert _install(tmp_path).returncode == 0
    assert {p: (tmp_path / p).read_text() for p in _files_under(tmp_path)} == before
    assert _install(tmp_path, "--uninstall").returncode == 0
    assert _files_under(tmp_path) == set()


@needs_sh
def test_the_installer_rejects_unknown_arguments(tmp_path) -> None:
    assert _install(tmp_path, "--bogus").returncode == 2
    assert _files_under(tmp_path) == set()


def _fake_pcmanfm(root: Path) -> Path:
    bin_dir = root / "fakebin"
    bin_dir.mkdir()
    exe = bin_dir / "pcmanfm"
    exe.write_text("#!/bin/sh\nexit 0\n", newline="\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return bin_dir


@needs_sh
def test_with_the_pi_file_manager_it_turns_on_the_one_preference_that_removes_the_execute_prompt(tmp_path) -> None:
    home = tmp_path / "home"
    (home / ".config" / "libfm").mkdir(parents=True)
    conf = home / ".config" / "libfm" / "libfm.conf"
    conf.write_text("[config]\nsingle_click=0\nquick_exec=0\nterminal=x-terminal-emulator %s\n\n[ui]\nbig_icon_size=48\n",
                    newline="\n")
    done = _install(home, path_extra=_fake_pcmanfm(tmp_path))
    assert done.returncode == 0, done.stderr
    assert conf.read_text() == "[config]\nsingle_click=0\nquick_exec=1\nterminal=x-terminal-emulator %s\n\n[ui]\nbig_icon_size=48\n"
    assert "next time the desktop starts" in done.stdout


@needs_sh
def test_the_preference_is_added_when_absent_and_the_file_created_when_missing(tmp_path) -> None:
    home = tmp_path / "home"
    (home / ".config" / "libfm").mkdir(parents=True)
    conf = home / ".config" / "libfm" / "libfm.conf"
    conf.write_text("[config]\nsingle_click=0\n[ui]\nbig_icon_size=48\n", newline="\n")
    assert _install(home, path_extra=_fake_pcmanfm(tmp_path)).returncode == 0
    assert conf.read_text() == "[config]\nquick_exec=1\nsingle_click=0\n[ui]\nbig_icon_size=48\n"

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert _install(fresh, path_extra=tmp_path / "fakebin").returncode == 0
    assert (fresh / ".config" / "libfm" / "libfm.conf").read_text() == "[config]\nquick_exec=1\n"


@needs_sh
def test_without_that_file_manager_no_preference_file_is_written(tmp_path) -> None:
    assert _install(tmp_path).returncode == 0
    assert not (tmp_path / ".config").exists()


# --- the touchscreen keyboard setup ------------------------------------------------------

TOUCH = DEPLOY / "install-touch-keyboard.sh"

# The exact settings the script applies, in order (schema key value).
TOUCH_SETTINGS = [
    "org.gnome.desktop.interface toolkit-accessibility true",
    "org.onboard.auto-show enabled true",
    "org.onboard.icon-palette in-use true",
    "org.onboard.window force-to-top true",
    "org.onboard start-minimized true",
    "org.onboard show-status-icon false",
]


def test_the_keyboard_setup_installs_nothing_and_needs_no_privileges() -> None:
    code = [line.strip() for line in _text(TOUCH).splitlines()
            if line.strip() and not line.lstrip().startswith("#") and not line.lstrip().startswith("echo")]
    for line in code:
        assert not re.match(r"(sudo|apt|apt-get|dpkg|pip|npm|curl|wget)\b", line), line
    assert "http" not in "\n".join(code)


def _touch(tmp_path: Path, *args: str, onboard: bool = True, schema: bool = True) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the setup against fake gsettings/onboard; returns (result, gsettings call log)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "gsettings.log"
    calls.touch()

    def tool(name: str, body: str) -> None:
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\n{body}\n", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    tool("gsettings", f'''
        case "$1" in
          list-schemas) {'echo org.onboard; echo org.gnome.desktop.interface' if schema else 'echo org.gnome.desktop.interface'} ;;
          *) echo "$*" >> "{calls}" ;;
        esac''')
    if onboard:
        tool("onboard", "exit 0")

    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    parts = [str(bin_dir), str(tools)]
    for name in ("id", "grep", "cat", "mkdir", "mv", "rm"):
        try:
            os.symlink(shutil.which(name), tools / name)
        except FileExistsError:
            pass
        except (OSError, NotImplementedError, TypeError):  # no symlinks (Windows): use the shell's own dir
            parts = [str(bin_dir), str(Path(SH).parent)]
            break
    env = {"PATH": os.pathsep.join(parts), "HOME": str(home)}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    done = subprocess.run([SH, str(TOUCH), *args], env=env, capture_output=True, text=True, timeout=60)
    return done, calls


@needs_sh
def test_the_keyboard_setup_applies_exactly_these_settings_and_autostarts_onboard(tmp_path) -> None:
    done, calls = _touch(tmp_path)
    assert done.returncode == 0, done.stderr
    assert calls.read_text().splitlines() == [f"set {line.split()[0]} {line.split()[1]} {line.split()[2]}" for line in TOUCH_SETTINGS]
    entry = tmp_path / "home" / ".config" / "autostart" / "cybertrainer-onboard.desktop"
    assert entry.read_text() == (
        "[Desktop Entry]\nType=Application\nName=Onboard (touch keyboard)\n"
        "Comment=On-screen keyboard for the touchscreen\nExec=onboard --startup-delay=3\nNoDisplay=true\n"
    )
    assert "OnlyShowIn" not in entry.read_text(), "the packaged entry only applies to Unity/MATE; this one must run here"
    assert _files_under(tmp_path / "home") == {".config/autostart/cybertrainer-onboard.desktop"}


@needs_sh
def test_the_keyboard_setup_is_repeatable_and_reversible(tmp_path) -> None:
    assert _touch(tmp_path)[0].returncode == 0
    entry = tmp_path / "home" / ".config" / "autostart" / "cybertrainer-onboard.desktop"
    first = entry.read_text()
    assert _touch(tmp_path)[0].returncode == 0
    assert entry.read_text() == first
    done, calls = _touch(tmp_path, "--uninstall")
    assert done.returncode == 0, done.stderr
    assert not entry.exists()
    assert calls.read_text().splitlines()[-len(TOUCH_SETTINGS):] == [f"reset {line.split()[0]} {line.split()[1]}" for line in TOUCH_SETTINGS]


@needs_sh
def test_without_onboard_it_says_how_to_install_it_and_changes_nothing(tmp_path) -> None:
    done, calls = _touch(tmp_path, onboard=False)
    assert done.returncode == 1
    assert "sudo apt install onboard at-spi2-core" in done.stderr
    assert calls.read_text() == ""
    assert _files_under(tmp_path / "home") == set()


@needs_sh
def test_without_onboards_settings_schema_it_changes_nothing(tmp_path) -> None:
    done, calls = _touch(tmp_path, schema=False)
    assert done.returncode == 1
    assert "onboard-common" in done.stderr
    assert calls.read_text() == ""
    assert _files_under(tmp_path / "home") == set()


@needs_sh
def test_the_keyboard_setup_rejects_unknown_arguments(tmp_path) -> None:
    assert _touch(tmp_path, "--bogus")[0].returncode == 2
