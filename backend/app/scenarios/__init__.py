"""Scenario engine — SCAFFOLDING ONLY, not implemented in Phase 2A.

Intended responsibility (Phase 2C): own the state of a training scenario
(starting with "Weak MQTT Auth") — the simulated network, the ESP32 device,
the MQTT broker, and how each resolved command observes or mutates them.

The engine is the abstraction boundary that keeps the frontend terminal
independent of what is behind it: a simulated device today, a physical ESP32
later. Both must satisfy the same interface, so that swapping the backing
environment does not change the WebSocket protocol, the terminal component,
or the command router.

This module is intentionally empty of behaviour until that interface is
designed against a real scenario.
"""
