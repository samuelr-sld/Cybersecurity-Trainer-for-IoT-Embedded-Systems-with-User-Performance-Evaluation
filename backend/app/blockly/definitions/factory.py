"""Compact constructors for `BlockDefinition`, one factory per category.

Exists so ~200 definitions stay readable and consistent: the factory owns the
category id, defaults `semantic_operation` to the block id (blocks that share
an operation pass `op=`), and turns `implemented_as="<blockly type>"` into the
matching status, Blockly type and generator id together so the three can
never disagree.

Inputs are `(NAME, ValueType)` or `(NAME, ValueType, "description")` tuples.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.blockly.models import (
    BlockDefinition,
    BlockInput,
    BlockKind,
    Capability,
    ImplementationStatus,
    ValueType,
)

NUMBER = ValueType.NUMBER
TEXT = ValueType.TEXT
BOOLEAN = ValueType.BOOLEAN
LIST = ValueType.LIST
PIN = ValueType.PIN
ANY = ValueType.ANY
BODY = ValueType.STATEMENTS

#: The code-generation target the existing Blockly POC generators emit.
GENERATOR_TARGET = "arduino_cpp"

InputSpec = tuple


def _inputs(specs: tuple[InputSpec, ...]) -> tuple[BlockInput, ...]:
    return tuple(BlockInput(*spec) for spec in specs)


@dataclass(frozen=True)
class CategoryFactory:
    """Builds definitions that all belong to one category."""

    category_id: str

    def _build(
        self,
        kind: BlockKind,
        block_id: str,
        name: str,
        description: str,
        *,
        op: str | None,
        inputs: tuple[InputSpec, ...],
        output: ValueType | None,
        deps: tuple[str, ...],
        caps: tuple[Capability, ...],
        implemented_as: str | None,
    ) -> BlockDefinition:
        status = ImplementationStatus.CATALOGED
        generator_id = None
        if implemented_as is not None:
            status = ImplementationStatus.IMPLEMENTED
            generator_id = f"{GENERATOR_TARGET}:{implemented_as}"
        return BlockDefinition(
            block_id=block_id,
            category_id=self.category_id,
            display_name=name,
            description=description,
            kind=kind,
            semantic_operation=op or block_id,
            inputs=_inputs(inputs),
            output=output,
            dependencies=deps,
            capabilities=caps,
            status=status,
            blockly_type=implemented_as,
            generator_id=generator_id,
        )

    def value(
        self,
        block_id: str,
        name: str,
        description: str,
        output: ValueType,
        *,
        op: str | None = None,
        inputs: tuple[InputSpec, ...] = (),
        deps: tuple[str, ...] = (),
        caps: tuple[Capability, ...] = (),
        implemented_as: str | None = None,
    ) -> BlockDefinition:
        return self._build(
            BlockKind.VALUE, block_id, name, description,
            op=op, inputs=inputs, output=output, deps=deps, caps=caps, implemented_as=implemented_as,
        )

    def expression(
        self,
        block_id: str,
        name: str,
        description: str,
        output: ValueType,
        inputs: tuple[InputSpec, ...],
        *,
        op: str | None = None,
        deps: tuple[str, ...] = (),
        caps: tuple[Capability, ...] = (),
        implemented_as: str | None = None,
    ) -> BlockDefinition:
        return self._build(
            BlockKind.EXPRESSION, block_id, name, description,
            op=op, inputs=inputs, output=output, deps=deps, caps=caps, implemented_as=implemented_as,
        )

    def statement(
        self,
        block_id: str,
        name: str,
        description: str,
        inputs: tuple[InputSpec, ...] = (),
        *,
        op: str | None = None,
        deps: tuple[str, ...] = (),
        caps: tuple[Capability, ...] = (),
        implemented_as: str | None = None,
    ) -> BlockDefinition:
        return self._build(
            BlockKind.STATEMENT, block_id, name, description,
            op=op, inputs=inputs, output=None, deps=deps, caps=caps, implemented_as=implemented_as,
        )

    def container(
        self,
        block_id: str,
        name: str,
        description: str,
        inputs: tuple[InputSpec, ...],
        *,
        op: str | None = None,
        deps: tuple[str, ...] = (),
        caps: tuple[Capability, ...] = (),
        implemented_as: str | None = None,
    ) -> BlockDefinition:
        return self._build(
            BlockKind.CONTAINER, block_id, name, description,
            op=op, inputs=inputs, output=None, deps=deps, caps=caps, implemented_as=implemented_as,
        )
