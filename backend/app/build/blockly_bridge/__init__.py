"""The semantic IR <-> Blockly adapter (Phases B4/B5) — the ONLY connection.

    C++ source
        -> B1 CodeSection            (app/build/discovery/)
        -> B3 Semantic IR            (app/build/semantic/)
        -> B4 THIS PACKAGE           (adapter.py)   Semantic IR -> Blockly
        -> Blockly                   (app/blockly/ metadata, src/blockly/ UI)
        -> B5 THIS PACKAGE           (reverse.py)   Blockly -> Semantic IR
        -> B3 Semantic IR

THE CENTRAL ARCHITECTURAL RULE. `app.build.semantic` and `app.blockly` must
never become a direct package dependency in either direction. The IR must stay
valid for code that came from handwritten C++, a future parser, another editor
or eventually Blockly itself; the catalog must stay a platform-level
description of ~200 programming concepts, independent of any IR. This package
is the third place their composition is allowed to live — exactly the reason
`app/build/document_project.py` exists for B1/B2 and `app/scenario_selection.py`
exists for `app.panels`/`app.scenarios`. It is also the ONLY module that
imports both.

    app.build.blockly_bridge  ->  app.build.semantic
    app.build.blockly_bridge  ->  app.blockly

and nothing points back. `tests/test_build_blockly_bridge.py` asserts that
statically, in both directions.

WHAT IT REUSES RATHER THAN RESTATES. The catalog has carried a
`semantic_operation` on every block since B0.1, and B3 created the referent.
B4 is where that field finally does work: an operation id is looked up to the
one IMPLEMENTED `BlockDefinition` that names it, and the block's own
`blockly_type` and input names come off that definition. B5 reads the very
same relation backwards, from `blockly_type` to `semantic_operation`, which is
single-valued because `BlockCatalog` already refuses to let two blocks claim
one Blockly type. This package contains no operation-to-block-type table in
EITHER direction — one relation, owned by the catalog, read two ways. The one
mapping it does add (`bindings.py`) is the field-level detail the catalog
deliberately does not model, keyed by operation id so the catalog's relation is
not duplicated even indirectly, and `render`/`parse` sit beside each other
there so the two directions cannot disagree about what a field's text means.

WHAT IT REFUSES TO PRETEND. Blockly can draw five operations. Real firmware is
mostly other things. Source that no block represents — because B3 never
understood it, or because an understood operand cannot occupy a real field —
is carried through verbatim as `PreservedSource`, positioned by index inside
the body it came from, and comes back as the `UnsupportedStatement` it was.
Nothing is dropped, nothing is approximated, and no block is emitted that
claims to understand C++ this codebase does not.

WHAT IT IS NOT. Two directions, both between one IR and one workspace: no
Blockly -> C++, no IR -> C++ and no source reconstruction (B6). No editability,
security region, remediation rule, validation, compile, flash or MQTT logic —
this package answers "how is this represented?", never "may the student change
it?".
"""

from __future__ import annotations

from app.build.blockly_bridge.adapter import block_definition_for, program_to_blockly
from app.build.blockly_bridge.bindings import (
    FieldBinding,
    FieldBindingTable,
    FieldKind,
    build_default_bindings,
    default_field_bindings,
)
from app.build.blockly_bridge.errors import (
    BlocklyBridgeError,
    BlocklyModelError,
    InvalidBlocklyFieldValueError,
    InvalidBlocklyWorkspaceError,
    MissingBlockSourceError,
    MissingBlocklyFieldError,
    UnknownBlocklyBlockError,
    UnrepresentableOperationError,
    UnsupportedBlocklyStructureError,
)
from app.build.blockly_bridge.models import (
    BLOCKLY_LANGUAGE_VERSION,
    BlocklyBlock,
    BlocklyField,
    BlocklyProgram,
    BlocklySection,
    BridgeReason,
    PreservedRecord,
    PreservedSource,
)
from app.build.blockly_bridge.reverse import block_definition_for_type, blockly_to_semantic

__all__ = [
    "BLOCKLY_LANGUAGE_VERSION",
    "BlocklyBlock",
    "BlocklyBridgeError",
    "BlocklyField",
    "BlocklyModelError",
    "BlocklyProgram",
    "BlocklySection",
    "BridgeReason",
    "FieldBinding",
    "FieldBindingTable",
    "FieldKind",
    "InvalidBlocklyFieldValueError",
    "InvalidBlocklyWorkspaceError",
    "MissingBlockSourceError",
    "MissingBlocklyFieldError",
    "PreservedRecord",
    "PreservedSource",
    "UnknownBlocklyBlockError",
    "UnrepresentableOperationError",
    "UnsupportedBlocklyStructureError",
    "block_definition_for",
    "block_definition_for_type",
    "blockly_to_semantic",
    "build_default_bindings",
    "default_field_bindings",
    "program_to_blockly",
]
