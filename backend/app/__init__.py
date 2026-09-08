"""Backend service for the Embedded IoT Cybersecurity Trainer.

Current scope: FastAPI application, Hack Mode WebSocket endpoint, session
lifecycle, a structured message protocol, and a controlled command router
that resolves completed terminal lines against a closed set of simulated
tools (`commands/`).

The command router resolves each line against a per-session scenario engine
(`scenarios/`) that simulates an Environmental Monitoring IoT target. The
scenario's own domain events and state snapshots are delivered back over the
same WebSocket as `event`/`state` frames (see `websocket.py`).

Still absent by design: event *recording and scoring* for evaluation
(events are transported now, but nothing persists or grades them yet), the
frontend WebSocket client, and any hardware integration.

There is no OS command execution, no PTY, and no shell anywhere in this
package, and none may be added — see `commands/__init__.py`.
"""

__version__ = "0.2.0d"
