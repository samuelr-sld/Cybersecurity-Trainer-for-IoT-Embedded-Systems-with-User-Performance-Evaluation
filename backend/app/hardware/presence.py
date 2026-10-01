"""Notice that a serial port went away — faster than a poll can.

    OS serial-port list  -->  PortPresenceWatcher  -->  DeviceMonitor.forget_identity(port)
    (pyserial, ~3 ms)         (this module)             (drops the cached MAC for that port)

THE GAP THIS CLOSES. `DeviceMonitor` probes a board's MAC once per plug-in and
caches it by PORT NAME, because a probe resets the board and cannot run on every
poll. The cache is dropped when a detection sees no board — but a detection is a
poll (`arduino-cli board list`, every ~10 s), and replacing one board with
another takes a couple of seconds. A swap that falls between two polls is
invisible to them: the port is there before and after, and generic USB bridges
(every CP210x devkit reports `10C4:EA60`, serial `0001`) give both boards the
SAME port name and the same descriptors, so nothing `board list` returns can
tell them apart. The monitor then kept naming the previous board.

WHAT THIS DOES ABOUT IT. It enumerates the OS's serial ports every second or so —
a few milliseconds, with no device opened and nothing probed — and when a port
that was present is missing, tells the monitor to forget that port's identity.
The board that later reappears there is then identified like any freshly plugged
one: the next `refresh()` finds no cached MAC and reads one. An unplug longer
than the interval is therefore always seen.

WHAT IT DELIBERATELY DOES NOT DO. It never reads a MAC, never opens a port, never
runs `arduino-cli` or esptool and never emits an event: it only withdraws trust in
a cached answer, and a board the monitor has not identified is the honest state
(`UNIDENTIFIED`, the panel layer's existing word). A swap quicker than the
interval is still invisible here; the decision points that act on identity (mode
preparation) re-read it themselves with `refresh(reverify_identity=True)`.

PYSERIAL IS OPTIONAL, like everywhere else in this backend: without it the
default enumerator reports nothing and the watcher is inert (logged once), the
same way the serial commands report themselves unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Protocol

from app import config

logger = logging.getLogger(__name__)

#: One snapshot of the serial ports the OS currently lists. A set of device
#: names (`COM3`, `/dev/ttyUSB0`), nothing more.
PortEnumerator = Callable[[], frozenset[str]]


class IdentityHolder(Protocol):
    """The one thing the watcher may do to the monitor."""

    def forget_identity(self, port: str | None = None) -> None: ...


def enumerate_serial_ports() -> frozenset[str]:
    """The serial ports pyserial lists right now; empty if pyserial is absent.

    Lists, never opens. Raises nothing the watcher has to care about: an
    enumeration that fails is treated as "no information" by the caller.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return frozenset()
    return frozenset(port.device for port in list_ports.comports())


class PortPresenceWatcher:
    """Tells a monitor to forget a port's identity when that port disappears."""

    def __init__(
        self,
        monitor: IdentityHolder,
        *,
        enumerate_ports: PortEnumerator = enumerate_serial_ports,
        interval_seconds: float = config.HARDWARE_PRESENCE_INTERVAL_SECONDS,
    ) -> None:
        self._monitor = monitor
        self._enumerate = enumerate_ports
        self._interval = max(0.05, float(interval_seconds))
        #: None until the first enumeration: with nothing to compare against,
        #: the first look establishes a baseline and forgets nothing.
        self._present: frozenset[str] | None = None

    async def check_once(self) -> frozenset[str]:
        """One enumeration; forgets every port that was present and now is not.

        Returns the ports that vanished. A failing enumeration changes nothing:
        not knowing is not the same as the board having gone, and forgetting on
        an error would make a flaky USB stack reset the board on every poll.
        """
        try:
            current = await asyncio.to_thread(self._enumerate)
        except Exception:  # noqa: BLE001 - a watcher must never take the app down
            logger.warning("serial port enumeration failed", exc_info=True)
            return frozenset()

        previous = self._present
        self._present = current
        if previous is None:
            return frozenset()
        gone = previous - current
        for port in sorted(gone):
            logger.info("serial port %s disappeared; its cached identity is dropped", port)
            self._monitor.forget_identity(port)
        return gone

    async def run(self) -> None:
        """Check forever, until cancelled."""
        while True:
            await self.check_once()
            await asyncio.sleep(self._interval)


def default_presence_watcher() -> PortPresenceWatcher:
    """A watcher over the process-wide monitor. Built lazily to keep imports flat."""
    from app.hardware import device_monitor

    return PortPresenceWatcher(device_monitor)
