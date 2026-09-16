"""Which registered training panel a given ESP32 is.

Position in the architecture (Phase 2C.5, extended by Phase 2D.1):

    ESP32 MAC ──> PanelRegistry.resolve ──> PanelDefinition ──> PanelPackage
    (identity.py)      (this module)          (this module)     (app/panels/)
                                                                      |
                                                            FirmwareConfiguration
                                                                (firmware.py)

A "panel" is one of the trainer's physical activity stations. The ESP32 on
it is an *interchangeable module*: the MAC burned into its OTP ROM
identifies that physical board, and this registry decides which panel role
the board is currently serving. The MAC therefore encodes nothing on its
own — it is a key, and everything a later phase needs (display name, and the
resource package holding the scenario and firmware configuration) hangs off
the `PanelDefinition` it resolves to. Swapping a module means changing a
binding, never a code path.

ONE ALGORITHM, NO PANEL BRANCHES. `PanelRegistry.resolve` is a normalized
dictionary lookup, identical for every panel. Adding a sixth panel, or a
spare module for an existing one, is a new table entry (or a
`TRAINER_PANEL_MACS` binding); nothing anywhere in the backend asks
`if panel_id == ...`.

PURE BY DESIGN. This module performs no hardware I/O and imports nothing
that can: no esptool, no serial port, no Arduino CLI, no `app.build`. MAC
*acquisition* belongs to `identity.py` (driven by `monitor.py`), and joining
the two is `panel_identification.py`'s job. `tests/test_panel_registry.py`
asserts this module's imports statically.

UNREGISTERED IS NOT AN ERROR, AND IS NEVER GUESSED. A board whose MAC has no
binding resolves to `PanelMatch.UNREGISTERED` with no panel, and the UI
shows the MAC itself. Inventing a plausible panel for an unknown board would
make the header lie about which station a student is sitting at.

THE FIVE-PANEL SCOPE. All five panels have a definition so a module can be
bound to any of them without a code change. Only one physical board is
bound today — the ESP32 on this project's development machine, whose MAC was
read from its OTP ROM with `esptool read_mac`.

IDENTITY HERE, EXPERIMENT ELSEWHERE (Phase 2D.1). A definition answers only
"which panel is this board?". What that panel's experiment IS — its
scenario, learning objectives, activity instructions, expected workflow,
evaluation declaration and firmware configuration — lives in its resource
package (`app/panels/`, loaded from `<root>/<package-id>/panel.json`), and a
definition references it by a validated `package_id` and nothing more.

That split is deliberate and load-bearing. This module sits on the hardware
header's 10s poll path, so naming a panel must not read a file; and
`package_id` being an identifier rather than a path is what keeps a MAC from
ever resolving to a filesystem location. It is also why `firmware` is no
longer a field here: a panel's firmware configuration is described in its
package, so exactly one place answers "which firmware belongs to this
panel?" instead of two that could disagree.

`package_id=None` is the honest "no courseware integrated yet" state, and it
is what four of the five panels carry: only Panel 1 has a package on `main`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum

from app import config
from app.hardware.firmware import IDENTIFIER_PATTERN
from app.hardware.mac import normalize_mac


@dataclass(frozen=True)
class PanelDefinition:
    """One training panel: what it is called, which modules serve it, and
    which firmware belongs to it.

    Deliberately carries NO attack behaviour, scenario logic, compile or
    flash capability — it is passive data. Hack Mode's scenarios and Build
    Mode's toolchain stay where they are; a definition only names the
    resource package a later phase will read.
    """

    #: Stable identifier, lower-case hyphenated. Never shown as the name.
    panel_id: str
    #: The human-readable name the hardware header shows.
    display_name: str
    #: The physical ESP32 modules registered to this panel, canonical
    #: lower-case colon form. Empty is valid: a panel awaiting hardware.
    mac_addresses: tuple[str, ...] = ()
    #: The resource package holding this panel's experiment (`app/panels/`),
    #: or None while no package for it exists in the repository.
    #:
    #: AN IDENTIFIER, NEVER A PATH. It is validated to the same lower-case
    #: hyphenated shape as `panel_id`, so it holds no separator, no dot and
    #: no drive letter and cannot name a parent directory or an absolute
    #: location. `app/panels/loader.py` joins it to ONE backend-configured
    #: root and re-checks containment; nothing else may turn it into a path.
    package_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.panel_id, str) or not re.fullmatch(
            IDENTIFIER_PATTERN, self.panel_id
        ):
            raise ValueError(f"panel id must be a lower-case hyphenated identifier: {self.panel_id!r}")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError(f"panel {self.panel_id!r} needs a non-empty display name")
        if self.package_id is not None and (
            not isinstance(self.package_id, str)
            or not re.fullmatch(IDENTIFIER_PATTERN, self.package_id)
        ):
            raise ValueError(
                f"panel {self.panel_id!r} package id must be a lower-case hyphenated "
                f"identifier: {self.package_id!r}"
            )

        # Normalize every MAC once, here, so the registry index can never
        # hold two spellings of one board. A malformed MAC in backend source
        # is a programming error and fails loudly rather than silently
        # leaving a board unidentifiable.
        canonical: list[str] = []
        for raw in self.mac_addresses:
            mac = normalize_mac(raw) if isinstance(raw, str) else None
            if mac is None:
                raise ValueError(f"panel {self.panel_id!r} has an invalid MAC address: {raw!r}")
            if mac not in canonical:
                canonical.append(mac)
        object.__setattr__(self, "mac_addresses", tuple(canonical))


class PanelMatch(str, Enum):
    """The outcome of resolving one MAC."""

    #: The MAC is bound to a registered panel.
    IDENTIFIED = "identified"
    #: A well-formed MAC with no binding — a real board nobody has registered.
    UNREGISTERED = "unregistered"
    #: No usable MAC: absent, empty, or malformed. Nothing to look up.
    NO_IDENTITY = "no_identity"


@dataclass(frozen=True)
class PanelResolution:
    """The answer to "which panel is this MAC?"."""

    match: PanelMatch
    #: The canonical MAC that was looked up, or None for NO_IDENTITY.
    mac: str | None = None
    panel: PanelDefinition | None = None

    @property
    def identified(self) -> bool:
        return self.panel is not None

    @property
    def panel_id(self) -> str | None:
        return self.panel.panel_id if self.panel is not None else None

    @property
    def display_name(self) -> str | None:
        return self.panel.display_name if self.panel is not None else None


class PanelRegistry:
    """Resolves MAC -> `PanelDefinition`. Immutable once built.

    Construction validates the whole table — duplicate panel ids, or one MAC
    bound to two panels, raise `ValueError` — so resolution is
    deterministic by construction: a MAC maps to at most one panel, always
    the same one.
    """

    def __init__(self, panels: Iterable[PanelDefinition]) -> None:
        by_id: dict[str, PanelDefinition] = {}
        by_mac: dict[str, PanelDefinition] = {}
        for panel in panels:
            if not isinstance(panel, PanelDefinition):
                raise ValueError(f"not a PanelDefinition: {panel!r}")
            if panel.panel_id in by_id:
                raise ValueError(f"duplicate panel id: {panel.panel_id!r}")
            by_id[panel.panel_id] = panel
            for mac in panel.mac_addresses:
                owner = by_mac.get(mac)
                if owner is not None:
                    raise ValueError(
                        f"MAC {mac} is bound to both {owner.panel_id!r} and {panel.panel_id!r}"
                    )
                by_mac[mac] = panel
        self._by_id = by_id
        self._by_mac = by_mac

    @property
    def panels(self) -> tuple[PanelDefinition, ...]:
        """Every registered panel, in registration order."""
        return tuple(self._by_id.values())

    def get(self, panel_id: str) -> PanelDefinition | None:
        """The panel with this id, or None."""
        return self._by_id.get(panel_id)

    def resolve(self, mac: str | None) -> PanelResolution:
        """Which panel this MAC is bound to. Never raises, never guesses."""
        canonical = normalize_mac(mac) if isinstance(mac, str) else None
        if canonical is None:
            return PanelResolution(match=PanelMatch.NO_IDENTITY)
        panel = self._by_mac.get(canonical)
        if panel is None:
            return PanelResolution(match=PanelMatch.UNREGISTERED, mac=canonical)
        return PanelResolution(match=PanelMatch.IDENTIFIED, mac=canonical, panel=panel)

    def bind(self, bindings: Mapping[str, str]) -> "PanelRegistry":
        """A new registry with extra MAC -> panel-id bindings applied.

        A binding *moves* a module: the MAC is removed from whichever panel
        held it and added to the named one, which is exactly what physically
        swapping an interchangeable ESP32 between panels means. Every MAC
        and panel id must be valid — `ValueError` otherwise; the lenient,
        skip-what-is-malformed behaviour belongs to `parse_panel_bindings`,
        where untrusted-by-typo environment text is read.
        """
        canonical: dict[str, str] = {}
        for raw_mac, panel_id in bindings.items():
            mac = normalize_mac(raw_mac) if isinstance(raw_mac, str) else None
            if mac is None:
                raise ValueError(f"invalid MAC address in binding: {raw_mac!r}")
            if panel_id not in self._by_id:
                raise ValueError(f"binding names an unregistered panel id: {panel_id!r}")
            canonical[mac] = panel_id

        rebuilt = []
        for panel in self._by_id.values():
            kept = [mac for mac in panel.mac_addresses if mac not in canonical]
            added = [mac for mac, target in canonical.items() if target == panel.panel_id]
            rebuilt.append(replace(panel, mac_addresses=tuple(kept + added)))
        return PanelRegistry(rebuilt)


def parse_panel_bindings(raw: str, known_panel_ids: Iterable[str]) -> dict[str, str]:
    """Read `TRAINER_PANEL_MACS` — `mac=panel-id` pairs, comma separated.

    Can only bind a MAC to a panel that already has a definition in backend
    source; it cannot create a panel, rename one, or attach firmware. That is
    what keeps panel resources trusted configuration even though the binding
    itself is operator-supplied.

    Malformed entries — a non-MAC key, an unknown panel id, a missing `=` —
    are skipped rather than raising: a typo in an environment variable must
    not stop the backend from starting, and the affected board simply shows
    its MAC, the same honest fallback an unregistered board gets.
    """
    known = set(known_panel_ids)
    bindings: dict[str, str] = {}
    for chunk in raw.split(","):
        key, separator, value = chunk.partition("=")
        if not separator:
            continue
        mac = normalize_mac(key)
        panel_id = value.strip()
        if mac and panel_id in known:
            bindings[mac] = panel_id
    return bindings


#: The finalized five-panel scope. Display names are the ones the hardware
#: header already shows. See the module docstring for why only one module is
#: bound and why no panel has firmware yet.
BUILT_IN_PANELS: tuple[PanelDefinition, ...] = (
    PanelDefinition(
        panel_id="smart-home-mqtt-control",
        display_name="SMART HOME MQTT CONTROL SYSTEM",
        # The ESP32-D0WD-V3 on this project's development machine, read from
        # the chip's OTP ROM with `esptool read_mac` — not a placeholder.
        mac_addresses=("20:9b:a9:88:0b:e4",),
        # Phase 2D.2: the one panel whose resource package exists on `main`
        # — see `backend/panels/smart-home-mqtt-control/panel.json`.
        package_id="smart-home-mqtt-control",
    ),
    PanelDefinition(
        panel_id="environmental-monitoring",
        display_name="ENVIRONMENTAL MONITORING SYSTEM",
    ),
    PanelDefinition(
        panel_id="emergency-exit-lighting",
        display_name="EMERGENCY EXIT LIGHTING SYSTEM",
    ),
    PanelDefinition(
        panel_id="iot-identity-access-control",
        display_name="IOT IDENTITY AND ACCESS CONTROL SYSTEM",
    ),
    PanelDefinition(
        panel_id="smart-iot-device-control",
        display_name="SMART IOT DEVICE CONTROL SYSTEM",
    ),
)


def default_panel_registry() -> PanelRegistry:
    """The active registry: built-in panels, with configured bindings applied.

    Built at call time rather than import time so a changed
    `config.PANEL_MACS_RAW` is honoured without a restart of the import
    graph (and so tests can monkeypatch it). Five panels make this far
    cheaper than the esptool probe it follows, which runs once per plug-in.
    """
    registry = PanelRegistry(BUILT_IN_PANELS)
    bindings = parse_panel_bindings(
        config.PANEL_MACS_RAW, (panel.panel_id for panel in registry.panels)
    )
    return registry.bind(bindings) if bindings else registry
