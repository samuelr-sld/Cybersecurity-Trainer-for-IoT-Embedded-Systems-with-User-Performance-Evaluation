"""Command router — SCAFFOLDING ONLY, not implemented in Phase 2A.

Intended position in the pipeline:

    WebSocket -> Session -> Command Router -> Scenario Engine -> simulated IoT

Intended responsibility (Phase 2B): turn a session's raw terminal input into
a resolved command against a CLOSED set of simulated tools (nmap, mosquitto
pub/sub, capture, exploit), producing terminal output plus zero or more
events. Line editing/echo state lives here, since it is per-session.

Hard constraint inherited from `app/websocket.py`: resolution is matching
against a known command table. It is never delegation to a host shell — no
`os.system`, `subprocess`, `shell=True`, `eval`, or `exec` may appear in this
package.

This module is intentionally empty of behaviour; a placeholder implementation
would be indistinguishable from a real one at call sites.
"""
