"""Regenerate the frontend's copy of the master Blockly block catalog.

Run from the `backend/` directory after editing anything under `app/blockly/`:

    python scripts/export_blockly_catalog.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.blockly.catalog import default_block_catalog  # noqa: E402
from app.blockly.export import FRONTEND_MODULE_PATH, render_frontend_module  # noqa: E402


def main() -> None:
    FRONTEND_MODULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FRONTEND_MODULE_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_frontend_module(default_block_catalog))
    print(f"wrote {FRONTEND_MODULE_PATH} ({len(default_block_catalog)} blocks)")


if __name__ == "__main__":
    main()
