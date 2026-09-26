"""Blockly serialization JSON -> the bridge model (Phase B8 correction).

    Blockly.serialization.workspaces.save(workspace)   what an editor produces
              |
              v
    blockly_section_from_state()                       (this module)
              |
              v
    BlocklySection                                     models.py — B4's shape
              |
              v
    blockly_to_semantic()                              reverse.py — B5

THE MISSING FIRST STEP OF B5. `reverse.py` states what a workspace MEANS, but
its input is a `BlocklySection`/`BlocklyProgram` dataclass — the shape B4
produces in Python. A browser hands back Blockly's own nested JSON, and until
this module existed nothing could read it. B5 was therefore reachable only
from inside this codebase, which is why Blockly could not be the student's
editing interface however complete the rest of the chain was. This module is
that step and only that step.

INVERTING `to_state()`, NOTHING MORE. `BlocklyBlock.to_state()` writes
`{"type", "fields"?, "inputs"?}` and `_chain` links a statement stack through
`"next"`. This reads exactly that back. It is STRUCTURAL: it builds the block
tree, resolves each Blockly type to the one implemented catalog block that
claims it, and stops. It does not decide what a block means (B5), does not
check a field's value against its parameter (B5), does not emit C++ (B6), and
does not know that a section can be locked (B2/B8 policy).

THE LOOKUP IS THE CATALOG'S, AND IT IS `reverse.py`'S. `block_definition_for_type`
is imported rather than reimplemented, so there is still exactly one place that
turns a Blockly type into a catalog block, and still no
`"pinmode" -> gpio.pin_mode` table anywhere in this package.

A WORKSPACE IS UNTRUSTED INPUT. Everything here is checked and nothing is
repaired: a node that is not an object, a missing or non-string `type`, a
field value that is not text, an input that does not hold a block, a `next`
that is not a block, or a nesting depth beyond `MAX_BLOCK_DEPTH` is reported
as an `InvalidBlocklyWorkspaceError` or an `UnsupportedBlocklyStructureError`.
The depth cap exists because this is a recursive reader over client JSON, and
a deeply nested payload must be refused rather than exhaust the stack.

PRESERVED FRAGMENTS COME BACK BESIDE THE WORKSPACE, NOT INSIDE IT. A Blockly
workspace cannot hold a node that is not a block (see
`BlocklyProgram.to_workspace_state`), so `BlocklySection.to_representation()`
emits the fragments separately with the index each one sat at. This reader
takes them back in that same shape and splices them into the body at those
indices, which is the exact inverse of `BlocklySection.records`.

THEIR TEXT IS AS TRUSTED AS AN `edit_region` PAYLOAD, AND NO MORE. A caller
could return a fragment whose text it changed. That is not a new capability:
it is the same untrusted C++ an `edit_region` frame has always been allowed to
put in a region, it is confined to the ONE section this reader is asked for,
and whether that section may be written at all is decided upstream by
`app/build/workspace.py` against the project's policy — never here.

PURE AND STDLIB-ONLY. No filesystem, no process, no session, no panel, no
clock. The same JSON always produces an equal `BlocklySection`.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.blockly.catalog import BlockCatalog, default_block_catalog
from app.blockly.models import ValueType
from app.build.blockly_bridge.errors import (
    InvalidBlocklyWorkspaceError,
    UnsupportedBlocklyStructureError,
)
from app.build.blockly_bridge.models import (
    BLOCKLY_LANGUAGE_VERSION,
    BlocklyBlock,
    BlocklyField,
    BlocklyProgram,
    BlocklySection,
    BlocklyValueInput,
    BridgeReason,
    PreservedSource,
)
from app.build.blockly_bridge.reverse import block_definition_for_type
from app.build.semantic import UnsupportedReason

#: How deeply blocks may nest before a workspace is refused. Far beyond any
#: real program (the deepest shape this platform draws today is a container
#: holding a flat statement stack, which is depth 2) and far below Python's
#: recursion limit, so a hostile payload is a clean rejection rather than a
#: RecursionError surfacing from the middle of a request handler.
MAX_BLOCK_DEPTH = 64


def blockly_section_from_state(
    section_id: str,
    state: Mapping[str, Any],
    preserved: Iterable[Mapping[str, Any]] = (),
    *,
    catalog: BlockCatalog = default_block_catalog,
) -> BlocklySection:
    """One section's Blockly workspace JSON, read back as a `BlocklySection`.

    `state` is what `BlocklySection.to_workspace_state()` produced and what
    `Blockly.serialization.workspaces.save` writes: `{"blocks":
    {"languageVersion": 0, "blocks": [...]}}`. At most ONE top-level block is
    accepted, because a section is one construct — a workspace carrying two
    would be describing two, and silently keeping the first would discard the
    student's work without saying so.

    `preserved` is the fragment list from the same representation, each entry
    carrying the `index` in the body it belongs at. An empty workspace with no
    fragments is a section whose body is empty, which is a legitimate edit.

    Raises `InvalidBlocklyWorkspaceError` for a payload that is not this shape
    and `UnsupportedBlocklyStructureError` for a block tree the catalog does
    not permit. It never raises for source it cannot draw — that is what the
    preserved fragments are.
    """
    if not isinstance(section_id, str) or not section_id.strip():
        raise InvalidBlocklyWorkspaceError(f"section id must be a non-empty string: {section_id!r}")
    top = _top_level_blocks(state, section_id)
    if len(top) > 1:
        raise UnsupportedBlocklyStructureError(
            f"{section_id}: a section is one construct, but this workspace has "
            f"{len(top)} top-level blocks"
        )
    fragments = _fragments(preserved, section_id)
    if not top:
        # No container block. Either the section was never representable (its
        # whole body is preserved source) or the editor handed back an empty
        # canvas. Both are the same shape here; deciding whether that is an
        # acceptable EDIT is `app/build/section_blockly.py`'s job, because only
        # it knows what the section looked like before.
        return BlocklySection(
            section_id=section_id,
            block=None,
            preserved=tuple(source for _, source in fragments),
        )
    block = _block(top[0], catalog, section_id, depth=1, body_fragments=fragments)
    return BlocklySection(section_id=section_id, block=block)


def blockly_program_from_state(
    sections: Iterable[Mapping[str, Any]],
    *,
    catalog: BlockCatalog = default_block_catalog,
) -> BlocklyProgram:
    """Several section representations, read back as one `BlocklyProgram`.

    The whole-file counterpart, kept for symmetry with
    `BlocklyProgram.to_representation()`. Build Mode's editing path does not
    use it — a student edits ONE section at a time and
    `app/build/section_blockly.py` splices that section into the file's
    existing program — but a caller holding a complete representation should
    not have to loop over this module's other function to get the object B5
    consumes.
    """
    return BlocklyProgram(
        sections=tuple(
            blockly_section_from_state(
                _text(entry, "sectionId", "a section representation"),
                _mapping(entry, "workspace", "a section representation"),
                entry.get("preserved", ()),
                catalog=catalog,
            )
            for entry in _each_mapping(sections, "sections")
        )
    )


# --- the workspace envelope -------------------------------------------------


def _top_level_blocks(state: Mapping[str, Any], section_id: str) -> tuple[Mapping[str, Any], ...]:
    """The `blocks.blocks` list, validated. Absent members mean an empty canvas."""
    if not isinstance(state, Mapping):
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: workspace state must be an object, got "
            f"{type(state).__name__}"
        )
    container = state.get("blocks")
    if container is None:
        return ()
    if not isinstance(container, Mapping):
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: workspace 'blocks' must be an object"
        )
    version = container.get("languageVersion", BLOCKLY_LANGUAGE_VERSION)
    if version != BLOCKLY_LANGUAGE_VERSION:
        # The serialization FORMAT changed, not this codebase's block set.
        # Reading it with today's rules could misread a structure rather than
        # fail, so it is refused.
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: workspace languageVersion {version!r} is not the "
            f"{BLOCKLY_LANGUAGE_VERSION} this backend reads"
        )
    blocks = container.get("blocks", ())
    if isinstance(blocks, (str, bytes, Mapping)) or not isinstance(blocks, Iterable):
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: workspace 'blocks.blocks' must be a list"
        )
    return tuple(_each_mapping(blocks, f"{section_id}: top-level blocks"))


def _fragments(
    preserved: Iterable[Mapping[str, Any]], section_id: str
) -> tuple[tuple[int, PreservedSource], ...]:
    """The preserved records as `(index, PreservedSource)`, in index order.

    Indices must be distinct and non-negative; whether they FIT the body is
    checked when the body is assembled, since that is the first point the
    body's length is known.
    """
    items: list[tuple[int, PreservedSource]] = []
    for record in _each_mapping(preserved, f"{section_id}: preserved fragments"):
        index = record.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise InvalidBlocklyWorkspaceError(
                f"{section_id}: a preserved fragment's index must be a non-negative "
                f"integer, got {index!r}"
            )
        items.append(
            (
                index,
                PreservedSource(
                    text=_text(record, "text", f"{section_id}: a preserved fragment"),
                    reason=_reason(record, section_id),
                ),
            )
        )
    indices = [index for index, _ in items]
    if len(set(indices)) != len(indices):
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: two preserved fragments claim the same body index"
        )
    return tuple(sorted(items, key=lambda item: item[0]))


def _reason(record: Mapping[str, Any], section_id: str) -> UnsupportedReason | BridgeReason:
    """A fragment's reason, read back into whichever vocabulary declares it.

    `PreservedSource.reason` is a union of B3's `UnsupportedReason` and this
    layer's `BridgeReason` (see `models.py`), and a record carries only the
    string. B3's is tried first because it is the one the IR owns; the two
    vocabularies share no value, so the order changes nothing but reads in the
    direction the data flows.
    """
    value = _text(record, "reason", f"{section_id}: a preserved fragment")
    for vocabulary in (UnsupportedReason, BridgeReason):
        try:
            return vocabulary(value)
        except ValueError:
            continue
    raise InvalidBlocklyWorkspaceError(
        f"{section_id}: {value!r} is not a preservation reason this backend defines"
    )


# --- blocks -----------------------------------------------------------------


def _block(
    state: Mapping[str, Any],
    catalog: BlockCatalog,
    section_id: str,
    *,
    depth: int,
    body_fragments: tuple[tuple[int, PreservedSource], ...] = (),
) -> BlocklyBlock:
    """One block node and everything hanging off it.

    `body_fragments` is passed only for the top-level container, whose body is
    the one a section's preserved fragments are indexed against. A nested
    block's body gets none: B4 only ever records fragments at section-body
    depth, so accepting them deeper would be inventing a position nothing
    produces.
    """
    if depth > MAX_BLOCK_DEPTH:
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: blocks nest deeper than {MAX_BLOCK_DEPTH} levels"
        )
    block_type = _text(state, "type", f"{section_id}: a block")
    definition = block_definition_for_type(block_type, catalog)

    fields = _fields(state, block_type, section_id)
    body_input, children, values = _inputs(state, definition, catalog, section_id, depth)
    body = _splice(children, body_fragments, block_type, section_id)
    if body and body_input is None:
        # Fragments with no block beside them still form a body, and a body
        # needs the input it hangs from. The catalog knows which input that is;
        # reading it here rather than guessing keeps the name out of this
        # module, exactly as `adapter.py` does in the forward direction.
        body_input = _declared_body_input(definition, block_type, section_id)
    try:
        return BlocklyBlock(
            operation_id=definition.semantic_operation,
            block_id=definition.block_id,
            block_type=block_type,
            # PROVENANCE IS GONE, AND THAT IS CORRECT. `to_state()` never
            # writes `source_text`, so a block that has been through an editor
            # genuinely has no source any more. B6 generates from the
            # operation and its values (see `app/build/semantic/models.py`), so
            # a source-less block regenerates exactly like a source-backed one
            # — and claiming a source this JSON does not carry would be the
            # only way to get it wrong.
            fields=fields,
            body_input=body_input,
            body=body,
            values=values,
        )
    except ValueError as error:
        raise UnsupportedBlocklyStructureError(f"{section_id}: {error}") from error


def _fields(
    state: Mapping[str, Any], block_type: str, section_id: str
) -> tuple[BlocklyField, ...]:
    """A block's `fields` object as ordered `BlocklyField`s.

    Values are required to be text because a Blockly field holds text (see
    `BlocklyField`). A number arriving as JSON `1000` rather than `"1000"` is
    normalised rather than refused — that is a serialisation detail of the
    transport, not a different value — but a container (an object, a list) is
    refused, because there is no token it could be.
    """
    raw = state.get("fields")
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: {block_type}: 'fields' must be an object"
        )
    fields: list[BlocklyField] = []
    for name, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise InvalidBlocklyWorkspaceError(
                f"{section_id}: {block_type}: field {name!r} must hold text, got "
                f"{type(value).__name__}"
            )
        try:
            fields.append(BlocklyField(name=str(name), value=str(value)))
        except ValueError as error:
            raise InvalidBlocklyWorkspaceError(f"{section_id}: {block_type}: {error}") from error
    return tuple(fields)


def _inputs(
    state: Mapping[str, Any],
    definition,
    catalog: BlockCatalog,
    section_id: str,
    depth: int,
) -> tuple[str | None, tuple[BlocklyBlock, ...], tuple[BlocklyValueInput, ...]]:
    """The block's `inputs`: its one statement body, and its value sockets.

    Blockly serializes both under the same `inputs` object, so which is which
    is read off the CATALOG: an input the block declares as STATEMENTS is a
    `next`-linked body (at most one — no implemented block has two), any other
    declared input is a socket holding exactly ONE value block, and an input
    the catalog does not declare for this block at all is refused rather than
    guessed at. Returns `(body input name, body chain, value inputs)`.
    """
    raw = state.get("inputs")
    if raw is None:
        return None, (), ()
    if not isinstance(raw, Mapping):
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: 'inputs' must be an object"
        )
    declared = {item.name: item.value_type for item in definition.inputs}
    body_input: str | None = None
    children: tuple[BlocklyBlock, ...] = ()
    values: list[BlocklyValueInput] = []
    for name, connection in raw.items():
        name = str(name)
        if name not in declared:
            raise UnsupportedBlocklyStructureError(
                f"{section_id}: {definition.block_id} declares no input {name!r}"
            )
        if not isinstance(connection, Mapping) or "block" not in connection:
            raise UnsupportedBlocklyStructureError(
                f"{section_id}: input {name!r} does not hold a block"
            )
        node = connection["block"]
        if declared[name] is ValueType.STATEMENTS:
            if body_input is not None:
                raise UnsupportedBlocklyStructureError(
                    f"{section_id}: a block draws at most one statement body, this one "
                    f"fills {body_input!r} and {name!r}"
                )
            body_input, children = name, _chain(node, catalog, section_id, depth)
            continue
        if not isinstance(node, Mapping):
            raise InvalidBlocklyWorkspaceError(
                f"{section_id}: input {name!r} must hold a block object"
            )
        if node.get("next") is not None:
            raise UnsupportedBlocklyStructureError(
                f"{section_id}: the value in {name!r} cannot chain to a next block"
            )
        values.append(
            BlocklyValueInput(name=name, block=_block(node, catalog, section_id, depth=depth + 1))
        )
    return body_input, children, tuple(values)


def _chain(
    node: Any, catalog: BlockCatalog, section_id: str, depth: int
) -> tuple[BlocklyBlock, ...]:
    """A `next`-linked stack, flattened in order — the inverse of `_chain`.

    Iterative rather than recursive down the `next` axis: a statement stack is
    a LIST, its length is not bounded by anything sensible, and recursing per
    statement would make a long-but-legitimate body look like an attack. Depth
    still guards nesting, which is the axis that actually nests.
    """
    blocks: list[BlocklyBlock] = []
    current: Any = node
    while current is not None:
        if not isinstance(current, Mapping):
            raise InvalidBlocklyWorkspaceError(
                f"{section_id}: a block must be an object, got {type(current).__name__}"
            )
        blocks.append(_block(current, catalog, section_id, depth=depth + 1))
        following = current.get("next")
        if following is None:
            break
        if not isinstance(following, Mapping) or "block" not in following:
            raise InvalidBlocklyWorkspaceError(
                f"{section_id}: 'next' must hold a block"
            )
        current = following["block"]
    return tuple(blocks)


def _splice(
    blocks: tuple[BlocklyBlock, ...],
    fragments: tuple[tuple[int, PreservedSource], ...],
    block_type: str,
    section_id: str,
) -> tuple[BlocklyBlock | PreservedSource, ...]:
    """Re-insert preserved fragments at the body positions they came from.

    The exact inverse of `BlocklySection.records`, which numbered them against
    the body they sat in. Fragments are placed in index order, so the result
    has each one back where it was and the blocks in their own order around it.
    """
    if not fragments:
        return blocks
    total = len(blocks) + len(fragments)
    out_of_range = [index for index, _ in fragments if index >= total]
    if out_of_range:
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: {block_type}: preserved fragment index "
            f"{out_of_range[0]} is past the end of a {total}-item body"
        )
    body: list[BlocklyBlock | PreservedSource] = []
    remaining = list(blocks)
    fragment_at = dict(fragments)
    for position in range(total):
        if position in fragment_at:
            body.append(fragment_at[position])
        else:
            body.append(remaining.pop(0))
    return tuple(body)


def _declared_body_input(definition, block_type: str, section_id: str) -> str:
    """The one statement-body input the catalog declares for this block."""
    bodies = [item for item in definition.inputs if item.value_type is ValueType.STATEMENTS]
    if len(bodies) != 1:
        raise UnsupportedBlocklyStructureError(
            f"{section_id}: {block_type} carries a body, but the catalog declares "
            f"{len(bodies)} statement inputs for it"
        )
    return bodies[0].name


# --- small validated readers ------------------------------------------------


def _each_mapping(items: Any, what: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(items, (str, bytes, Mapping)) or not isinstance(items, Iterable):
        raise InvalidBlocklyWorkspaceError(f"{what} must be a list")
    entries = tuple(items)
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise InvalidBlocklyWorkspaceError(
                f"{what}: every entry must be an object, got {type(entry).__name__}"
            )
    return entries


def _mapping(source: Mapping[str, Any], key: str, what: str) -> Mapping[str, Any]:
    value = source.get(key)
    if not isinstance(value, Mapping):
        raise InvalidBlocklyWorkspaceError(f"{what} must carry an object {key!r}")
    return value


def _text(source: Mapping[str, Any], key: str, what: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidBlocklyWorkspaceError(f"{what} must carry non-empty text {key!r}")
    return value
