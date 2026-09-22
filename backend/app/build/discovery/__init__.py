"""C++ structural discovery (Phase B1) — an isolated, passive analysis layer.

    raw .ino text -> analyze_source(text) -> BuildDocument -> CodeSection[]

Still self-contained: this package imports nothing from `app.build.models`,
`app.build.workspace`, `app.build.service`, `app.build_sessions`,
`app.panels`, `app.hardware`, or any Blockly module — see its own module
docstrings (`models.py`, `analyzer.py`) for why, in particular why a
`CodeSection` is not a `FileSegment`.

Phase B2 added the first consumer, and deliberately added it OUTSIDE this
package: `app/build/document_project.py` converts a `BuildDocument` into the
`FirmwareFile`/`FileSegment` shape a `BuildProject` holds, and
`app/build/sketch_source.py` reads a real sketch directory through it. The
dependency points one way only — they import this, this imports nothing of
theirs — so discovery remains a passive analysis layer with no knowledge of
workspaces, permissions, panels or sessions.
"""

from __future__ import annotations

from app.build.discovery.analyzer import (
    DiscoveryError,
    EmptySourceError,
    UnbalancedBracesError,
    UnterminatedLiteralError,
    analyze_source,
    code_mask,
)
from app.build.discovery.models import BuildDocument, CodeSection, SectionKind

__all__ = [
    "BuildDocument",
    "CodeSection",
    "DiscoveryError",
    "EmptySourceError",
    "SectionKind",
    "UnbalancedBracesError",
    "UnterminatedLiteralError",
    "analyze_source",
    "code_mask",
]
