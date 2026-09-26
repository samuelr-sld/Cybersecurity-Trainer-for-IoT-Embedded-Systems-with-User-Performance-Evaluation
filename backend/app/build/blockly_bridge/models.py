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
    losing it. It is PROVENANCE exactly like `source_text` — not part of the
    Blockly workspace state (`to_state()` never writes it; a real editor never
    sees or sends it) and not derived from a field. A REAL edit over the wire
    still needs `app/build/section_blockly.py::program_with_section` to copy it
    across explicitly, because `app/build/blockly_bridge/workspace_state.py`
    parses a browser's JSON, which never carried this field to begin with; this
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
        if len(set(socket_names)) != len(socket_names) or self.body_input in socket_names:
            raise BlocklyModelError(f"{self.block_type}: duplicate input names")
        if set(socket_names) & set(names):
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

    def to_state(self) -> dict[str, Any]:
        """This block as Blockly serialization JSON, body chained by `next`.

        Only blocks appear in the chain. Preserved fragments are reported
        separately by `BlocklyProgram.preserved`, with the index that says
        where they sat — a Blockly workspace state cannot hold a node that is
        not a block, and faking one would be the dishonesty this phase is
        built to avoid.
        """
        state: dict[str, Any] = {"type": self.block_type}
        if self.fields:
            state["fields"] = {field.name: field.value for field in self.fields}
        inputs: dict[str, Any] = {
            item.name: {"block": item.block.to_state()} for item in self.values
        }
        chain = _chain(self.child_blocks)
        if chain is not None:
            assert self.body_input is not None  # guaranteed by __post_init__
            inputs[self.body_input] = {"block": chain}
        if inputs:
            state["inputs"] = inputs
        return state


def _chain(blocks: tuple[BlocklyBlock, ...]) -> dict[str, Any] | None:
    """A statement stack as one nested `next`-linked state, or None if empty."""
    state: dict[str, Any] | None = None
    for block in reversed(blocks):
        current = block.to_state()
        if state is not None:
            current["next"] = {"block": state}
        state = current
    return state


@dataclass(frozen=True)
class PreservedRecord:
    """One preserved fragment, located: which section, and where in its body.

    The index is the fragment's position among ALL of the section's ordered
    items, blocks included, so `supported / unsupported / supported` comes
    back as indices 0, 1, 2 rather than as an undated footnote.
    """

    section_id: str
    index: int
    source: PreservedSource

    def to_record(self) -> dict[str, Any]:
        return {"sectionId": self.section_id, "index": self.index, **self.source.to_record()}


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
        """Every preserved fragment of this section, located by index."""
        return tuple(
            PreservedRecord(section_id=self.section_id, index=index, source=item)
            for index, item in enumerate(self.items)
            if isinstance(item, PreservedSource)
        )

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
            state = self.block.to_state()
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
        for index, block in enumerate(self.blocks):
            state = block.to_state()
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
