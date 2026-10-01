"""P4.0: Panel 1's remediation, BUILT in the real Blockly from the vulnerable helper.

`test_build_token_parsing.py` proved the parse -> guard -> dispatch remediation
is expressible in the IR by composing Blockly-shaped JSON by hand. This file
closes the remaining gap to the browser: nothing here writes block JSON for
the new logic. The committed VULNERABLE `helper_applyCommand` is opened exactly
as Build Mode opens it (`BuildWorkspace.section_blockly`), loaded into the REAL
`src/blockly/arduinoBlocks.js` definitions under Blockly's headless workspace,
edited through Blockly's own block API (`newBlock`, `setFieldValue`,
`connect` - what a drag-and-drop ends in, with Blockly's connection checker
enforcing every socket's type), saved with `serialization.workspaces.save`, and
sent with the ORIGINAL preserved list - which is what `BuildMode.jsx` resubmits
- through the real `apply_section_blockly`.

    received message
        -> parse command + token         text_index_of / text_substring / text_length
        -> reject malformed input        logic_if + logic_less_equal + return_void
        -> reject unauthorized token     logic_if + logic_not_equal + return_void
        -> dispatch START / STOP         the EXISTING chain, retargeted message -> command

Only generic toolbox blocks are used; there is no security block, and nothing
in `app/` knows the token (`test_build_token_parsing.py` pins that).

No C++ is injected: every assertion on C++ is on what B5 -> B6 wrote.
Skipped, not failed, where Node or the frontend dependencies are unavailable.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import shutil

import pytest

from app import config
from app.build import (
    ArduinoCliCompiler,
    BuildWorkspace,
    CompileRequest,
    board_info_from_fqbn,
    load_sketch_project,
)
from app.build.program_source import program_for_source
from app.build.semantic import ConditionalStatement, UnsupportedStatement
from app.panels import default_panel_package_loader

from tests.test_real_blockly import blockly, pytestmark  # noqa: F401 - shared fixture/skip

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PANEL_ONE = "smart-home-mqtt-control"
PANEL_SKETCH = BACKEND / "panels" / PANEL_ONE / "firmware" / "smart_home_mqtt_control"
SKETCH_NAME = "smart_home_mqtt_control.ino"
SECTION = "helper_applyCommand"

#: What a student types into a text block. Test data only.
TOKEN = "PANEL1-CMD-AUTH-K7"


def panel_one_workspace() -> BuildWorkspace:
    """Panel 1's committed firmware under the policy its own package declares."""
    declared = default_panel_package_loader().load(PANEL_ONE).remediation
    assert declared.security_section_id == SECTION
    return BuildWorkspace(
        load_sketch_project(
            PANEL_SKETCH,
            project_id="smart-home-mqtt-control-firmware",
            scenario_id=PANEL_ONE,
            module_id=PANEL_ONE,
            firmware_name="Smart Home MQTT Control System",
            board=board_info_from_fqbn("esp32:esp32:esp32"),
            editable_section_ids=declared.editable_section_ids,
            explore_section_ids=declared.explore_section_ids,
            security_region_id=declared.security_section_id,
        )
    )


# --- the student's edit, as Blockly API steps --------------------------------


def new(ref: str, block_type: str, **fields) -> dict:
    return {"op": "new", "ref": ref, "type": block_type, "fields": fields}


def plug(parent: str, socket: str, child: str) -> dict:
    return {"op": "value", "parent": parent, "input": socket, "child": child}


def remediation_ops() -> list[dict]:
    """Parse -> guard -> dispatch, built from toolbox blocks onto the open section."""
    ops: list[dict] = []

    def var(ref: str, name: str) -> str:
        ops.append(new(ref, "variables_get", NAME=name))
        return ref

    def declare(ref: str, type_token: str, name: str, initial: str) -> str:
        ops.append(new(ref, "variables_declare", QUALIFIER="none", TYPE=type_token, NAME=name))
        ops.append(plug(ref, "INITIAL", initial))
        return ref

    def guard(ref: str, condition: str) -> str:
        ops.append(new(ref, "logic_if"))
        ops.append(plug(ref, "CONDITION", condition))
        ops.append(new(f"{ref}.return", "return_void"))
        ops.append({"op": "statement", "parent": ref, "input": "DO", "child": f"{ref}.return"})
        return ref

    # 1. dispatch on the parsed command, not the raw message: retype the
    #    variable in the EXISTING if / else-if chain the section opened with.
    ops.append({
        "op": "rename", "root": f"id:{SECTION}.0", "type": "variables_get",
        "field": "NAME", "from": "message", "to": "command",
    })

    # 2. String AUTH_TOKEN = "<token>";
    ops.append(new("token.value", "text_literal", VALUE=TOKEN))
    declare("auth", "text", "AUTH_TOKEN", "token.value")

    # 3. int separator = message.indexOf(" ");
    ops.append(new("find", "text_index_of"))
    ops.append(new("space", "text_literal", VALUE=" "))
    ops.append(plug("find", "SEARCH", "space"))
    ops.append(plug("find", "TEXT", var("msg.1", "message")))
    declare("separator", "number", "separator", "find")

    # 4. if (separator <= 0) { return; }
    ops.append(new("malformed.test", "logic_less_equal"))
    ops.append(plug("malformed.test", "A", var("sep.1", "separator")))
    ops.append(new("zero", "math_number", VALUE=0))
    ops.append(plug("malformed.test", "B", "zero"))
    guard("malformed", "malformed.test")

    # 5. String command = message.substring(0, separator);
    ops.append(new("head", "text_substring"))
    ops.append(plug("head", "TEXT", var("msg.2", "message")))
    ops.append(new("zero.2", "math_number", VALUE=0))
    ops.append(plug("head", "FROM", "zero.2"))
    ops.append(plug("head", "TO", var("sep.2", "separator")))
    declare("command", "text", "command", "head")

    # 6. String token = message.substring(separator + 1, message.length());
    ops.append(new("after", "math_add"))
    ops.append(plug("after", "A", var("sep.3", "separator")))
    ops.append(new("one", "math_number", VALUE=1))
    ops.append(plug("after", "B", "one"))
    ops.append(new("len", "text_length"))
    ops.append(plug("len", "TEXT", var("msg.3", "message")))
    ops.append(new("tail", "text_substring"))
    ops.append(plug("tail", "TEXT", var("msg.4", "message")))
    ops.append(plug("tail", "FROM", "after"))
    ops.append(plug("tail", "TO", "len"))
    declare("token", "text", "token", "tail")

    # 7. if (token != AUTH_TOKEN) { return; }
    ops.append(new("unauthorized.test", "logic_not_equal"))
    ops.append(plug("unauthorized.test", "A", var("tok.1", "token")))
    ops.append(plug("unauthorized.test", "B", var("auth.1", "AUTH_TOKEN")))
    guard("unauthorized", "unauthorized.test")

    # 8. stack the new statements, then drop the stack at the TOP of the body:
    #    Blockly re-attaches what was there (the chain, then the read-only
    #    comment block) under the stack's last block, as a real drop does.
    order = ["auth", "separator", "malformed", "command", "token", "unauthorized"]
    for upper, lower in zip(order, order[1:]):
        ops.append({"op": "next", "parent": upper, "child": lower})
    ops.append({"op": "statement", "parent": f"id:{SECTION}", "input": "BODY", "child": "auth"})
    return ops


@pytest.fixture(scope="module")
def built(blockly):
    """The whole browser round: open, edit in real Blockly, save, apply."""
    live = panel_one_workspace()
    before_source = live.full_source(SKETCH_NAME)
    before_segments = {s.region_id: s.text for s in live.project.files[0].segments}
    opened = live.section_blockly(SKETCH_NAME, SECTION)
    result = blockly(edits={"remediation": {"state": opened["workspace"], "ops": remediation_ops()}})
    assert result["errors"] == {}, result["errors"]
    saved = result["saved"]["remediation"]
    # Exactly what BuildMode.jsx resubmits: the saved workspace plus the
    # preserved list the section opened with, unchanged.
    live.apply_section_blockly(SKETCH_NAME, SECTION, saved, opened["preserved"])
    return {
        "live": live,
        "opened": opened,
        "saved": saved,
        "before_source": before_source,
        "before_segments": before_segments,
        "region": live.region_source(SKETCH_NAME, SECTION),
    }


def segments(live: BuildWorkspace) -> dict[str, str]:
    return {s.region_id: s.text for s in live.project.files[0].segments}


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# =============================================================================
# 1. The section opens as blocks, and real Blockly accepts every step
# =============================================================================


def test_the_vulnerable_helper_opens_as_editable_blocks(built) -> None:
    opened = built["opened"]
    assert opened["representable"] is True
    top = opened["workspace"]["blocks"]["blocks"][0]
    assert top["type"] == "function_implementation"
    # The only preserved record is the trailing explanatory comment.
    assert [r["text"].lstrip()[:2] for r in opened["preserved"]] == ["//"]


def test_only_generic_toolbox_blocks_were_used(built) -> None:
    from app.blockly.catalog import default_block_catalog
    from app.blockly.models import ImplementationStatus

    types: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("type"), str):
                types.add(node["type"])
            for key, value in node.items():
                if key != "extraState":  # the read-only signature header: display data, not blocks
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(built["saved"])
    types.discard("preserved_source")  # drawn, read-only, never read back
    implemented = {
        block.blockly_type: block
        for block in default_block_catalog.blocks
        if block.status is ImplementationStatus.IMPLEMENTED
    }
    assert types <= set(implemented), types - set(implemented)
    for block_type in types:
        description = implemented[block_type].description.lower()
        assert "token" not in description and "panel" not in description, block_type
    assert types == {
        "function_implementation", "variables_declare", "variables_get", "text_literal",
        "text_index_of", "text_substring", "text_length", "math_number", "math_add",
        "logic_less_equal", "logic_not_equal", "logic_equal", "logic_if", "return_void",
        "call_existing_function",
    }


def test_blockly_refuses_a_step_its_definitions_forbid(blockly) -> None:
    """Proof the edit harness is checked by Blockly, not merely replayed."""
    opened = panel_one_workspace().section_blockly(SKETCH_NAME, SECTION)
    bad = [
        new("text", "text_literal", VALUE="x"),
        new("gate", "logic_if"),
        plug("gate", "CONDITION", "text"),  # a String output in a Boolean socket
    ]
    result = blockly(edits={"bad": {"state": opened["workspace"], "ops": bad}})
    assert "bad" in result["errors"]


# =============================================================================
# 2-3. Ownership and the generated C++
# =============================================================================


def test_the_security_region_accepts_the_blockly_edit(built) -> None:
    # `built` already ran `apply_section_blockly` with the security-region
    # ownership rule active; reaching here means no SecurityRegionOwnershipError.
    live = built["live"]
    assert live.project.security_region_id == SECTION
    assert built["region"] != built["before_segments"][SECTION]


def test_the_generated_apply_command_is_parse_guard_dispatch(built) -> None:
    body = normalized(built["region"])
    steps = [
        "static void applyCommand(const String &message) {",
        f'String AUTH_TOKEN = "{TOKEN}";',                                  # token
        'int separator = message.indexOf(" ");',                            # separator
        "if (separator <= 0) { return; }",                                  # malformed
        "String command = message.substring(0, separator);",                # command
        "String token = message.substring(separator + 1, message.length());",  # token
        "if (token != AUTH_TOKEN) { return; }",                             # authorization
        'if (command == "START") { motorStart(); }',                        # START
        'else if (command == "STOP") { motorStop(); }',                     # STOP
    ]
    position = 0
    for step in steps:
        found = body.find(step, position)
        assert found >= 0, f"missing or out of order: {step}\n---\n{built['region']}"
        position = found + len(step)
    # The raw message never reaches dispatch.
    assert 'message == "START"' not in body and 'message == "STOP"' not in body
    assert body.index("return;") < body.index("motorStart();")


def test_the_explanatory_comment_survives_in_the_region(built) -> None:
    comment = built["opened"]["preserved"][0]["text"]
    assert normalized(comment) in normalized(built["region"])


def test_the_explanatory_comment_stays_after_the_dispatch_chain(built) -> None:
    """Formerly a strict xfail: the fragment was spliced back at the index it
    was OPENED at, so the six blocks inserted above it pulled it up the body.
    It now stays where its read-only block sits after the edit - last."""
    region = built["region"]
    assert region.index("motorStop();") < region.index("// Any other payload")


def test_inserting_above_a_preserved_statement_does_not_reorder_it(blockly) -> None:
    """Formerly a strict xfail - the same defect on real code, where it
    reordered an executable statement rather than a comment."""
    from tests.test_real_blockly import apply_saved, loop_source
    from app.build.section_blockly import section_representation

    source = loop_source("one();", "a = table[i];", "two();")
    rep = section_representation(program_for_source(source), "loop")
    top = rep["workspace"]["blocks"]["blocks"][0]
    ops = [
        new("inserted", "call_existing_function", NAME="inserted"),
        {"op": "statement", "parent": f"id:{top['id']}", "input": "DO", "child": "inserted"},
    ]
    saved = blockly(edits={"x": {"state": rep["workspace"], "ops": ops}})["saved"]["x"]
    assert apply_saved(source, saved, rep["preserved"]) == loop_source(
        "inserted();", "one();", "a = table[i];", "two();"
    )


# =============================================================================
# 4. Round trip: the generated C++ reads back as the same, fully drawable blocks
# =============================================================================


def test_the_generated_helper_reads_back_as_blocks_with_nothing_opaque(built) -> None:
    live = built["live"]
    again = live.section_blockly(SKETCH_NAME, SECTION)
    assert again["representable"] is True
    assert all(r["text"].lstrip().startswith("//") for r in again["preserved"])
    meaning = program_for_source(live.full_source(SKETCH_NAME)).section(SECTION)
    # The one carried statement is the explanatory comment; every piece of
    # C++ the remediation added is understood by the IR.
    carried = [s for s in meaning.statements if isinstance(s, UnsupportedStatement)]
    assert [s.source_text.lstrip()[:2] for s in carried] == ["//"]
    kinds = [type(s).__name__ for s in meaning.statements if s not in carried]
    assert kinds == [
        "VariableDeclaration", "VariableDeclaration", "ConditionalStatement",
        "VariableDeclaration", "VariableDeclaration", "ConditionalStatement",
        "ConditionalStatement",
    ]
    # The dispatch chain, then the comment that followed it in the firmware -
    # still after it, because a preserved fragment keeps its saved position.
    assert isinstance(meaning.statements[-2], ConditionalStatement)
    assert meaning.statements[-1] is carried[0]
    assert meaning.statements[-2].else_if is not None


def test_reopening_and_resaving_in_real_blockly_is_a_fixpoint(built, blockly) -> None:
    live = built["live"]
    region = live.region_source(SKETCH_NAME, SECTION)
    whole = live.full_source(SKETCH_NAME)
    again = live.section_blockly(SKETCH_NAME, SECTION)
    saved = blockly(states={"again": again["workspace"]})["saved"]["again"]
    live.apply_section_blockly(SKETCH_NAME, SECTION, saved, again["preserved"])
    assert live.region_source(SKETCH_NAME, SECTION) == region
    assert live.full_source(SKETCH_NAME) == whole


# =============================================================================
# 5. Formatting preservation: only the edited section moved
# =============================================================================


def test_every_other_section_is_byte_identical(built) -> None:
    before = built["before_segments"]
    after = segments(built["live"])
    assert list(before) == list(after)
    changed = [region for region in before if before[region] != after[region]]
    assert changed == [SECTION]


def test_the_edited_section_keeps_its_original_surrounding_gaps(built) -> None:
    before = built["before_segments"][SECTION]
    after = built["region"]
    lead = before[: len(before) - len(before.lstrip())]
    trail = before[len(before.rstrip()):]
    assert after.startswith(lead) and after.endswith(trail)


def test_preserved_source_outside_the_region_is_untouched(built) -> None:
    source = built["live"].full_source(SKETCH_NAME)
    for fragment in (
        "message += static_cast<char>(payload[i]);",
        "if (String(topic) == COMMAND_TOPIC) {",
        "static unsigned long lastEdge = 0;",
        "client.publish(STATE_TOPIC, motorRunning ? \"RUNNING\" : \"STOPPED\", true);",
    ):
        assert source.count(fragment) == built["before_source"].count(fragment) >= 1, fragment


def test_the_committed_sketch_on_disk_is_unchanged(built) -> None:
    committed = (PANEL_SKETCH / SKETCH_NAME).read_text(encoding="utf-8")
    assert committed == built["before_source"]
    assert 'if (message == "START") {' in committed


# =============================================================================
# 6. The real toolchain compiles it
# =============================================================================

_REAL_ARDUINO_CLI = shutil.which(config.ARDUINO_CLI_PATH)


@pytest.mark.skipif(
    _REAL_ARDUINO_CLI is None,
    reason=f"arduino-cli not available via config.ARDUINO_CLI_PATH={config.ARDUINO_CLI_PATH!r}",
)
def test_the_blockly_built_remediation_compiles(built, tmp_path: pathlib.Path) -> None:
    """Compile only - nothing is uploaded, no port is opened, no board is needed."""
    live = built["live"]
    sketch_dir = live.materialize(tmp_path / "sketch")
    request = CompileRequest(
        sketch_dir=sketch_dir,
        fqbn=live.project.board.fqbn,
        build_path=tmp_path / "build",
        timeout_seconds=config.BUILD_COMPILE_TIMEOUT_SECONDS,
    )
    outcome = asyncio.run(ArduinoCliCompiler(config.ARDUINO_CLI_PATH).run_compile(request))
    assert outcome.success is True, outcome.stderr or outcome.stdout
