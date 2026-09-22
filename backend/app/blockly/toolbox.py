"""Builds a Blockly `categoryToolbox` definition from the master catalog.

A toolbox can only reference block types Blockly actually has, so only blocks
with a `blockly_type` (status above CATALOGED) become flyout entries. Every
other cataloged block is invisible here — the toolbox never advertises a block
that cannot be dragged out.

`hide_empty_categories=False` yields the full category tree, which is what
proves the catalog and the toolbox agree; `True` yields what a student should
be shown today, categories with something usable in them.
"""

from __future__ import annotations

from typing import Any

from app.blockly.catalog import BlockCatalog


def build_toolbox(catalog: BlockCatalog, *, hide_empty_categories: bool = False) -> dict[str, Any]:
    """Blockly toolbox JSON: categories in catalog order, groups separated."""
    contents: list[dict[str, Any]] = []
    previous_group = None
    for category in catalog.categories:
        available = [block for block in catalog.blocks_in(category.category_id) if block.blockly_type is not None]
        if hide_empty_categories and not available:
            continue
        if previous_group is not None and category.group is not previous_group:
            contents.append({"kind": "sep"})
        previous_group = category.group
        contents.append(
            {
                "kind": "category",
                "name": category.display_name,
                "colour": str(category.hue),
                "contents": [{"kind": "block", "type": block.blockly_type} for block in available],
            }
        )
    return {"kind": "categoryToolbox", "contents": contents}
