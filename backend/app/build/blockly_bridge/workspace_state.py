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
`"next"`. This reads exactly that back. A container's `extraState` (its
read-only signature header, `signature_display.py`) is display-only and is
deliberately NEVER read: a function's signature on the way back in is always
the section's own (`section_blockly.py::program_with_section`). It is STRUCTURAL: it builds the block
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

NESTED PRESERVED SOURCE IS AS AUTHORITATIVE AS SECTION-LEVEL SOURCE. Every
record carries `id`/`parentId`/`input`/`index`: the id of its own drawn block,
the id of the block whose statement input holds the fragment, which input, and
where in it (`BlocklyBlock.to_state` writes those ids into the workspace and
Blockly keeps them through an edit).

CONTENT FROM THE RECORD, POSITION FROM THE WORKSPACE. A drawn
`preserved_source` block is read for exactly two things - its `id` and where it
sits in a statement chain - and NEVER for its text. Its id is looked up among
the records; the fragment placed at that chain position is the RECORD's
`PreservedSource`, so editing the drawing's text changes nothing. That is what
keeps a read-only fragment in the order the student left it: inserting a block
above it, or dropping a stack that re-attaches it lower down, moves the
fragment with its drawing instead of splicing it back at the index it was
OPENED at (which silently reordered real C++). A drawing whose id names no
record is forged and ignored; one record drawn twice is refused, so a payload
cannot duplicate a fragment.

A record with NO drawing in the workspace keeps the older, index-based rule: it
is spliced back into the body it names at the index it names. That is what
makes deleting a drawing restore the fragment and what keeps a workspace from
before drawings carried ids reading exactly as it did. A fragment whose parent
block is no longer in the workspace goes with that block (deleting an `if`
deletes what was inside it, drawing included), because a fragment lives in a
body and a body lives in a block. Removing a fragment on purpose is done by
removing its RECORD (Build Mode's CLEAR), never by anything done to a drawing.

AN ORPHANED DRAWING IS NOT A SECOND CONSTRUCT. The drawing is read-only and
unmovable, and Blockly cannot always keep it attached: healing a stack around a
removed block skips a non-movable successor, and inserting a block that has no
next socket bumps the displaced tail out of its chain. Either leaves the
drawing at the TOP LEVEL of the saved workspace, which the one-construct rule
would otherwise count as a second construct and refuse - over a block this
reader never reads for content or position anyway, and which the student cannot
move or delete. So a top-level chain made ONLY of `preserved_source` drawings
(no inputs, nothing else chained) is ignored. Its record is then simply
undrawn and takes the index rule above, exactly as if the drawing had been
deleted. This is not a widening of what a student can submit: the fragment's
text still comes only from the record, an orphaned drawing can add no block, and
a top-level block of any other type - including a real block chained under a
drawing, or a drawing carrying inputs - still counts as a construct. A lone
orphan with no container yields a section with no block, which
`app/build/section_blockly.py` refuses for every section; the drawing never
becomes the section's construct.

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
    BlocklyBranch,
    BlocklyField,
    BlocklyProgram,
    BlocklySection,
    BlocklyValueInput,
    BridgeReason,
    PRESERVED_BLOCK_TYPE,
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
    {"languageVersion": 0, "blocks": [...]}}`. At most ONE top-level CONSTRUCT
    is accepted, because a section is one construct — a workspace carrying two
    would be describing two, and silently keeping the first would discard the
    student's work without saying so. A top-level chain of nothing but
    read-only `preserved_source` drawings is not a construct and is ignored
    (see `_is_drawing_only`); anything else beside the container is refused,
    and the refusal names each extra root's type and id.

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
    # A top-level chain made only of read-only `preserved_source` drawings is not
    # a construct (see the ORPHANED DRAWING paragraph of the module docstring);
    # everything else at the top level is, and a section has exactly one.
    top = tuple(
        root for root in _top_level_blocks(state, section_id) if not _is_drawing_only(root)
    )
    if len(top) > 1:
        raise UnsupportedBlocklyStructureError(
            f"{section_id}: a section is one construct, but this workspace has "
            f"{len(top)} top-level blocks ({_describe_roots(top)}). Attach each extra "
            "block inside the function, or delete it."
        )
    table = _FragmentTable(_fragments(preserved, section_id), section_id)
    if not top:
        # No container block. Either the section was never representable (its
        # whole body is preserved source) or the editor handed back an empty
        # canvas. Both are the same shape here; deciding whether that is an
        # acceptable EDIT is `app/build/section_blockly.py`'s job, because only
        # it knows what the section looked like before.
        return BlocklySection(
            section_id=section_id,
            block=None,
            preserved=tuple(source for _, source in table.section_level()),
        )
    # Which records are drawn is decided over the WHOLE tree before any body
    # is assembled: a drawing may have been moved out of the body its record
    # names into one assembled earlier or later, and the index fallback must
    # not place a fragment that a drawing is also going to place.
    table.observe_drawings(top[0])
    block = _block(top[0], catalog, section_id, depth=1, table=table)
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


def _is_drawing_only(root: Mapping[str, Any]) -> bool:
    """True when a top-level block is a chain of NOTHING but read-only drawings.

    Every link must be a `preserved_source` that carries no `inputs`, and the
    `next` chain must end in nothing but more of the same. Anything else - a real
    block under a drawing, a drawing with a forged input, a malformed link - is
    not a drawing-only chain and is left to count as a construct (and to be
    refused by the block reader if it is the section's only root). Iterative, so
    a long chain of client JSON cannot recurse.
    """
    node: Any = root
    while True:
        if not isinstance(node, Mapping):
            return False
        if node.get("type") != PRESERVED_BLOCK_TYPE or node.get("inputs"):
            return False
        following = node.get("next")
        if following is None:
            return True
        if not isinstance(following, Mapping):
            return False
        node = following.get("block")


#: Extra roots named in a refusal, and how long one name may be: the payload is
#: untrusted, so the message that echoes it is bounded.
_MAX_ROOTS_NAMED = 5
_MAX_LABEL_CHARS = 64


def _label(block: Mapping[str, Any]) -> str:
    """`type[id]` for one block, tolerating a missing or non-text type or id."""
    kind = block.get("type")
    ident = block.get("id")
    text = kind if isinstance(kind, str) and kind else "<untyped block>"
    if isinstance(ident, str) and ident:
        text += f"[{ident}]"
    return text if len(text) <= _MAX_LABEL_CHARS else text[: _MAX_LABEL_CHARS - 1] + "…"


def _describe_roots(roots: Iterable[Mapping[str, Any]]) -> str:
    """The roots as `type[id]`, for a refusal a student or an evaluator can act on.

    A root that is a drawing with a real block chained under it is named with
    that block too, because the drawing alone is exactly what this reader
    otherwise ignores and a bare `preserved_source[...]` would read as a
    contradiction.
    """
    named: list[str] = []
    for root in roots:
        label = _label(root)
        if root.get("type") == PRESERVED_BLOCK_TYPE:
            node: Any = root
            while isinstance(node, Mapping) and node.get("type") == PRESERVED_BLOCK_TYPE:
                following = node.get("next")
                node = following.get("block") if isinstance(following, Mapping) else None
            if isinstance(node, Mapping):
                label += f" carrying {_label(node)}"
        named.append(label)
    shown = ", ".join(named[:_MAX_ROOTS_NAMED])
    if len(named) > _MAX_ROOTS_NAMED:
        shown += f", and {len(named) - _MAX_ROOTS_NAMED} more"
    return shown


class _Fragment:
    """One preserved record, parsed: where it lives and what it says."""

    __slots__ = ("index", "source", "parent_id", "input_name", "fragment_id")

    def __init__(
        self,
        index: int,
        source: PreservedSource,
        parent_id: str | None,
        input_name: str | None,
        fragment_id: str | None = None,
    ) -> None:
        self.index = index
        self.source = source
        self.parent_id = parent_id
        self.input_name = input_name
        self.fragment_id = fragment_id


class _FragmentTable:
    """The records of a section, ready to be placed where the workspace put them.

    A record whose `id` a drawn `preserved_source` block carries is placed at
    that drawing's position in its chain (`drawn`). Every other record is
    spliced into the body its locator names, at its index (`take`).

    A record with no `parentId` is a legacy section-level record: it belongs to
    the section's own container, exactly as before records carried a locator.
    `parentId == section_id` names the same container. Any other parent is a
    block id, matched against the `id` of a block the workspace actually holds.
    """

    def __init__(self, fragments: list[_Fragment], section_id: str) -> None:
        self._section_id = section_id
        self._by_id: dict[str, _Fragment] = {}
        self._drawn: set[str] = set()
        self._by_body: dict[tuple[str, str | None], list[tuple[int, _Fragment]]] = {}
        for fragment in fragments:
            if fragment.fragment_id is not None:
                if fragment.fragment_id in self._by_id:
                    raise InvalidBlocklyWorkspaceError(
                        f"{section_id}: two preserved fragments share the id "
                        f"{fragment.fragment_id!r}"
                    )
                self._by_id[fragment.fragment_id] = fragment
            parent = fragment.parent_id or section_id
            self._by_body.setdefault((parent, fragment.input_name), []).append(
                (fragment.index, fragment)
            )
        for key, items in self._by_body.items():
            indices = [index for index, _ in items]
            if len(set(indices)) != len(indices):
                raise InvalidBlocklyWorkspaceError(
                    f"{section_id}: two preserved fragments claim the same body index"
                )
            items.sort(key=lambda item: item[0])

    def observe_drawings(self, root: Mapping[str, Any]) -> None:
        """Note which records the workspace draws, refusing a record drawn twice.

        Iterative over the raw JSON, so it needs no depth guard of its own;
        the block reader that follows enforces `MAX_BLOCK_DEPTH` and every
        shape rule. A drawing whose id names no record is forged and is not
        noted - it will be skipped where it sits.
        """
        pending: list[Any] = [root]
        while pending:
            node = pending.pop()
            if isinstance(node, list):
                pending.extend(node)
                continue
            if not isinstance(node, Mapping):
                continue
            if node.get("type") == PRESERVED_BLOCK_TYPE:
                drawn_id = node.get("id")
                if isinstance(drawn_id, str) and drawn_id in self._by_id:
                    if drawn_id in self._drawn:
                        raise InvalidBlocklyWorkspaceError(
                            f"{self._section_id}: preserved fragment {drawn_id!r} is drawn "
                            "more than once"
                        )
                    self._drawn.add(drawn_id)
            pending.extend(node.values())

    def drawn(self, drawing: Mapping[str, Any]) -> PreservedSource | None:
        """The RECORD's fragment for a drawn `preserved_source` block, or None.

        Only the drawing's `id` is consulted; its text never is. None for a
        drawing that names no record (forged, or its record was CLEARed).
        """
        drawn_id = drawing.get("id")
        if not isinstance(drawn_id, str) or drawn_id not in self._drawn:
            return None
        return self._by_id[drawn_id].source

    def _undrawn(self, items) -> list[tuple[int, PreservedSource]]:
        return [
            (index, fragment.source)
            for index, fragment in items
            if fragment.fragment_id not in self._drawn
        ]

    def section_level(self) -> tuple[tuple[int, PreservedSource], ...]:
        """The fragments of the section's own body, primary input, in order."""
        return tuple(self._undrawn(self._by_body.get((self._section_id, None), ()))) or tuple(
            item
            for (parent, _), items in self._by_body.items()
            if parent == self._section_id
            for item in self._undrawn(items)
        )

    def take(
        self, parent_id: str | None, input_name: str, primary: str | None
    ) -> tuple[tuple[int, PreservedSource], ...]:
        """The UNDRAWN fragments of `parent_id`'s `input_name` body, in index order.

        A record that names no input belongs to the block's PRIMARY body. A
        drawn record is excluded wherever its drawing now sits: `drawn` has
        already placed it.
        """
        if parent_id is None:
            return ()
        found = self._undrawn(self._by_body.get((parent_id, input_name), ()))
        if input_name == primary:
            found.extend(self._undrawn(self._by_body.get((parent_id, None), ())))
        return tuple(sorted(found, key=lambda item: item[0]))


def _fragments(preserved: Iterable[Mapping[str, Any]], section_id: str) -> list[_Fragment]:
    """The preserved records, parsed and validated.

    Indices must be non-negative integers; whether they FIT the body they name
    is checked when that body is assembled, since that is the first point its
    length is known. Locators are optional text (see `_FragmentTable`).
    """
    items: list[_Fragment] = []
    for record in _each_mapping(preserved, f"{section_id}: preserved fragments"):
        index = record.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise InvalidBlocklyWorkspaceError(
                f"{section_id}: a preserved fragment's index must be a non-negative "
                f"integer, got {index!r}"
            )
        parent = record.get("parentId")
        input_name = record.get("input")
        fragment_id = record.get("id")
        for label, value in (("id", fragment_id), ("parentId", parent), ("input", input_name)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise InvalidBlocklyWorkspaceError(
                    f"{section_id}: a preserved fragment's {label} must be text, got {value!r}"
                )
        items.append(
            _Fragment(
                index,
                PreservedSource(
                    text=_text(record, "text", f"{section_id}: a preserved fragment"),
                    reason=_reason(record, section_id),
                ),
                parent,
                input_name,
                fragment_id,
            )
        )
    return items


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
    table: _FragmentTable | None = None,
) -> BlocklyBlock:
    """One block node and everything hanging off it.

    `table` holds the section's preserved records. Each statement input this
    block's catalog definition declares is assembled from the blocks the
    workspace holds in it PLUS the fragments the table names for it - looked up
    by the block's own `id` (the section's container, at depth 1, is addressed
    by the section id and needs no id from the payload). A block whose payload
    carries no `id` simply has no fragments to receive.
    """
    if depth > MAX_BLOCK_DEPTH:
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: blocks nest deeper than {MAX_BLOCK_DEPTH} levels"
        )
    block_type = _text(state, "type", f"{section_id}: a block")
    definition = block_definition_for_type(block_type, catalog)

    fields = _fields(state, block_type, section_id)
    present, bodies, values = _inputs(state, definition, catalog, section_id, depth, table)

    declared = [item.name for item in definition.inputs if item.value_type is ValueType.STATEMENTS]
    primary = declared[0] if declared else None
    own_id = state.get("id")
    parent_key = section_id if depth == 1 else (own_id if isinstance(own_id, str) else None)

    spliced: dict[str, tuple[BlocklyBlock | PreservedSource, ...]] = {}
    for name in declared:
        fragments = () if table is None else table.take(parent_key, name, primary)
        spliced[name] = _splice(bodies.get(name, ()), fragments, block_type, section_id)

    body_input: str | None = None
    body: tuple[BlocklyBlock | PreservedSource, ...] = ()
    branches: list[BlocklyBranch] = []
    for name in declared:
        items = spliced[name]
        if not items and name not in present:
            continue
        if name == primary:
            body_input, body = name, items
        else:
            branches.append(BlocklyBranch(name=name, items=items))
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
            branches=tuple(branches),
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
    table: _FragmentTable | None,
) -> tuple[
    set[str],
    dict[str, tuple[BlocklyBlock | PreservedSource, ...]],
    tuple[BlocklyValueInput, ...],
]:
    """The block's `inputs`: its statement bodies, and its value sockets.

    Blockly serializes both under the same `inputs` object, so which is which
    is read off the CATALOG: an input the block declares as STATEMENTS is a
    `next`-linked body, any other declared input is a socket holding exactly ONE
    value block, and an input the catalog does not declare for this block at all
    is refused rather than guessed at. Returns `(names of the bodies present,
    each body's real blocks, value inputs)`.

    A body's chain holds real blocks and, where a `preserved_source` drawing
    sits, the RECORD's fragment for it (`_chain`); fragments with no drawing
    are re-inserted from the records by `_block`.
    """
    raw = state.get("inputs")
    if raw is None:
        return set(), {}, ()
    if not isinstance(raw, Mapping):
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: 'inputs' must be an object"
        )
    declared = {item.name: item.value_type for item in definition.inputs}
    present: set[str] = set()
    bodies: dict[str, tuple[BlocklyBlock | PreservedSource, ...]] = {}
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
            present.add(name)
            bodies[name] = _chain(node, catalog, section_id, depth, table)
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
            BlocklyValueInput(name=name, block=_block(node, catalog, section_id, depth=depth + 1, table=table))
        )
    return present, bodies, tuple(values)


def _chain(
    node: Any,
    catalog: BlockCatalog,
    section_id: str,
    depth: int,
    table: _FragmentTable | None,
) -> tuple[BlocklyBlock | PreservedSource, ...]:
    """A `next`-linked stack, flattened in order - the inverse of `_chain`.

    Iterative rather than recursive down the `next` axis: a statement stack is
    a LIST, its length is not bounded by anything sensible, and recursing per
    statement would make a long-but-legitimate body look like an attack. Depth
    still guards nesting, which is the axis that actually nests.

    A `preserved_source` block is the DRAWING of a fragment a record already
    carries (see `PRESERVED_BLOCK_TYPE`). Its position in this chain is where
    the fragment goes; what goes there is the RECORD's fragment, looked up by
    the drawing's id (`_FragmentTable.drawn`) - its text is never read. A
    drawing that names no record is skipped.
    """
    blocks: list[BlocklyBlock | PreservedSource] = []
    current: Any = node
    while current is not None:
        if not isinstance(current, Mapping):
            raise InvalidBlocklyWorkspaceError(
                f"{section_id}: a block must be an object, got {type(current).__name__}"
            )
        if current.get("type") == PRESERVED_BLOCK_TYPE:
            fragment = None if table is None else table.drawn(current)
            if fragment is not None:
                blocks.append(fragment)
        else:
            blocks.append(_block(current, catalog, section_id, depth=depth + 1, table=table))
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
    items: tuple[BlocklyBlock | PreservedSource, ...],
    fragments: tuple[tuple[int, PreservedSource], ...],
    block_type: str,
    section_id: str,
) -> tuple[BlocklyBlock | PreservedSource, ...]:
    """Re-insert UNDRAWN preserved fragments at the body positions they came from.

    `items` is the chain as the workspace holds it - blocks, and the drawn
    fragments already in their drawn positions. `fragments` are the records
    with no drawing (see `_FragmentTable.take`), placed by index: the inverse
    of `BlocklySection.records`, which numbered them against the body they sat
    in. Inserting in ascending index order leaves each one exactly at its
    index, so a body with no drawings at all comes back as it always did.
    """
    if not fragments:
        return items
    total = len(items) + len(fragments)
    out_of_range = [index for index, _ in fragments if index >= total]
    if out_of_range:
        raise InvalidBlocklyWorkspaceError(
            f"{section_id}: {block_type}: preserved fragment index "
            f"{out_of_range[0]} is past the end of a {total}-item body"
        )
    body = list(items)
    for index, fragment in fragments:
        body.insert(index, fragment)
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
