"""`DeviceMonitor` — the one thing that knows which ESP32 is attached.

Position in the architecture:

    Build Mode ---+
                  +--> DeviceMonitor --> FlasherAdapter.detect_devices
    Hack Mode ----+      (this module)     (app/build/flasher.py)
                                                    |
                                          arduino-cli board list --format json
                                                    |
                                                  ESP32

WHY THIS EXISTS. Before Phase 1 every Build Mode *session* ran its own
`arduino-cli board list` and stored its own answer, and Hack Mode had no
answer at all. There is only one physical ESP32 plugged into the machine, so
per-session detection was both duplicated work and a chance for two views of
one board to disagree. This module makes the device a process-wide fact that
both modes read, and neither owns.

REUSE, NOT REIMPLEMENTATION. There is no Arduino CLI knowledge in this file:
no command line, no JSON schema, no port-selection ladder. All of that stays
in `app/build/flasher.py`, which already does it correctly, and this module
reaches it through the *narrow* `DeviceDetector` protocol below — the
`detect_devices` half of the existing `FlasherAdapter`, with `run_flash`
deliberately out of view. The shared layer can therefore ask what is plugged
in, and is structurally incapable of uploading anything.

CONCURRENCY. Two invariants make this safe for both modes to consume at once
without either blocking on the other's reads:

1. `snapshot()` never awaits and never locks. `_state` holds a frozen
   `DeviceState` that is only ever *replaced*, so a reader gets a complete,
   self-consistent state — never a stale port beside a fresh board name.
2. `_lock` guards only the expensive part: deciding to run a detection and
   running it. A burst of concurrent `refresh()` calls therefore produces
   ONE `arduino-cli board list`; the callers that queue behind it find the
   result already cached and return it, rather than each spawning their own.

That second point is what keeps polling honest. Build Mode and Hack Mode
both poll on the same interval, and every open session polls independently;
without coalescing, N sessions would mean N CLI invocations every tick for
one board. `cache_seconds` is deliberately much shorter than that poll
interval, so a *single* poller still gets genuinely fresh hardware on every
tick — the cache removes duplicate work, it does not slow detection down.

NO EVENTS, NO LOGS, NO OUTPUT. This module returns state and nothing else.
It emits no `BuildEvent`, no `ScenarioEvent`, writes nothing to a terminal,
and touches no scenario. Hardware presence is infrastructure state: a board
being plugged in is not something a student *did*, so it must never appear
in an Activity Log, an exploit event, or the Hack Mode terminal. See
`app/websocket.py`'s `hardware_status` handler, which is built on that rule.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from app import config
from app.hardware.identity import IdentityProbe, IdentityRequest, default_identity_probe
from app.hardware.panels import panel_for
from app.hardware.serial_alias import serial_representations
from app.hardware.state import DeviceState, DeviceStatus

if TYPE_CHECKING:  # pragma: no cover
    from app.build.flasher import DeviceDetectOutcome


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceDetector(Protocol):
    """The only capability the shared layer needs from the Arduino CLI.

    Structurally satisfied by `app.build.flasher.ArduinoCliFlasher` (and by
    every `FlasherAdapter` test double) without either side importing the
    other, which is what lets this package stay free of build-layer
    dependencies at import time. `run_flash` is intentionally absent: the
    shared layer reports what is connected, it never writes firmware.
    """

    async def detect_devices(self, request: Any) -> "DeviceDetectOutcome": ...


def _detection_api():
    """Import the real Arduino CLI detection adapter, lazily.

    DELIBERATELY NOT A MODULE-LEVEL IMPORT. `app/build/models.py` aliases
    this package's `DeviceStatus` as `HardwareStatus`, so `app.build` is
    imported *by* the shared layer's consumers and a top-level
    `from app.build.flasher import ...` here would close an import cycle
    (app.build.models -> app.hardware -> app.build.flasher -> app.build).
    Deferring it to call time keeps the cycle open and keeps this package
    importable entirely on its own, which `state.py`'s docstring depends on.

    The cost is one `sys.modules` lookup per detection, next to a real
    subprocess — immeasurable.
    """
    from app.build.flasher import DeviceDetectRequest, default_flasher

    return DeviceDetectRequest, default_flasher


class DeviceMonitor:
    """Owns the current shared `DeviceState` for one process.

    `detector` defaults to the real `ArduinoCliFlasher` (resolved lazily,
    see `_detection_api`), but is injectable so a test — or a `BuildService`
    that was handed its own flasher double — drives detection without a real
    toolchain or a physical board.

    `cache_seconds` is how long a completed detection is reused instead of
    re-running the CLI. Zero disables reuse entirely, which is the right
    default for a privately-injected detector: there are no other sessions
    to deduplicate against, and a cache would only hide the caller's own
    changes to that double.
    """

    def __init__(
        self,
        detector: DeviceDetector | None = None,
        *,
        target_fqbn: str = config.HARDWARE_TARGET_FQBN,
        cache_seconds: float = 0.0,
        timeout_seconds: float = config.BUILD_DEVICE_DETECT_TIMEOUT_SECONDS,
        identity_probe: IdentityProbe | None = None,
        identity_timeout_seconds: float = config.HARDWARE_IDENTITY_TIMEOUT_SECONDS,
    ) -> None:
        self._detector = detector
        self._target_fqbn = target_fqbn
        self._cache_seconds = cache_seconds
        self._timeout_seconds = timeout_seconds
        self._identity_probe = (
            identity_probe if identity_probe is not None else default_identity_probe
        )
        self._identity_timeout_seconds = identity_timeout_seconds
        #: MAC per port, so a board is probed once per plug-in rather than
        #: once per poll — the difference between resetting the ESP32 every
        #: few seconds and resetting it when it appears. Cleared whenever
        #: the device goes away, so an unplug cannot leave a stale identity
        #: attached to a port a different board might later occupy.
        self._mac_by_port: dict[str, str] = {}
        #: Non-zero while something else owns the serial port (a flash).
        self._probe_holds = 0
        self._state = DeviceState()
        self._lock = asyncio.Lock()

    # --- reading -----------------------------------------------------------

    def snapshot(self) -> DeviceState:
        """The current shared state. No I/O, no awaiting, no locking.

        Safe to call from anywhere at any time — see the module docstring's
        first concurrency invariant. Returns whatever the last completed
        detection (or `publish`) established, which is `DeviceState()` with
        status NOT_CHECKED until something has actually looked.
        """
        return self._state

    @property
    def target_fqbn(self) -> str:
        """The platform's default board target, used when a caller names none."""
        return self._target_fqbn

    # --- writing -----------------------------------------------------------

    async def refresh(
        self,
        *,
        fqbn: str | None = None,
        max_age_seconds: float | None = None,
    ) -> DeviceState:
        """Re-detect the attached ESP32 and publish the result.

        `fqbn` is the board target detection should *prefer* (never a
        filter — see `flasher.py::_select_candidates`). Build Mode passes its
        session's own project board so its behaviour is unchanged; Hack Mode
        passes nothing and gets `config.HARDWARE_TARGET_FQBN`.

        Returns the fresh `DeviceState` and never raises: a missing
        toolchain, a wedged CLI, or an empty port list are all *answers*,
        recorded as ERROR/DISCONNECTED respectively, not exceptions for a
        caller to handle. A polling caller can therefore call this forever
        without a try/except around it.

        A cached result is reused only when it is younger than
        `max_age_seconds` (default `cache_seconds`) AND was detected against
        the same board platform, so a Build Mode check against one project's
        FQBN can never be silently served by a detection run for a different
        platform.
        """
        target = fqbn or self._target_fqbn
        max_age = self._cache_seconds if max_age_seconds is None else max_age_seconds

        async with self._lock:
            cached = self._reusable(target, max_age)
            if cached is not None:
                return cached

            # Publish DETECTING before awaiting, so a concurrent `snapshot()`
            # reader sees "a check is in flight" rather than a stale verdict.
            # `replace` keeps the previous port/board visible meanwhile: a
            # board does not stop being plugged in because we are re-asking.
            self._state = replace(self._state, status=DeviceStatus.DETECTING)

            request_type, fallback_detector = _detection_api()
            detector = self._detector if self._detector is not None else fallback_detector
            outcome = await detector.detect_devices(
                request_type(fqbn=target, timeout_seconds=self._timeout_seconds)
            )
            state = self.publish(outcome, fqbn=target)
            # Identity is resolved only here, never in `publish`: reading a
            # MAC drives the board into its bootloader and holds the port,
            # which must never happen as a side effect of someone else's
            # already-completed detection (a flash's, in particular).
            return await self._resolve_identity(state)

    def publish(self, outcome: "DeviceDetectOutcome", *, fqbn: str) -> DeviceState:
        """Fold one already-completed detection into the shared state.

        The second entry point, and the reason Build Mode's flash pipeline
        did not have to change: `BuildService.flash_workspace` already runs
        its own discovery before every upload, and that result is the
        freshest hardware information in the process. Handing it here means
        the shared state learns from a flash *without a second
        `arduino-cli board list`* — the alternative would be spending an
        extra CLI invocation to re-learn something we just found out.

        Synchronous on purpose: it performs no I/O, and assigning `_state`
        is atomic under asyncio (no await point inside), so it needs none of
        `_lock`'s protection and can be called from anywhere, including from
        inside code already doing something else with the device.

        NEVER PROBES. Identity is attached only from the MAC already cached
        for this port, so a flash's own discovery keeps the panel name on
        screen without this call ever opening the serial port — which it
        must not, since the caller is usually about to upload through it.
        An unknown port simply stays unidentified until the next `refresh`.
        """
        state = _state_from_outcome(outcome, fqbn=fqbn)
        if state.connected and state.port:
            mac = self._mac_by_port.get(state.port)
            if mac is not None:
                state = replace(state, mac=mac, panel=panel_for(mac))
        else:
            self._mac_by_port.clear()
        self._state = state
        return self._state

    @contextmanager
    def hold_identity_probe(self):
        """Suppress MAC probing while something else owns the serial port.

        THE HAZARD THIS CLOSES. `esptool read_mac` and `arduino-cli upload`
        both need exclusive control of the port, and both begin by driving
        the board into its bootloader. If a poll probed identity while a
        flash was uploading, one of the two would fail — and the one that
        matters is the firmware write. `BuildService.flash_workspace` wraps
        its upload in this, so a probe cannot start mid-flash.

        Re-entrant by counting rather than a boolean: nested or overlapping
        holds must not have the inner one release the outer one's claim.
        Detection itself is *not* suppressed — `arduino-cli board list` only
        enumerates ports and never opens the device — so the header keeps
        updating during a flash; only identity probing pauses.
        """
        self._probe_holds += 1
        try:
            yield
        finally:
            self._probe_holds -= 1

    def reset(self) -> None:
        """Forget everything detected so far. Used by tests, never in serving.

        Returns the monitor to its NOT_CHECKED starting point so one test's
        detection cannot leak into the next through the freshness cache, and
        drops every cached MAC so a fake board's identity cannot outlive it.
        """
        self._state = DeviceState()
        self._mac_by_port.clear()

    async def _resolve_identity(self, state: DeviceState) -> DeviceState:
        """Attach MAC and panel name to a freshly detected state.

        Order of preference, cheapest first:

        1. Nothing connected -> forget every cached MAC and return as-is.
           Requirement: identity must not survive a confirmed disconnect,
           or the header would keep naming a panel that has been unplugged.
        2. A MAC already cached for this port -> reuse it. This is the
           common path and the reason a 10-second poll does not reset the
           board every 10 seconds.
        3. A probe is held off (a flash owns the port) -> stay unidentified
           for now and try again on the next poll.
        4. Otherwise probe for real, and cache whatever comes back.

        A failed probe is never escalated: the board stays CONNECTED and
        simply has no panel identity, because detection — not esptool —
        is what decides whether something is plugged in.
        """
        if not state.connected or not state.port:
            self._mac_by_port.clear()
            return state

        port = state.port
        mac = self._mac_by_port.get(port)

        if mac is None and self._probe_holds == 0:
            outcome = await self._identity_probe.read_mac(
                IdentityRequest(
                    port=port, timeout_seconds=self._identity_timeout_seconds
                )
            )
            mac = outcome.mac
            if mac is not None:
                self._mac_by_port[port] = mac
            # A board that is no longer the one we detected (unplugged
            # during the probe) must not be handed a stale identity.
            if self._state.port != port:
                return self._state

        identified = replace(state, mac=mac, panel=panel_for(mac))
        self._state = identified
        return identified

    # --- internals ---------------------------------------------------------

    def _reusable(self, target_fqbn: str, max_age_seconds: float) -> DeviceState | None:
        """A recent-enough detection for this target, or None to go and look."""
        if max_age_seconds <= 0:
            return None
        state = self._state
        if state.checked_at is None or state.status is DeviceStatus.DETECTING:
            return None
        if _platform_of(state.fqbn) != _platform_of(target_fqbn):
            return None
        age = (_utc_now() - state.checked_at).total_seconds()
        return state if 0 <= age < max_age_seconds else None


def _platform_of(fqbn: str | None) -> str | None:
    """The `vendor:arch` prefix of an FQBN — `esp32:esp32:esp32` -> `esp32:esp32`.

    Same rule `flasher.py::_platform_of` applies when preferring candidates;
    duplicated here as three lines rather than imported, because importing
    it would reintroduce the load-time `app.build` dependency `state.py`'s
    docstring explains this package must not have.
    """
    if not fqbn:
        return None
    parts = fqbn.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else fqbn


def _state_from_outcome(outcome: "DeviceDetectOutcome", *, fqbn: str) -> DeviceState:
    """Map one `DeviceDetectOutcome` onto the shared vocabulary.

    The single place that decides what a detection *means*, so a standalone
    poll and a flash's own discovery can never disagree about whether a
    board is connected. The ordering of the branches is the discipline:
    "discovery failed" is checked before "found nothing", because an
    environment problem must never be reported as an unplugged board.

    Never guesses a port or a board for anything but the single-device case
    — the same refusal-to-guess `flash_workspace` applies before uploading.
    """
    checked_at = _utc_now()

    if not outcome.ok:
        return DeviceState(
            status=DeviceStatus.ERROR,
            fqbn=fqbn,
            detail=outcome.stderr or "device detection could not run",
            checked_at=checked_at,
        )

    devices = outcome.devices
    if not devices:
        return DeviceState(
            status=DeviceStatus.DISCONNECTED,
            fqbn=fqbn,
            checked_at=checked_at,
        )

    if len(devices) > 1:
        ports = ", ".join(device.port for device in devices)
        return DeviceState(
            status=DeviceStatus.AMBIGUOUS,
            fqbn=fqbn,
            detail=f"multiple candidate serial devices detected: {ports}",
            checked_at=checked_at,
        )

    device = devices[0]
    identified = bool(device.board_name and device.board_fqbn)
    # The names the student-facing USB field may show: the real detected
    # port first, then any different spelling detection itself reported,
    # then the canonical trainer path the Hack Mode command examples use
    # (see `serial_alias.py`). The UI may cycle through these and nothing
    # else. `device.vid`/`pid` are deliberately NOT among them — they stay
    # available as backend diagnostics but are not student-facing.
    aliases = serial_representations(device.port, device.label)
    return DeviceState(
        status=DeviceStatus.CONNECTED,
        port=device.port,
        board=device.board_name,
        fqbn=device.board_fqbn if identified else fqbn,
        identified=identified,
        port_aliases=aliases,
        checked_at=checked_at,
    )


#: The process-wide shared device state — THE ESP32, as far as this backend
#: is concerned. Build Mode's `default_service` and Hack Mode's `/ws/hack`
#: endpoint both read this one instance, which is what makes the two modes
#: structurally incapable of showing different boards, ports, or connection
#: states for the same physical device.
#:
#: `cache_seconds` is well under the frontend's poll interval (see
#: `src/hardware/deviceState.js::HARDWARE_POLL_INTERVAL_MS`): long enough
#: that many sessions polling at once collapse into one `arduino-cli board
#: list`, short enough that any single poll still reflects a board plugged
#: in or pulled out since the last tick.
device_monitor = DeviceMonitor(cache_seconds=config.HARDWARE_CACHE_SECONDS)
