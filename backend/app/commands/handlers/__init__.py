"""Handler modules, one per registered command.

Each module exposes a module-level `SPEC` (a `CommandSpec`) that
`app.commands.registry.build_default_registry` picks up. `help` is the sole
exception: it exposes `build_spec(registry)` instead, because its output is a
function of the registry it belongs to.

SCOPE. Tool handlers (`nmap`, `mosquitto_sub`, `mosquitto_pub`, `esptool.py`,
`strings`, `grep`) are thin adapters: they parse the student's arguments and
call one method on `CommandContext.scenario`, then render the returned
`ScenarioOutcome`. They
hold no target facts (IPs, ports, topics, readings) and no discovery/attack
state — all of that belongs to the scenario engine (`app/scenarios/`), so
handlers cannot drift out of step with the simulation. This is the pattern
Phase 2C formalises as "the generic Hack Engine": every tool handler above
is reusable as-is by any future `Scenario` implementation, because none of
them import from `app.scenarios.environmental` or know it exists.

The serial handlers (`serial_status`, `serial_monitor`, `serial_send`,
`serial_close`, Phase 2A) are a second, equally generic category: they carry
real bytes to the attached ESP32 through the shared hardware layer
(`app/hardware/`) and never touch `CommandContext.scenario` at all — a board
plugged into a training rig has no notion of "panel 1" or "panel 2" either.

`help`, `clear`, and `history` (Phase 2C) are scenario-independent trainer
utilities. `history` reads back the Phase 2B event recorder
(`context.session.recorder`); it is the one handler here that touches the
event layer, and only ever to read it.
"""
