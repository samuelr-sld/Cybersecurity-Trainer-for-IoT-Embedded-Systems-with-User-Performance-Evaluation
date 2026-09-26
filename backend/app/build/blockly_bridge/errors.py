"""Errors raised by the Blockly bridge (Phases B4 and B5).

WHAT IS NOT AN ERROR HERE, AND THIS IS THE WHOLE POINT OF THE MODULE. Source
the bridge cannot draw as a block is never an error. It becomes a
`PreservedSource` carrying its exact text and a reason (see `models.py`),
exactly as B3 represents C++ it cannot interpret. A student never loses source
because one of two layers stopped short of understanding it. Going the other
way (B5), such a fragment is restored as an `UnsupportedStatement` — also not
an error.

A `BlocklyBridgeError` means something quite different: the PLATFORM disagrees
with itself. A semantic operation names no implemented block, the block
catalog and the IR describe the same operation's operands differently, or the
field-binding table names an input the catalog does not have. None of those
can be caused by firmware — only by the three tables drifting apart — so they
are raised loudly rather than hidden behind a preserved fragment.

THE REVERSE DIRECTION ADDS A SECOND FAMILY, and it exists for a different
reason: B4's input is an IR this codebase produced and validated, while B5's
input is a WORKSPACE — something an editor, a stored document or a test hands
over, which may name a block nobody defines, omit a field the block requires,
or nest a construct no block shape allows. Those are malformed INPUT rather
than platform drift, they are always reported (never repaired, never dropped),
and they share the same base class so one `except BlocklyBridgeError` covers
both directions.
"""

from __future__ import annotations


class BlocklyBridgeError(ValueError):
    """Base for every error the semantic -> Blockly adapter raises."""


class UnrepresentableOperationError(BlocklyBridgeError):
    """A semantic operation has no faithful block behind it.

    Raised when the catalog offers no implemented block for the operation,
    offers more than one, describes it with a different shape than the IR
    does, or has no field binding declared for it.
    """


class BlocklyModelError(BlocklyBridgeError):
    """A bridge model object was constructed in an invalid shape."""


# --- the reverse direction: a workspace is INPUT, so it can be malformed ----


class InvalidBlocklyWorkspaceError(BlocklyBridgeError):
    """The thing handed to the reverse adapter is not a Blockly program."""


class UnknownBlocklyBlockError(BlocklyBridgeError):
    """A block names a type no implemented catalog block claims.

    Covers both "no catalog entry has this Blockly type" and "the entry that
    has it is only CATALOGED or BLOCKLY_DEFINED". A block that cannot be drawn
    cannot have been drawn, so either way the workspace is describing
    something this platform does not define, and guessing an operation for it
    would invent meaning.
    """


class MissingBlocklyFieldError(BlocklyBridgeError):
    """A block is missing a field its operation's operand needs.

    A half-filled block would become a half-understood statement, which
    `app/build/semantic/models.py` deliberately has no representation for.
    """


class InvalidBlocklyFieldValueError(BlocklyBridgeError):
    """A field holds text that cannot be the value its parameter takes.

    A token with structure (`a + b`), a dropdown value outside the options it
    declares, a name where a `FieldNumber` is drawn. Reading it as something
    else would silently rewrite what the workspace says, which is exactly what
    `bindings.py` refuses to do in the forward direction too.
    """


class UnsupportedBlocklyStructureError(BlocklyBridgeError):
    """A block's shape contradicts the shape the catalog declares for it.

    A container found inside a statement body, a statement block carrying a
    body, a container drawing fields, a body hanging from an input name the
    catalog does not declare, or a field the block's operation has no operand
    for. Never repaired and never dropped — a malformed construct is reported.
    """


# A BLOCK WITH NO RECORDED SOURCE IS NO LONGER AN ERROR, and the reason it once
# was is worth keeping. Until B6, `OperationStatement` required the source text
# its meaning had been read from, so a block authored in the editor — which has
# none, and for which none exists anywhere — could not become a statement at
# all: inventing the text would have meant generating C++, which B5 must not do.
# B6 made the text provenance rather than a requirement (a statement's C++ is
# written from its operation and its values), so such a block now converts like
# any other and carries no source, and the `MissingBlockSourceError` that
# reported the old boundary has no case left to describe.
