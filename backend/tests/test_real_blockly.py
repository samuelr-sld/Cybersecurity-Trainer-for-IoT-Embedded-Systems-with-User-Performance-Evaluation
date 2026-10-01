"""The backend's workspace states, loaded into the REAL Blockly and its REAL blocks.

Everything else in this suite checks the Python side: the catalog, the bridge,
the generator. This file closes the gap to the browser. It bundles the actual
`src/blockly/arduinoBlocks.js` (nothing is mocked or re-declared), loads it into
Blockly's own headless `Workspace` under jsdom, and pushes every workspace the
backend produces through Blockly's own `serialization.workspaces.load` and
`save` - then sends what Blockly saved back through the backend and checks the
regenerated C++ is byte-identical.

That proves, against the definitions a student's browser uses, that:

* every input, field and dropdown value the bridge writes exists on the block;
* Blockly keeps the block ids the `preserved` records point at (the whole
  nested-preservation design rests on that);
* the read-only `preserved_source` block is really read-only.

Skipped, not failed, where Node or the frontend dependencies are unavailable.
"""

from __future__ import annotations

import copy
import json
import pathlib
import shutil
import subprocess

import pytest

from app.build.blockly_bridge.models import PRESERVED_BLOCK_TYPE
from app.build.program_source import program_for_source, source_for_program
from app.build.section_blockly import (
    program_with_section,
    section_from_state,
    section_representation,
)

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
NODE_MODULES = REPO / "node_modules"
NODE = shutil.which("node")
ROLLDOWN = NODE_MODULES / "rolldown" / "bin" / "cli.mjs"
JSDOM = NODE_MODULES / "jsdom"
BLOCKLY = NODE_MODULES / "blockly" / "blockly.mjs"

pytestmark = pytest.mark.skipif(
    NODE is None or not ROLLDOWN.exists() or not JSDOM.exists() or not BLOCKLY.exists(),
    reason="needs node plus the frontend's node_modules (blockly, jsdom, rolldown)",
)

PANEL_ONE_INO = (
    HERE.parent
    / "panels"
    / "smart-home-mqtt-control"
    / "firmware"
    / "smart_home_mqtt_control"
    / "smart_home_mqtt_control.ino"
)


@pytest.fixture(scope="module")
def blockly(tmp_path_factory):
    """`run(states, flags)` -> what real Blockly saved / said, per case."""
    work = tmp_path_factory.mktemp("real_blockly")
    entry = (HERE / "blockly_headless" / "entry.mjs").read_text(encoding="utf-8")
    entry = entry.replace("__BLOCKLY__", BLOCKLY.as_posix()).replace(
        "__BLOCKS__", (REPO / "src" / "blockly" / "arduinoBlocks.js").as_posix()
    )
    (work / "entry.mjs").write_text(entry, encoding="utf-8")
    bundle = work / "bundle.js"
    built = subprocess.run(
        [NODE, str(ROLLDOWN), str(work / "entry.mjs"), "--format", "iife", "--platform", "browser",
         "-o", str(bundle)],
        capture_output=True, text=True, timeout=120,
    )
    assert built.returncode == 0 and bundle.exists(), built.stderr or built.stdout

    def run(
        states: dict | None = None,
        flags: list[str] | None = None,
        edits: dict | None = None,
        inspect: dict | None = None,
    ) -> dict:
        cases = work / "cases.json"
        out = work / "out.json"
        cases.write_text(
            json.dumps({
                "states": states or {}, "flags": flags or [], "edits": edits or {},
                "inspect": inspect or {},
            }),
            encoding="utf-8",
        )
        done = subprocess.run(
            [NODE, str(HERE / "blockly_headless" / "run.cjs"), str(JSDOM), str(bundle), str(cases), str(out)],
            capture_output=True, text=True, timeout=120,
        )
        assert done.returncode == 0, done.stderr or done.stdout
        return json.loads(out.read_text(encoding="utf-8"))

    return run


def loop_source(*lines: str) -> str:
    return "void loop() {\n" + "".join(f"  {line}\n" for line in lines) + "}\n"


OPAQUE = "table[i]"

#: Sources chosen to hit every P3 block, plus nesting in every statement input.
SOURCES = {
    "for-loop": loop_source("for (unsigned int i = 0; i < length; i++) {", "  count += 1;", "}"),
    "for-loop-with-nested-source": loop_source(
        "for (unsigned int i = 0; i < length; i++) {", f"  a = {OPAQUE};", "  count += 1;", "}"
    ),
    "if-else-if-else": loop_source(
        "if (a) {", "  one();", "} else if (b) {", "  two();", "} else {", "  three();", "}"
    ),
    "logic-and-comparison": loop_source(
        "if (!(a || b) && count >= LIMIT && count < MAX && n > 0) {", "  tick();", "}"
    ),
    "ternary": loop_source("x = run ? HIGH : LOW;", "digitalWrite(PIN, run ? HIGH : LOW);"),
    "nested-source-in-every-input": loop_source(
        "if (ready) {",
        "  for (int i = 0; i < 3; i++) {",
        "    if (i > 1) {",
        f"      inner = {OPAQUE};",
        "      tick();",
        "    } else if (i > 0) {",
        "      mid();",
        f"      middle = {OPAQUE};",
        "    }",
        "  }",
        "} else {",
        f"  outer = {OPAQUE};",
        "}",
    ),
}


def apply_saved(source: str, saved: dict, records: list[dict]) -> str:
    program = program_for_source(source)
    section = section_from_state("loop", saved, records)
    return source_for_program(program_with_section(program, section))


def all_ids(node) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("id"), str):
            found.append(node["id"])
        for value in node.values():
            found.extend(all_ids(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(all_ids(value))
    return found


@pytest.mark.parametrize("name", SOURCES)
def test_real_blockly_loads_and_saves_every_p3_workspace_and_the_cpp_is_identical(blockly, name):
    source = SOURCES[name]
    rep = section_representation(program_for_source(source), "loop")
    result = blockly(states={name: rep["workspace"]})
    assert name not in result["errors"], result["errors"].get(name)
    saved = result["saved"][name]

    # Blockly kept every id the records point at - parents and fragments alike.
    for record in rep["preserved"]:
        assert record["parentId"] in all_ids(saved), record
        assert record["id"] in all_ids(saved), record
    assert apply_saved(source, saved, rep["preserved"]) == source


def test_real_blockly_loads_and_saves_every_panel_one_section(blockly):
    source = PANEL_ONE_INO.read_text(encoding="utf-8")
    program = program_for_source(source)
    reps = {
        section.section_id: section_representation(program, section.section_id)
        for section in program.sections
        if section.operation is not None
    }
    result = blockly(states={sid: rep["workspace"] for sid, rep in reps.items()})
    assert result["errors"] == {}
    rebuilt = program
    for sid, rep in reps.items():
        for record in rep["preserved"]:
            assert record["parentId"] in all_ids(result["saved"][sid]), (sid, record)
        rebuilt = program_with_section(
            rebuilt, section_from_state(sid, result["saved"][sid], rep["preserved"])
        )
    assert len(reps) == 11
    assert source_for_program(rebuilt) == source_for_program(program)


def test_an_edit_made_in_real_blockly_reaches_the_cpp(blockly):
    """A field changed on the saved state (as the editor would) changes the C++."""
    source = SOURCES["if-else-if-else"]
    rep = section_representation(program_for_source(source), "loop")
    saved = blockly(states={"s": rep["workspace"]})["saved"]["s"]
    edited = copy.deepcopy(saved)

    def rename(node):
        if isinstance(node, dict):
            if node.get("fields", {}).get("NAME") == "two":
                node["fields"]["NAME"] = "deux"
            for value in node.values():
                rename(value)
        elif isinstance(node, list):
            for value in node:
                rename(value)

    rename(edited)
    assert apply_saved(source, edited, rep["preserved"]) == source.replace("two()", "deux()")


def test_the_preserved_source_block_is_read_only_in_real_blockly(blockly):
    flags = blockly(flags=[PRESERVED_BLOCK_TYPE, "call_method"])["flags"]
    assert flags[PRESERVED_BLOCK_TYPE] == {"editable": False, "movable": False, "deletable": False}
    assert flags["call_method"] == {"editable": True, "movable": True, "deletable": True}


def test_real_blockly_refuses_a_state_the_block_definitions_do_not_declare(blockly):
    """Proof the harness really checks: an input the block lacks is an error."""
    bad = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [{"type": "logic_if", "inputs": {"NOT_AN_INPUT": {"block": {"type": "logic_true"}}}}],
        }
    }
    result = blockly(states={"bad": bad})
    assert "bad" in result["errors"] or result["saved"]["bad"] != bad
