"""Handler modules, one per registered command.

Each module exposes a module-level `SPEC` (a `CommandSpec`) that
`app.commands.registry.build_default_registry` picks up. `help` is the sole
exception: it exposes `build_spec(registry)` instead, because its output is a
function of the registry it belongs to.

SCOPE. Tool handlers (`nmap`, `mosquitto_sub`, `mosquitto_pub`,
`mqtt-explorer`, `firmware-extract`, `firmware-analyze`) are thin adapters:
they parse the student's arguments and call one method on
`CommandContext.scenario`, then render the returned `ScenarioOutcome`. They
hold no target facts (IPs, ports, topics, readings) and no discovery/attack
state — all of that belongs to the scenario engine (`app/scenarios/`), so
handlers cannot drift out of step with the simulation. `help` and `clear`
are scenario-independent.
"""
