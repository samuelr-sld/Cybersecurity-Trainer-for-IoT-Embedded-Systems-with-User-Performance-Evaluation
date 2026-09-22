"""The master block catalog: every category and block, validated as a set.

Mirrors `app/panels/panels.py`'s `PanelRegistry` and
`app/scenarios/registry.py`'s `ScenarioRegistry`: one explicit table, built
once, with structural rules enforced at construction so an invalid catalog can
never exist. Nothing here reads a file, imports Blockly, or knows any panel —
a panel package will later *select* from this catalog, it does not extend it.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.blockly.categories import CATEGORIES
from app.blockly.definitions import arduino, communication, hardware, programming, storage
from app.blockly.dependencies import DEPENDENCIES
from app.blockly.models import BlockCategory, BlockDefinition, CatalogError, ImplementationStatus


class BlockCatalog:
    """Ordered categories and blocks, indexed by id."""

    def __init__(self, categories: Iterable[BlockCategory], blocks: Iterable[BlockDefinition]) -> None:
        self._categories = tuple(categories)
        self._blocks = tuple(blocks)
        self._category_index = self._index_categories()
        self._block_index = self._index_blocks()
        self._by_category = self._group_blocks()
        self._check_blockly_types()

    def _index_categories(self) -> dict[str, BlockCategory]:
        indexed: dict[str, BlockCategory] = {}
        for category in self._categories:
            if category.category_id in indexed:
                raise CatalogError(f"duplicate category id: {category.category_id}")
            indexed[category.category_id] = category
        return indexed

    def _index_blocks(self) -> dict[str, BlockDefinition]:
        indexed: dict[str, BlockDefinition] = {}
        for block in self._blocks:
            if block.block_id in indexed:
                raise CatalogError(f"duplicate block id: {block.block_id}")
            if block.category_id not in self._category_index:
                raise CatalogError(f"{block.block_id}: unknown category {block.category_id!r}")
            for dependency_id in block.dependencies:
                if dependency_id not in DEPENDENCIES:
                    raise CatalogError(f"{block.block_id}: unknown dependency {dependency_id!r}")
            indexed[block.block_id] = block
        return indexed

    def _group_blocks(self) -> dict[str, tuple[BlockDefinition, ...]]:
        grouped: dict[str, list[BlockDefinition]] = {category_id: [] for category_id in self._category_index}
        for block in self._blocks:
            grouped[block.category_id].append(block)
        return {category_id: tuple(items) for category_id, items in grouped.items()}

    def _check_blockly_types(self) -> None:
        claimed: dict[str, str] = {}
        for block in self._blocks:
            if block.blockly_type is None:
                continue
            if block.blockly_type in claimed:
                raise CatalogError(
                    f"Blockly type {block.blockly_type!r} claimed by both {claimed[block.blockly_type]} and {block.block_id}"
                )
            claimed[block.blockly_type] = block.block_id

    @property
    def categories(self) -> tuple[BlockCategory, ...]:
        """Categories in toolbox order."""
        return self._categories

    @property
    def blocks(self) -> tuple[BlockDefinition, ...]:
        """Every block, in category order then definition order."""
        return self._blocks

    def category(self, category_id: str) -> BlockCategory | None:
        return self._category_index.get(category_id)

    def block(self, block_id: str) -> BlockDefinition | None:
        return self._block_index.get(block_id)

    def blocks_in(self, category_id: str) -> tuple[BlockDefinition, ...]:
        """The blocks of one category, or an empty tuple for an unknown id."""
        return self._by_category.get(category_id, ())

    def with_status(self, status: ImplementationStatus) -> tuple[BlockDefinition, ...]:
        return tuple(block for block in self._blocks if block.status is status)

    def __contains__(self, block_id: object) -> bool:
        return block_id in self._block_index

    def __len__(self) -> int:
        return len(self._blocks)


def build_default_catalog() -> BlockCatalog:
    """Assemble the platform's master catalog from its definition modules."""
    blocks = (
        programming.BLOCKS
        + arduino.BLOCKS
        + communication.BLOCKS
        + hardware.BLOCKS
        + storage.BLOCKS
    )
    return BlockCatalog(CATEGORIES, blocks)


#: Process-wide master catalog. Immutable, so sharing it is safe.
default_block_catalog = build_default_catalog()
