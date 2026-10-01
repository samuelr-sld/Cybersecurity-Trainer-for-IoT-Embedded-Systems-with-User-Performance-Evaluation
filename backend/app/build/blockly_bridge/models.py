"""The Blockly-side representation the adapter produces (Phase B4).

    BlocklyProgram
      -> BlocklySection       (one per SemanticSection, by the same id)
           -> BlocklyBlock    (a container block, when the section has one)
                -> body: an ORDERED MIX of
                     BlocklyBlock      a statement the catalog can draw
                     PreservedSource   source carried verbatim instead

WHY THIS MODEL EXISTS AT ALL. Blockly's own serialization format is a nested
dict of `{"type": ..., "fields": ..., "next": ...}`, which is a fine wire
format and a poor thing to build, validate or compare in Python. These frozen
dataclasses are the intermediate the adapter builds and the tests read;
`to_workspace_state()` renders them into the exact JSON
`Blockly.serialization.workspaces.load` accepts. The two are separate so the
adapter never hand-assembles nested dicts and a test never has to walk one.

PRESERVED SOURCE IS A FIRST-CLASS BODY ITEM, NOT A FOOTNOTE. Blockly has no
block for "some C++ nobody modelled", and inventing one would mean claiming
the toolbox can round-trip arbitrary firmware. So a body is an ordered tuple
whose items are EITHER a block OR a `PreservedSource`, and the position of a
preserved fragment inside that tuple is its position in the original body. A
consumer that renders only the blocks still knows, exactly, that source sat
between the second and third block and what it said. Nothing is dropped and
nothing is approximated.

THERE ARE TWO REASONS SOURCE ENDS UP PRESERVED, and they are kept distinct:

  * B3 never understood it — the reason is B3's own `UnsupportedReason`,
    carried through verbatim rather than restated in a parallel vocabulary;
  * B3 understood it but Blockly's block cannot HOLD one of its values (a
    named constant in a numeric field, a token outside a dropdown's option
    list) — the reason is this layer's `BridgeReason`.

NO PERMISSIONS, NO SESSIONS, NO FILES. Nothing here says whether a student may
edit a construct. `editable_section_ids` and `security_region_id` belong to
B2's `FileSegment`/`BuildProject` and stay there; this model answers only "how
is this construct represented in Blockly?". Nothing here reads a file, names a
panel, or holds UI state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.build.blockly_bridge.errors import BlocklyModelError
from app.build.blockly_bridge.signature_display import signature_extra_state
from app.build.semantic import UnsupportedReason

#: The same shape the block catalog requires of an input name, and therefore
#: of the Blockly field it becomes.
_FIELD_NAME_PATTERN = r"^[A-Z][A-Z0-9_]*$"

#: Blockly's serialization format version. 0 is what `blockly/core` writes and
#: reads today; it is a property of the FORMAT, not of this codebase.
BLOCKLY_LANGUAGE_VERSION = 0

#: Where the first top-level container block is placed, and how far apart
#: successive ones are. A deterministic default so two conversions of one
#: program are byte-identical — a frontend is free to re-layout afterwards.
FIRST_BLOCK_X = 24
FIRST_BLOCK_Y = 24
BLOCK_VERTICAL_GAP = 320

#: The Blockly type a `PreservedSource` is DRAWN as. Not a catalog block, no
#: semantic operation and no generator. The reverse reader (`workspace_state.py`)
#: reads only its `id` and its position in a chain, at every depth: the
#: position is where the fragment goes, and the fragment placed there is the
#: record with that id in the `preserved` list beside the workspace - never
#: this block's field.
PRESERVED_BLOCK_TYPE = "preserved_source"
PRESERVED_TEXT_FIELD = "TEXT"


class BridgeReason(str, Enum):
    """Why an UNDERSTOOD statement still could not be drawn as a block.

    Deliberately separate from B3's `UnsupportedReason`, which says why the IR
    never understood a construct. A `BridgeReason` means the meaning was
    established and the editor is what fell short.

    FIELD_VALUE_NOT_REPRESENTABLE
        A value cannot occupy the Blockly field bound to its parameter: a
        named constant where the real block draws a numeric field
        (`delay(BUZZER_CHIRP_MS)`), or a token that is not one of a dropdown's
        declared options (`digitalWrite(PIN, true)`, whose field offers the
        tokens HIGH and LOW). Writing something else into the field would
        silently rewrite the student's source, so the statement is carried
        verbatim instead.
    """

    FIELD_VALUE_NOT_REPRESENTABLE = "field_value_not_representable"


@dataclass(frozen=True)
class PreservedSource:
    """Source text carried verbatim because no block represents it.

    `reason` is either B3's `UnsupportedReason` (the IR never understood this)
    or this layer's `BridgeReason` (understood, undrawable). The union rather
    than a restated enum is deliberate: duplicating B3's six members here
    would create two vocabularies for one fact.
    """

    text: str
    reason: UnsupportedReason | BridgeReason

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise BlocklyModelError("preserved source text must be non-empty")
        if not isinstance(self.reason, (UnsupportedReason, BridgeReason)):
            raise BlocklyModelError(f"invalid preservation reason: {self.reason!r}")

    @property
    def understood_by_the_ir(self) -> bool:
        """True when B3 did represent this statement and only B4 could not."""
        return isinstance(self.reason, BridgeReason)

    def to_record(self) -> dict[str, Any]:
        """This fragment as JSON-safe data, camelCase for the frontend."""
        return {
            "text": self.text,
            "reason": self.reason.value,
            "understoodByTheIr": self.understood_by_the_ir,
        }


@dataclass(frozen=True)
class BlocklyField:
    """One named field of a block and the exact token it holds.

    The value is always TEXT, because a Blockly field holds text: it is the
    operand as it was WRITTEN (`1000`, `START_BUTTON`, `INPUT_PULLUP`), never
    a resolved value. `app/build/semantic/models.py` does not resolve names
    and neither does this layer.

    Non-empty, but whitespace IS a value: a `text.literal` field holding a
    single space is the separator `" "` Panel 1's token parser searches for,
    and trimming or refusing it would change what the student wrote. Whether
    a given field may hold whitespace is its reader's decision (a NAME field
    must still be an identifier).
    """

    name: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.match(_FIELD_NAME_PATTERN, self.name):
            raise BlocklyModelError(f"invalid field name: {self.name!r}")
        if not isinstance(self.value, str) or not self.value:
            raise BlocklyModelError(f"field {self.name}: value must be non-empty text")


@dataclass(frozen=True)
class BlocklyValueInput:
    """One VALUE socket of a block and the value block plugged into it.

    `logic.if`'s CONDITION holding a `logic.less_equal`, `text.substring`'s
    FROM holding a `math.number`. Distinct from a block's statement BODY: a
    value input holds exactly ONE block, which yields a value and never
    chains through `next`. Serialized by Blockly under the same `inputs`
    object as a body, so `to_state()` writes both there.
    """

    name: str
    block: BlocklyBlock

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.match(_FIELD_NAME_PATTERN, self.name):
            raise BlocklyModelError(f"invalid value input name: {self.name!r}")
        if not isinstance(self.block, BlocklyBlock):
            raise BlocklyModelError(f"value input {self.name}: not a block: {self.block!r}")


@dataclass(frozen=True)
class BlocklyBranch:
    """A SECOND (or later) statement input of a block: `else`, a `for`'s step.

    A block's first statement input is `BlocklyBlock.body_input`/`body`; a block
    with more than one (`logic_if`: DO, ELSE_IF, ELSE; `for_loop`: DO, INIT,
    STEP) carries the others here, each by the input name the catalog declares.
    Same item type as a body - blocks and preserved source, in order.
    """

    name: str
    items: tuple[BlocklyBlock | PreservedSource, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.match(_FIELD_NAME_PATTERN, self.name):
            raise BlocklyModelError(f"invalid branch input name: {self.name!r}")
        for item in self.items:
            if not isinstance(item, (BlocklyBlock, PreservedSource)):
                raise BlocklyModelError(f"branch {self.name}: not a body item: {item!r}")


@dataclass(frozen=True)
class BlocklyBlock:
    """One block: a catalog block type, its field values, and its body.

    Three identities travel together on purpose, and none is derived from
    another at read time:

      `operation_id`  what this means      (B3's stable id)
      `block_id`      which catalog entry  (`app/blockly/`'s stable id)
      `block_type`    what Blockly calls it

    A statement block has fields and no body; a container block has a body and
    (today) no fields. Both shapes are one class because a future container
    that also takes a field, or a statement that nests a body, changes no type
    here.

    `source_text` is the exact source this block was built from, when it was
    built from source at all. It is PROVENANCE, never a value: nothing reads it
    to decide what the block means (the fields say that), and it is not part of
    the Blockly workspace state. It exists because the IR keeps the source text
    of every statement — a preserved fragment and an understood statement
    alike — and a block that forgot it could only be returned to the IR by
    generating C++ for it, which is B6's job. A block authored in the editor
    has no source and leaves it None.

    `container_signature` is the SAME KIND OF THING, added for a generic named-
    function container (`functions.implementation` — see
    `app/build/semantic/models.py::SemanticSection.signature`): the preserved,
    exact C++ declarator of the function this container's body belongs to
    (`"static void applyCommand(const String &message)"`), carried so this
    IN-MEMORY object can round-trip back to an equal `SemanticSection` without
    losing it. It is PROVENANCE exactly like `source_text` and not derived from
    a field. Its one appearance in the workspace state is a DISPLAY-ONLY
    `extraState` describing it for the block's read-only header (P4.2,
    `signature_display.py`), which the reverse reader never reads. So a REAL
    edit over the wire still needs `app/build/section_blockly.py::
    program_with_section` to copy the section's own signature across
    explicitly, because `app/build/blockly_bridge/workspace_state.py` never
    reconstructs one from a browser's JSON; this
    is what makes the plain in-process `program_to_blockly` -> `blockly_to_
    semantic` round trip (no browser, no section_blockly.py) equal on its own.
    None for `program.setup`/`program.loop` and for every statement block.
    """

    operation_id: str
    block_id: str
    block_type: str
    source_text: str | None = None
    container_signature: str | None = None
    fields: tuple[BlocklyField, ...] = ()
    body_input: str | None = None
    body: tuple[BlocklyBlock | PreservedSource, ...] = ()
    #: VALUE sockets and the value block in each — see `BlocklyValueInput`.
    values: tuple[BlocklyValueInput, ...] = ()
    #: Statement inputs beyond `body_input` - see `BlocklyBranch`.
    branches: tuple[BlocklyBranch, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("operation id", self.operation_id),
            ("catalog block id", self.block_id),
            ("Blockly type", self.block_type),
        ):
            if not isinstance(value, str) or not value.strip():
                raise BlocklyModelError(f"{name} must be a non-empty string: {value!r}")
        if self.source_text is not None and (
            not isinstance(self.source_text, str) or not self.source_text.strip()
        ):
            raise BlocklyModelError(
                f"{self.block_type}: recorded source must be non-empty text or absent"
            )
        if self.container_signature is not None and (
            not isinstance(self.container_signature, str) or not self.container_signature.strip()
        ):
            raise BlocklyModelError(
                f"{self.block_type}: container signature must be non-empty text or absent"
            )
        names = [field.name for field in self.fields]
        for field in self.fields:
            if not isinstance(field, BlocklyField):
                raise BlocklyModelError(f"{self.block_type}: not a field: {field!r}")
        if len(set(names)) != len(names):
            raise BlocklyModelError(f"{self.block_type}: duplicate field names")
        for item in self.body:
            if not isinstance(item, (BlocklyBlock, PreservedSource)):
                raise BlocklyModelError(f"{self.block_type}: not a body item: {item!r}")
        if self.body and self.body_input is None:
            raise BlocklyModelError(
                f"{self.block_type}: a body needs the input name it is attached to"
            )
        if self.body_input is not None and not re.match(_FIELD_NAME_PATTERN, self.body_input):
            raise BlocklyModelError(f"{self.block_type}: invalid body input {self.body_input!r}")
        socket_names = [item.name for item in self.values]
        for item in self.values:
            if not isinstance(item, BlocklyValueInput):
                raise BlocklyModelError(f"{self.block_type}: not a value input: {item!r}")
        for branch in self.branches:
            if not isinstance(branch, BlocklyBranch):
                raise BlocklyModelError(f"{self.block_type}: not a branch: {branch!r}")
        branch_names = [branch.name for branch in self.branches]
        every_input = [*socket_names, *branch_names]
        if self.body_input is not None:
            every_input.append(self.body_input)
        if len(set(every_input)) != len(every_input):
            raise BlocklyModelError(f"{self.block_type}: duplicate input names")
        if set(every_input) & set(names):
            raise BlocklyModelError(f"{self.block_type}: a name is both a field and an input")

    def value(self, name: str) -> BlocklyBlock | None:
        """The value block plugged into this socket, or None if it is empty."""
        for item in self.values:
            if item.name == name:
                return item.block
        return None

    @property
    def child_blocks(self) -> tuple[BlocklyBlock, ...]:
        """The body's block items, in order — what Blockly actually chains."""
        return tuple(item for item in self.body if isinstance(item, BlocklyBlock))

    @property
    def preserved_items(self) -> tuple[tuple[int, PreservedSource], ...]:
        """The body's preserved fragments with their positions in the body."""
        return tuple(
            (index, item)
            for index, item in enumerate(self.body)
            if isinstance(item, PreservedSource)
        )

    def branch(self, name: str) -> BlocklyBranch | None:
        """The extra statement input with this name, or None."""
        for branch in self.branches:
            if branch.name == name:
                return branch
        return None

    @property
    def bodies(self) -> tuple[tuple[str, tuple[BlocklyBlock | PreservedSource, ...]], ...]:
        """Every statement input this block fills, primary first: `(name, items)`."""
        found: list[tuple[str, tuple[BlocklyBlock | PreservedSource, ...]]] = []
        if self.body_input is not None:
            found.append((self.body_input, self.body))
        found.extend((branch.name, branch.items) for branch in self.branches)
        return tuple(found)

    def child_id(self, block_id: str, input_name: str, index: int) -> str:
        """The id of the item at `index` in this block's `input_name` body.

        Deterministic and structural: the primary body's items are `<id>.<n>`,
        an extra input's are `<id>.<INPUT>.<n>`. It is how a `preserved`
        record names the block whose body holds it.
        """
        if input_name == self.body_input:
            return f"{block_id}.{index}"
        return f"{block_id}.{input_name}.{index}"

    def to_state(self, block_id: str | None = None) -> dict[str, Any]:
        """This block as Blockly serialization JSON, bodies chained by `next`.

        Preserved fragments are drawn too, as read-only `preserved_source`
        blocks at the position they hold in their body, so a student sees the
        C++ the toolbox cannot yet express instead of an empty container. They
        are display only: the authoritative copy of each fragment is reported
        separately by `BlocklySection.records`, and the reverse reader ignores
        these blocks.

        `block_id`, when given, is written as the block's `id` and derives the
        ids of every body item beneath it (`child_id`). Blockly keeps a
        provided id through load and save, which is what lets a record say
        WHICH block's body a fragment belongs to and survive an edit. Value
        blocks carry no body and no id.
        """
        state: dict[str, Any] = {"type": self.block_type}
        if block_id is not None:
            state["id"] = block_id
        # A named function's declarator, for the container's READ-ONLY header
        # (P4.2). Display only: Blockly round-trips it through the block's
        # saveExtraState/loadExtraState, the reverse reader never reads it, and
        # a submission's signature is always the section's own - see
        # `signature_display.py`.
        extra = signature_extra_state(self.container_signature)
        if extra is not None:
            state["extraState"] = extra
        if self.fields:
            state["fields"] = {field.name: field.value for field in self.fields}
        inputs: dict[str, Any] = {
            item.name: {"block": item.block.to_state()} for item in self.values
        }
        for name, items in self.bodies:
            chain = _chain(
                items, None if block_id is None else lambda index, n=name: self.child_id(block_id, n, index)
            )
            if chain is not None:
                inputs[name] = {"block": chain}
        if inputs:
            state["inputs"] = inputs
        return state


def _preserved_state(item: PreservedSource, block_id: str | None = None) -> dict[str, Any]:
    """A preserved fragment as its read-only, display-only Blockly block."""
    state: dict[str, Any] = {"type": PRESERVED_BLOCK_TYPE}
    if block_id is not None:
        state["id"] = block_id
    state["fields"] = {PRESERVED_TEXT_FIELD: item.text}
    return state


def _chain(items: tuple[BlocklyBlock | PreservedSource, ...], ids=None) -> dict[str, Any] | None:
    """A statement stack as one nested `next`-linked state, or None if empty.

    `ids`, when given, maps an item's index to the id it is written under.
    """
    state: dict[str, Any] | None = None
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        item_id = None if ids is None else ids(index)
        current = (
            _preserved_state(item, item_id)
            if isinstance(item, PreservedSource)
            else item.to_state(item_id)
        )
        if state is not None:
            current["next"] = {"block": state}
        state = current
    return state


@dataclass(frozen=True)
class PreservedRecord:
    """One preserved fragment, located: which section, and where in which body.

    `index` is the fragment's position among ALL the items of the body that
    holds it, blocks included, so `supported / unsupported / supported` comes
    back as indices 0, 1, 2 rather than as an undated footnote.

    `parent_id` and `input_name` say WHICH body: the id of the block whose
    statement input `input_name` holds the fragment (`section_id` itself for
    the section's own container; a `<section>.<n>...` path for a block nested
    inside it). `fragment_id` is the id of the fragment's own drawn block. All
    three are deterministic functions of the tree (`BlocklyBlock.child_id`), so
    a nested fragment is as authoritative as a section-level one: the record,
    not the drawing, is what the reverse reader trusts.
    """

    section_id: str
    index: int
    source: PreservedSource
    parent_id: str = ""
    input_name: str | None = None
    fragment_id: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "sectionId": self.section_id,
            "index": self.index,
            "id": self.fragment_id or f"{self.section_id}.{self.index}",
            "parentId": self.parent_id or self.section_id,
            "input": self.input_name,
            **self.source.to_record(),
        }


@dataclass(frozen=True)
class BlocklySection:
    """One semantic section's Blockly representation, under the same id.

    `section_id` is B1's `CodeSection.section_id`, which B2 reuses as a
    `FileSegment.region_id` and B3 reuses as a `SemanticSection.section_id`.
    A fourth name for one construct is exactly what this codebase avoids.

    A section either becomes a container BLOCK whose body holds its ordered
    items, or — when the IR gave it no container operation (a helper function,
    a callback, global declarations) — a run of preserved source and no block
    at all. Never both: a block's body already carries everything.
    """

    section_id: str
    block: BlocklyBlock | None
    preserved: tuple[PreservedSource, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.section_id, str) or not self.section_id.strip():
            raise BlocklyModelError(f"section id must be a non-empty string: {self.section_id!r}")
        if self.block is not None:
            if not isinstance(self.block, BlocklyBlock):
                raise BlocklyModelError(f"{self.section_id}: not a block: {self.block!r}")
            if self.preserved:
                raise BlocklyModelError(
                    f"{self.section_id}: a section with a block carries its preserved source "
                    "inside that block's body, not beside it"
                )
        for item in self.preserved:
            if not isinstance(item, PreservedSource):
                raise BlocklyModelError(f"{self.section_id}: not preserved source: {item!r}")

    @property
    def items(self) -> tuple[BlocklyBlock | PreservedSource, ...]:
        """This section's ordered items, however the section is represented."""
        return self.block.body if self.block is not None else self.preserved

    @property
    def records(self) -> tuple[PreservedRecord, ...]:
        """Every preserved fragment of this section, located - at any depth.

        Section-level fragments first-come in body order, then nested ones as
        they are met walking the tree depth first, so the list is stable and a
        record's `parent_id` always names a block that is drawn (see
        `BlocklyBlock.to_state`).
        """
        found: list[PreservedRecord] = []
        if self.block is None:
            for index, item in enumerate(self.preserved):
                found.append(
                    PreservedRecord(
                        section_id=self.section_id,
                        index=index,
                        source=item,
                        parent_id=self.section_id,
                        fragment_id=f"{self.section_id}.{index}",
                    )
                )
            return tuple(found)

        def walk(block: BlocklyBlock, block_id: str) -> None:
            for name, items in block.bodies:
                for index, item in enumerate(items):
                    child_id = block.child_id(block_id, name, index)
                    if isinstance(item, PreservedSource):
                        found.append(
                            PreservedRecord(
                                section_id=self.section_id,
                                index=index,
                                source=item,
                                parent_id=block_id,
                                input_name=name,
                                fragment_id=child_id,
                            )
                        )
                    else:
                        walk(item, child_id)

        walk(self.block, self.section_id)
        return tuple(found)

    @property
    def representable(self) -> bool:
        """Whether Blockly can draw this section at all.

        False when the IR established no container form for the construct — a
        helper function, a callback, a run of global declarations — so there is
        no block and the whole section is preserved source.

        THIS FLAG IS WHY AN EDITOR DOES NOT HAVE TO GUESS. An empty
        `to_workspace_state()` is produced by two completely different
        situations: a section whose body a student emptied, and a section
        Blockly has no vocabulary for yet. Rendering the second as a blank
        canvas would tell a student their firmware contains nothing, and
        accepting an edit to it would mean accepting a deletion nobody asked
        for. A consumer reads this flag, shows the preserved source read-only,
        and says the toolbox cannot express this construct YET — see
        `app/build/section_blockly.py`, which refuses an edit to such a section
        for exactly that reason.
        """
        return self.block is not None

    def to_workspace_state(self) -> dict[str, Any]:
        """This ONE section as `Blockly.serialization.workspaces.load` input.

        The section-scoped counterpart of `BlocklyProgram.to_workspace_state`,
        and the read half of the section -> Blockly contract: an editor opening
        one section loads exactly this, without being handed the rest of the
        firmware and without slicing a whole-program state itself.

        The section's container block is the single top-level block, at the
        same deterministic origin a program's first block gets, so two
        conversions of one section are byte-identical. A section with no
        container form yields no blocks — see `representable`.
        """
        blocks: list[dict[str, Any]] = []
        if self.block is not None:
            state = self.block.to_state(self.section_id)
            state["x"] = FIRST_BLOCK_X
            state["y"] = FIRST_BLOCK_Y
            blocks.append(state)
        return {"blocks": {"languageVersion": BLOCKLY_LANGUAGE_VERSION, "blocks": blocks}}

    def to_representation(self) -> dict[str, Any]:
        """Both halves of ONE section, as JSON-safe data.

        Exactly the shape `BlocklyProgram.to_representation()` uses, narrowed
        to one section and carrying the two things a section-addressed editor
        additionally needs: which section this is, and whether Blockly can draw
        it. Nothing here says whether a student may EDIT it — that is the
        `InteractionPolicy` a `BuildProject` carries (`app/build/policy.py`),
        and this package is forbidden permission vocabulary.
        """
        return {
            "sectionId": self.section_id,
            "representable": self.representable,
            "workspace": self.to_workspace_state(),
            "preserved": [record.to_record() for record in self.records],
        }


@dataclass(frozen=True)
class BlocklyProgram:
    """One source file as Blockly: its sections, in the document's order."""

    sections: tuple[BlocklySection, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for section in self.sections:
            if not isinstance(section, BlocklySection):
                raise BlocklyModelError(f"not a BlocklySection: {section!r}")
            if section.section_id in seen:
                raise BlocklyModelError(f"duplicate section id: {section.section_id!r}")
            seen.add(section.section_id)

    def section(self, section_id: str) -> BlocklySection | None:
        for section in self.sections:
            if section.section_id == section_id:
                return section
        return None

    @property
    def blocks(self) -> tuple[BlocklyBlock, ...]:
        """The top-level container blocks, in document order."""
        return tuple(
            section.block for section in self.sections if section.block is not None
        )

    @property
    def preserved(self) -> tuple[PreservedRecord, ...]:
        """Every preserved fragment in the program, in document order."""
        return tuple(record for section in self.sections for record in section.records)

    def to_workspace_state(self) -> dict[str, Any]:
        """The program as `Blockly.serialization.workspaces.load` input.

        Contains ONLY blocks, because that is all a Blockly workspace can
        hold. `preserved` is the other half of the representation and is
        rendered beside it by `to_representation()`.
        """
        blocks: list[dict[str, Any]] = []
        for index, section in enumerate(s for s in self.sections if s.block is not None):
            state = section.block.to_state(section.section_id)
            state["x"] = FIRST_BLOCK_X
            state["y"] = FIRST_BLOCK_Y + index * BLOCK_VERTICAL_GAP
            blocks.append(state)
        return {"blocks": {"languageVersion": BLOCKLY_LANGUAGE_VERSION, "blocks": blocks}}

    def to_representation(self) -> dict[str, Any]:
        """Both halves as JSON-safe data: the workspace and what it cannot hold."""
        return {
            "workspace": self.to_workspace_state(),
            "preserved": [record.to_record() for record in self.preserved],
        }
