"""Backend service for the Embedded IoT Cybersecurity Trainer.

Current scope: FastAPI application, Hack Mode WebSocket endpoint, session
lifecycle, a structured message protocol, and a controlled command router
that resolves completed terminal lines against a closed set of simulated
tools (`commands/`).

The command router resolves each line against a per-session scenario engine
(`scenarios/`) that simulates an Environmental Monitoring IoT target. The
scenario's own domain events and state snapshots are delivered back over the
same WebSocket as `event`/`state` frames (see `websocket.py`).

Phase 2B added durable event recording (`events/`): the command router
stamps each dispatched command and each domain transition with a
server-side UTC timestamp and a per-session sequence, and appends them to a
SQLite log scoped by session id. The backend decides what happened — nothing
is inferred from terminal text.

Still absent by design: *scoring*. The Phase 2E metrics (ACR, RE, EAC, TTE
for Hack Mode; TTR, AID, DEI for Build Mode) are computed from these rows
later; no arithmetic over them exists anywhere in this package yet.

There is no OS command execution, no PTY, and no shell anywhere in this
package, and none may be added — see `commands/__init__.py`.
"""

__version__ = "0.2.0d"
