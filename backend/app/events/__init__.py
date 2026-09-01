"""Event logger — SCAFFOLDING ONLY, not implemented in Phase 2A.

Intended position in the pipeline:

    Session -> Event Logger -> Evaluation System

Intended responsibility (later phase): record what a student did — commands
attempted, objectives reached, timing — as structured records, independent of
the terminal byte stream. Those records feed the performance evaluation and
the professor Dashboard, and are what `EventMessage` in
`app/models/messages.py` will carry to the client.

The `EventMessage` envelope already exists so the protocol is stable, but
nothing emits one yet and no storage layer is chosen. This module is
intentionally empty of behaviour.
"""
