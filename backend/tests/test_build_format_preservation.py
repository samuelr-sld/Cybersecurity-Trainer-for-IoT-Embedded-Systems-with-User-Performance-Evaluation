"""Editing one section must not rewrite the formatting of any other.

B6 lays a whole file out in its own style (two-space indent, one blank line
between sections, no blank lines inside a body). `apply_program_to_file` used
to hand that whole regeneration back as the new file, so editing ONE section
re-laid-out EVERY section: interior blank lines were dropped
(`helper_ensureConnected` 4 -> 2, `setup` 5 -> 2) and the gap in front of
several sections was normalised, although no statement in them had changed.

`_preserving_untouched_layout` (app/build/program_source.py) now keeps a
section's ORIGINAL text whenever B6 writes it exactly as it writes the
unedited file, and gives a changed section its original leading/trailing gap.

These tests use the real committed Panel 1 firmware and the path a browser
takes: section -> Blockly JSON -> edit a block -> `apply_section_blockly`.
"""

from __future__ import annotations

import copy
import pathlib

import pytest

from app.build import BuildWorkspace, board_info_from_fqbn, load_sketch_project

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SKETCH_DIR = BACKEND / "panels" / "smart-home-mqtt-control" / "firmware" / "smart_home_mqtt_control"
PATH = "smart_home_mqtt_control.ino"
EDITABLE = (
    "helper_setMotorOutputs",
    "helper_chirpBuzzer",
    "helper_applyMotorState",
    "helper_motorStart",
    "helper_motorStop",
    "helper_applyCommand",
    "callback_onMessage",
    "helper_pollButtons",
    "helper_ensureConnected",
    "setup",
    "loop",
)


def panel_one() -> BuildWorkspace:
    project = load_sketch_project(
        SKETCH_DIR,
        project_id="p",
        scenario_id="s",
        module_id="m",
        firmware_name="f",
        board=board_info_from_fqbn("esp32:esp32:esp32"),
        editable_section_ids=EDITABLE,
        explore_section_ids=("global", "global_3"),
        security_region_id="helper_applyCommand",
    )
    return BuildWorkspace(project)


def texts(workspace: BuildWorkspace) -> dict[str, str]:
    return {segment.region_id: segment.text for segment in workspace.project.file(PATH).segments}


def retext(node, old: str, new: str) -> int:
    """Rewrite every string equal to `old` inside a workspace's block fields."""
    count = 0
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and value == old and key == "VALUE":
                node[key] = new
                count += 1
            else:
                count += retext(value, old, new)
    elif isinstance(node, list):
        for item in node:
            count += retext(item, old, new)
    return count


def edit_section(workspace: BuildWorkspace, section_id: str, old: str, new: str) -> None:
    """A student's edit: open the section as blocks, change one text block, submit."""
    representation = workspace.section_blockly(PATH, section_id)
    state = copy.deepcopy(representation["workspace"])
    assert retext(state, old, new) >= 1, f"no {old!r} block in {section_id}"
    workspace.apply_section_blockly(PATH, section_id, state, representation["preserved"])


def resubmit_unchanged(workspace: BuildWorkspace, section_id: str) -> None:
    representation = workspace.section_blockly(PATH, section_id)
    workspace.apply_section_blockly(
        PATH, section_id, copy.deepcopy(representation["workspace"]), representation["preserved"]
    )


def test_the_fixture_actually_has_the_whitespace_this_protects() -> None:
    """Guard: the real firmware has interior blank lines B6 would have dropped."""
    original = texts(panel_one())
    blanks = lambda key: sum(1 for line in original[key].split("\n") if not line.strip())
    assert blanks("helper_ensureConnected") >= 4
    assert blanks("setup") >= 5


def test_an_unedited_program_is_byte_identical() -> None:
    workspace = panel_one()
    before = workspace.full_source(PATH)
    for section_id in EDITABLE:
        resubmit_unchanged(workspace, section_id)
    assert workspace.full_source(PATH) == before


def test_editing_one_section_changes_only_that_section() -> None:
    workspace = panel_one()
    before = texts(workspace)
    edit_section(workspace, "helper_applyCommand", "START", "START PANEL1-CMD-AUTH-K7")
    after = texts(workspace)

    assert list(after) == list(before), "ordering and the set of sections are unchanged"
    changed = [key for key in before if before[key] != after[key]]
    assert changed == ["helper_applyCommand"]
    assert '"START PANEL1-CMD-AUTH-K7"' in after["helper_applyCommand"]
    assert '"STOP"' in after["helper_applyCommand"]


def test_untouched_sections_keep_gaps_interior_blank_lines_and_comments() -> None:
    workspace = panel_one()
    before = texts(workspace)
    edit_section(workspace, "helper_applyCommand", "START", "START PANEL1-CMD-AUTH-K7")
    after = texts(workspace)

    for key in before:
        if key == "helper_applyCommand":
            continue
        assert after[key] == before[key], f"{key} was rewritten by an edit elsewhere"
    # The specific regressions the HIL run surfaced.
    for key in ("helper_ensureConnected", "setup"):
        assert after[key].count("\n\n") == before[key].count("\n\n")
    for key in ("helper_setMotorOutputs", "helper_applyMotorState", "helper_pollButtons"):
        gap = lambda text: len(text) - len(text.lstrip("\n"))
        assert gap(after[key]) == gap(before[key])
    comment_lines = lambda text: [line for line in text.split("\n") if line.strip().startswith("//")]
    for key in before:
        if key != "helper_applyCommand":
            assert comment_lines(after[key]) == comment_lines(before[key])


def test_the_edited_section_keeps_its_own_gap_to_its_neighbours() -> None:
    workspace = panel_one()
    before = texts(workspace)["helper_applyCommand"]
    edit_section(workspace, "helper_applyCommand", "START", "START X")
    after = texts(workspace)["helper_applyCommand"]
    lead = lambda text: text[: len(text) - len(text.lstrip())]
    trail = lambda text: text[len(text.rstrip()):]
    assert (lead(after), trail(after)) == (lead(before), trail(before))


def test_several_edited_sections_change_only_those_sections() -> None:
    workspace = panel_one()
    before = texts(workspace)
    edit_section(workspace, "helper_applyCommand", "START", "START X")
    edit_section(workspace, "helper_applyMotorState", "RUNNING", "RUN")
    after = texts(workspace)
    changed = {key for key in before if before[key] != after[key]}
    assert changed == {"helper_applyCommand", "helper_applyMotorState"}
    assert '"RUN"' in after["helper_applyMotorState"]


def test_preserved_source_still_round_trips_after_an_edit() -> None:
    """`callback_onMessage` carries opaque C++ fragments; they survive an edit elsewhere."""
    workspace = panel_one()
    before = texts(workspace)["callback_onMessage"]
    assert "static_cast<char>(payload[i])" in before
    edit_section(workspace, "helper_applyCommand", "START", "START X")
    assert texts(workspace)["callback_onMessage"] == before
    # ...and the section itself can still be opened and resubmitted unchanged.
    resubmit_unchanged(workspace, "callback_onMessage")
    assert texts(workspace)["callback_onMessage"] == before


def test_editing_a_section_that_contains_preserved_source_keeps_it() -> None:
    workspace = panel_one()
    edit_section(workspace, "callback_onMessage", "0", "1")  # the loop's start index
    edited = texts(workspace)["callback_onMessage"]
    assert "i = 1" in edited
    assert "static_cast<char>(payload[i])" in edited
    assert "message.trim();" in edited and "message.toUpperCase();" in edited


def test_the_result_still_rediscovers_as_the_same_regions() -> None:
    workspace = panel_one()
    ids = list(texts(workspace))
    edit_section(workspace, "helper_applyCommand", "START", "START X")
    from app.build.discovery import analyze_source

    rediscovered = [section.section_id for section in analyze_source(workspace.full_source(PATH)).sections]
    assert len(rediscovered) == len(ids)
    # Editing again works (the composed file is itself valid input).
    edit_section(workspace, "helper_applyCommand", "START X", "START Y")
    assert '"START Y"' in texts(workspace)["helper_applyCommand"]
