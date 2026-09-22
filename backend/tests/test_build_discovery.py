"""Phase B1 — C++ structural discovery (`app/build/discovery/`).

Covers the passive analysis layer in isolation: `analyze_source` turning raw
Arduino `.ino` text into a `BuildDocument` of ordered `CodeSection`s. Nothing
here touches a `BuildSession`, `BuildWorkspace`, `BuildProject`, a panel, or
hardware — this module is deliberately exercised only against plain strings
(including the real, committed Panel 1 firmware file, read directly off
disk) and its own model classes. See `app/build/discovery/analyzer.py`'s
module docstring for the algorithm this test suite is pinning down.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.build.discovery import (
    BuildDocument,
    CodeSection,
    EmptySourceError,
    SectionKind,
    UnbalancedBracesError,
    UnterminatedLiteralError,
    analyze_source,
)
from app.build.discovery.analyzer import DiscoveryError

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
DISCOVERY_DIR = APP_DIR / "build" / "discovery"
PANEL_ONE_INO = (
    pathlib.Path(__file__).resolve().parents[1]
    / "panels"
    / "smart-home-mqtt-control"
    / "firmware"
    / "smart_home_mqtt_control"
    / "smart_home_mqtt_control.ino"
)


# =============================================================================
# 1. Basic setup()/loop() discovery
# =============================================================================


def test_discovers_setup_and_loop():
    source = "void setup() {\n  pinMode(2, OUTPUT);\n}\n\nvoid loop() {\n  delay(1000);\n}\n"
    doc = analyze_source(source)

    assert doc.setup is not None
    assert doc.setup.kind is SectionKind.SETUP
    assert doc.setup.name == "setup"
    assert doc.setup.signature == "void setup()"

    assert doc.loop is not None
    assert doc.loop.kind is SectionKind.LOOP
    assert doc.loop.name == "loop"
    assert doc.loop.signature == "void loop()"


def test_setup_and_loop_get_stable_plain_ids():
    source = "void setup() {}\n\nvoid loop() {}\n"
    doc = analyze_source(source)
    assert doc.section("setup") is doc.setup
    assert doc.section("loop") is doc.loop


# =============================================================================
# 2. Multiple helper functions
# =============================================================================


def test_discovers_multiple_helper_functions_in_order():
    source = (
        "void startMotor() {\n  digitalWrite(1, HIGH);\n}\n\n"
        "void stopMotor() {\n  digitalWrite(1, LOW);\n}\n\n"
        "void setup() {}\n\nvoid loop() {}\n"
    )
    doc = analyze_source(source)
    helper_names = [s.name for s in doc.functions if s.kind is SectionKind.HELPER_FUNCTION]
    assert helper_names == ["startMotor", "stopMotor"]


def test_duplicate_function_names_get_disambiguated_ids():
    source = (
        "void helper(int x) { digitalWrite(x, HIGH); }\n"
        "void helper(int x, int y) { digitalWrite(x, y); }\n"
    )
    doc = analyze_source(source)
    ids = [s.section_id for s in doc.functions]
    assert ids == ["helper_helper", "helper_helper_2"]
    assert len(set(ids)) == 2


# =============================================================================
# 3. Global declarations
# =============================================================================


def test_global_declarations_precede_first_function():
    source = (
        '#include <WiFi.h>\nstatic const char *SSID = "lab";\n\n'
        "void setup() {}\n\nvoid loop() {}\n"
    )
    doc = analyze_source(source)
    first = doc.sections[0]
    assert first.kind is SectionKind.GLOBAL_DECLARATIONS
    assert "#include <WiFi.h>" in first.text
    assert "SSID" in first.text
    assert first.name is None
    assert first.signature is None


def test_trailing_content_after_the_last_function_is_its_own_global_section():
    """Not just a leading region — ANY top-level, non-function text is
    GLOBAL_DECLARATIONS-kind, including a trailing newline after the last
    function's closing brace. This is deliberate (see the analyzer's module
    docstring): dropping it would violate the reconstruction invariant, and
    silently folding it into the last function's own text would misrepresent
    that function as containing unrelated trailing content."""
    source = "void setup() {}\n\nvoid loop() {}\n"
    doc = analyze_source(source)
    assert doc.sections[-1].kind is SectionKind.GLOBAL_DECLARATIONS
    assert doc.sections[-1].text == "\n"
    assert doc.render() == source


def test_global_declarations_can_appear_between_and_after_functions():
    """Not just "before the first function" — any top-level, non-function
    text (including a later global declared after a function) is the same
    honest GLOBAL_DECLARATIONS kind, wherever in the file it sits."""
    source = (
        "void helperA() {}\n\n"
        "int lateGlobal = 5;\n\n"
        "void helperB() {}\n\n"
        "// trailing comment\n"
    )
    doc = analyze_source(source)
    kinds = [s.kind for s in doc.sections]
    assert kinds == [
        SectionKind.HELPER_FUNCTION,
        SectionKind.GLOBAL_DECLARATIONS,
        SectionKind.HELPER_FUNCTION,
        SectionKind.GLOBAL_DECLARATIONS,
    ]
    assert "lateGlobal" in doc.sections[1].text
    assert "trailing comment" in doc.sections[3].text


def test_top_level_array_and_struct_initializers_do_not_become_functions():
    """A brace-initializer or struct body at top level ends in something
    other than ')' immediately before '{' (or the preceding text is not a
    function signature), so it must stay folded into GLOBAL_DECLARATIONS —
    never misidentified as a function, never break depth tracking for what
    follows it."""
    source = (
        "int lookupTable[] = {10, 20, 30};\n\n"
        "struct Config {\n  int x;\n  int y;\n} cfg = {1, 2};\n\n"
        "void setup() {\n  int local[] = {1, 2, 3};\n  pinMode(2, OUTPUT);\n}\n\n"
        "void loop() {}"
    )
    doc = analyze_source(source)
    assert [s.kind for s in doc.sections] == [
        SectionKind.GLOBAL_DECLARATIONS,
        SectionKind.SETUP,
        SectionKind.LOOP,
    ]
    assert "lookupTable" in doc.sections[0].text
    assert "struct Config" in doc.sections[0].text
    assert "int local[] = {1, 2, 3};" in doc.setup.text


# =============================================================================
# 4. Nested braces
# =============================================================================


def test_nested_control_flow_braces_stay_inside_the_function_section():
    source = (
        "void setup() {\n"
        "  if (true) {\n"
        "    for (int i = 0; i < 10; i++) {\n"
        "      digitalWrite(2, HIGH);\n"
        "    }\n"
        "  }\n"
        "}\n\n"
        "void loop() {}"
    )
    doc = analyze_source(source)
    assert doc.setup is not None
    assert "for (int i = 0; i < 10; i++)" in doc.setup.text
    # loop() must not have been swallowed by setup()'s nested braces.
    assert doc.loop is not None
    assert doc.loop.text == "\n\nvoid loop() {}"


# =============================================================================
# 5. Braces inside comments
# =============================================================================


def test_braces_inside_line_and_block_comments_are_ignored():
    source = (
        "// a brace in a line comment { does not count }\n"
        "/* a block comment\n"
        "   with a } brace inside */\n"
        "void setup() {\n"
        "  pinMode(2, OUTPUT);\n"
        "}\n\n"
        "void loop() {}"
    )
    doc = analyze_source(source)
    assert [s.kind for s in doc.sections] == [
        SectionKind.GLOBAL_DECLARATIONS,
        SectionKind.SETUP,
        SectionKind.LOOP,
    ]
    assert doc.setup.text.count("{") == 1
    assert doc.setup.text.count("}") == 1


# =============================================================================
# 6. Braces inside string literals
# =============================================================================


def test_braces_inside_string_and_char_literals_are_ignored():
    source = (
        "void setup() {\n"
        '  const char* s = "a { fake } brace";\n'
        "  char c = '{';\n"
        "  pinMode(2, OUTPUT);\n"
        "}\n\n"
        "void loop() {}\n"
    )
    doc = analyze_source(source)
    assert doc.setup is not None
    assert doc.loop is not None
    assert 'const char* s = "a { fake } brace";' in doc.setup.text


def test_escaped_quote_inside_string_literal_does_not_end_it_early():
    source = 'void setup() {\n  const char* s = "a \\"quoted\\" { brace }";\n}\n\nvoid loop() {}\n'
    doc = analyze_source(source)
    assert doc.setup is not None
    assert doc.loop is not None


# =============================================================================
# 7. Function signatures: parameters, pointers, references, const, arrays
# =============================================================================


def test_signature_extraction_handles_pointer_reference_and_const_params():
    source = (
        "static void onMessage(char *topic, byte *payload, unsigned int length) {}\n\n"
        "float readTemperatureField(const String& message) {}\n\n"
        "void setup() {}\n\nvoid loop() {}\n"
    )
    doc = analyze_source(source)
    on_message = doc.section("helper_onMessage")
    assert on_message is not None
    assert on_message.signature == (
        "static void onMessage(char *topic, byte *payload, unsigned int length)"
    )
    read_temp = doc.section("helper_readTemperatureField")
    assert read_temp is not None
    assert read_temp.signature == "float readTemperatureField(const String& message)"


def test_a_control_flow_keyword_is_never_mistaken_for_a_function_name():
    # `if (...)`/`for (...)` end in ')' just like a real signature would, but
    # must never be classified as a HELPER_FUNCTION named "if"/"for".
    source = "void setup() {\n  if (true) {\n    delay(1);\n  }\n}\n\nvoid loop() {}\n"
    doc = analyze_source(source)
    names = [s.name for s in doc.functions]
    assert "if" not in names
    assert names == ["setup", "loop"]


# =============================================================================
# 8. Source ordering
# =============================================================================


def test_sections_preserve_source_order():
    source = (
        "int x = 1;\n\n"
        "void helperA() {}\n\n"
        "void setup() {}\n\n"
        "void helperB() {}\n\n"
        "void loop() {}"
    )
    doc = analyze_source(source)
    offsets = [s.start_offset for s in doc.sections]
    assert offsets == sorted(offsets)
    names_in_order = [s.name for s in doc.sections]
    assert names_in_order == [None, "helperA", "setup", "helperB", "loop"]


# =============================================================================
# 9. Exact source reconstruction invariant
# =============================================================================


@pytest.mark.parametrize(
    "source",
    [
        "void setup() {}\n\nvoid loop() {}\n",
        "int x = 1;\nvoid helperA() {}\nvoid setup() {}\nvoid loop() {}\n",
        "// just a comment\n",
        "int onlyGlobals[] = {1, 2, 3};\n",
        "void helperOnly() { digitalWrite(1, HIGH); }\n",
    ],
)
def test_render_reproduces_source_exactly(source):
    doc = analyze_source(source)
    assert doc.render() == source


def test_reconstruction_invariant_is_enforced_by_the_model_itself():
    """`BuildDocument.__post_init__` rejects a hand-built document whose
    sections do not exactly cover the source — this is a checked invariant,
    not merely a property the analyzer happens to uphold."""
    source = "void setup() {}\n"
    bad_section = CodeSection(
        section_id="setup",
        kind=SectionKind.SETUP,
        name="setup",
        text="void setup() {}",  # missing the trailing "\n"
        start_offset=0,
        end_offset=len(source) - 1,
        signature="void setup()",
    )
    with pytest.raises(ValueError, match="reconstruction would not be exact"):
        BuildDocument(source=source, sections=(bad_section,))


def test_real_panel_one_firmware_reconstructs_exactly():
    source = PANEL_ONE_INO.read_text(encoding="utf-8")
    doc = analyze_source(source)
    assert doc.render() == source


# =============================================================================
# 10. Empty source
# =============================================================================


def test_empty_source_raises():
    with pytest.raises(EmptySourceError):
        analyze_source("")


def test_whitespace_only_source_raises():
    with pytest.raises(EmptySourceError):
        analyze_source("   \n\t  \n")


# =============================================================================
# 11. Missing setup()/loop()
# =============================================================================


def test_missing_setup_and_loop_is_not_an_error():
    source = "void helperA() {}\nvoid helperB(int x) {}\n"
    doc = analyze_source(source)  # must not raise
    assert doc.setup is None
    assert doc.loop is None
    assert [s.name for s in doc.functions] == ["helperA", "helperB"]


def test_source_with_no_functions_at_all_is_one_global_section():
    source = "#include <Arduino.h>\nint x = 5;\n"
    doc = analyze_source(source)  # must not raise
    assert len(doc.sections) == 1
    assert doc.sections[0].kind is SectionKind.GLOBAL_DECLARATIONS
    assert doc.functions == ()


def test_missing_setup_only():
    source = "void loop() {}\n"
    doc = analyze_source(source)
    assert doc.setup is None
    assert doc.loop is not None


def test_missing_loop_only():
    source = "void setup() {}\n"
    doc = analyze_source(source)
    assert doc.setup is not None
    assert doc.loop is None


# =============================================================================
# 12. Malformed / unbalanced source
# =============================================================================


def test_unclosed_brace_raises():
    with pytest.raises(UnbalancedBracesError):
        analyze_source("void setup() {\n  pinMode(2, OUTPUT);\n")


def test_stray_closing_brace_raises():
    with pytest.raises(UnbalancedBracesError):
        analyze_source("void setup() {}\n}\n")


def test_unterminated_block_comment_raises():
    with pytest.raises(UnterminatedLiteralError):
        analyze_source("/* never closes\nvoid setup() {}\n")


def test_unterminated_string_literal_raises():
    with pytest.raises(UnterminatedLiteralError):
        analyze_source('void setup() {\n  const char* s = "never closes;\n}\n')


def test_all_discovery_errors_share_one_base_class():
    assert issubclass(EmptySourceError, DiscoveryError)
    assert issubclass(UnbalancedBracesError, DiscoveryError)
    assert issubclass(UnterminatedLiteralError, DiscoveryError)
    assert issubclass(DiscoveryError, ValueError)


# =============================================================================
# 13. Real Panel 1 firmware
# =============================================================================


def test_panel_one_firmware_discovers_the_real_structure():
    """Asserts the ACTUAL structure of the committed Panel 1 firmware, not a
    hypothetical one. See
    backend/panels/smart-home-mqtt-control/firmware/smart_home_mqtt_control/
    smart_home_mqtt_control.ino for the source this pins down."""
    source = PANEL_ONE_INO.read_text(encoding="utf-8")
    doc = analyze_source(source)

    assert doc.setup is not None
    assert doc.setup.signature == "void setup()"
    assert doc.loop is not None
    assert doc.loop.signature == "void loop()"

    function_names = [s.name for s in doc.functions]
    expected_functions = [
        "setMotorOutputs",
        "chirpBuzzer",
        "applyMotorState",
        "motorStart",
        "motorStop",
        "applyCommand",
        "onMessage",
        "pollButtons",
        "ensureConnected",
        "setup",
        "loop",
    ]
    assert function_names == expected_functions

    by_name = {s.name: s for s in doc.functions}
    # onMessage is registered with `client.setCallback(onMessage);` inside
    # setup() -- a bare reference, never a call -- so the generic classifier
    # must promote it to CALLBACK.
    assert by_name["onMessage"].kind is SectionKind.CALLBACK
    # applyCommand is only ever invoked (`applyCommand(message);`) -- never
    # passed bare -- so it must stay a HELPER_FUNCTION, not a callback,
    # even though it is the function that contains the actual vulnerability.
    assert by_name["applyCommand"].kind is SectionKind.HELPER_FUNCTION
    for helper_name in (
        "setMotorOutputs",
        "chirpBuzzer",
        "applyMotorState",
        "motorStart",
        "motorStop",
        "pollButtons",
        "ensureConnected",
    ):
        assert by_name[helper_name].kind is SectionKind.HELPER_FUNCTION

    first_global = doc.sections[0]
    assert first_global.kind is SectionKind.GLOBAL_DECLARATIONS
    assert "MQTT_BROKER" in first_global.text
    assert "COMMAND_TOPIC" in first_global.text


def test_panel_one_firmware_is_not_modified_by_analysis():
    before = PANEL_ONE_INO.read_text(encoding="utf-8")
    analyze_source(before)
    after = PANEL_ONE_INO.read_text(encoding="utf-8")
    assert before == after


# =============================================================================
# Model validation (CodeSection / BuildDocument), directly
# =============================================================================


def test_code_section_rejects_offset_text_length_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        CodeSection(
            section_id="x",
            kind=SectionKind.GLOBAL_DECLARATIONS,
            name=None,
            text="abc",
            start_offset=0,
            end_offset=10,
        )


def test_code_section_rejects_a_name_on_global_declarations():
    with pytest.raises(ValueError, match="no name"):
        CodeSection(
            section_id="x",
            kind=SectionKind.GLOBAL_DECLARATIONS,
            name="not allowed",
            text="int x;",
            start_offset=0,
            end_offset=6,
        )


def test_code_section_requires_a_name_on_function_kinds():
    with pytest.raises(ValueError, match="needs a name"):
        CodeSection(
            section_id="x",
            kind=SectionKind.HELPER_FUNCTION,
            name=None,
            text="void f() {}",
            start_offset=0,
            end_offset=11,
            signature="void f()",
        )


def test_build_document_rejects_a_gap_between_sections():
    source = "abcdef"
    a = CodeSection(
        section_id="a",
        kind=SectionKind.GLOBAL_DECLARATIONS,
        name=None,
        text="abc",
        start_offset=0,
        end_offset=3,
    )
    b = CodeSection(
        section_id="b",
        kind=SectionKind.GLOBAL_DECLARATIONS,
        name=None,
        text="ef",
        start_offset=4,  # gap: offset 3 ('d') is unaccounted for
        end_offset=6,
    )
    with pytest.raises(ValueError, match="contiguous"):
        BuildDocument(source=source, sections=(a, b))


def test_build_document_rejects_duplicate_section_ids():
    source = "abcabc"
    a = CodeSection(
        section_id="dup",
        kind=SectionKind.GLOBAL_DECLARATIONS,
        name=None,
        text="abc",
        start_offset=0,
        end_offset=3,
    )
    b = CodeSection(
        section_id="dup",
        kind=SectionKind.GLOBAL_DECLARATIONS,
        name=None,
        text="abc",
        start_offset=3,
        end_offset=6,
    )
    with pytest.raises(ValueError, match="duplicate section id"):
        BuildDocument(source=source, sections=(a, b))


def test_build_document_line_at_is_derived_not_stored():
    doc = analyze_source("void setup() {}\n// line two\nvoid loop() {}\n")
    assert doc.line_at(0) == 1
    newline_index = doc.source.index("\n")
    assert doc.line_at(newline_index + 1) == 2
    with pytest.raises(ValueError):
        doc.line_at(10_000)


# =============================================================================
# 14/15/16. Genericity, security, and import-boundary checks
# =============================================================================

_DISCOVERY_MODULES = (
    DISCOVERY_DIR / "__init__.py",
    DISCOVERY_DIR / "models.py",
    DISCOVERY_DIR / "analyzer.py",
)

#: Real Panel 1 identifiers that must never appear as a literal anywhere in
#: the discovery layer — the analyzer must stay generic across any Arduino
#: source, not special-cased to this one panel's MQTT firmware.
_PANEL_SPECIFIC_LITERALS = {
    "smart-home-mqtt-control",
    "20:9b:a9:88:0b:e4",
    "cybertrainer/smart-home/motor/control",
    "cybertrainer/smart-home/motor/state",
    "192.168.50.1",
    "onMessage",
    "applyCommand",
    "setCallback",
    "mosquitto",
    "MQTT",
}

#: Layers this analysis-only package must never import from — wiring it into
#: any of these is explicitly a later phase's job (see the B1 audit).
_FORBIDDEN_IMPORT_PREFIXES = (
    "app.panels",
    "app.hardware",
    "app.build_sessions",
    "app.build_websocket",
    "app.build.workspace",
    "app.build.service",
    "app.build.compiler",
    "app.build.flasher",
    "app.build.models",
    "app.blockly",
    "app.scenarios",
    "app.commands",
    "app.events",
)


def test_discovery_modules_contain_no_panel_specific_literal():
    for path in _DISCOVERY_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = sorted(
            {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in _PANEL_SPECIFIC_LITERALS
            }
        )
        assert offenders == [], f"{path.name} names panel-specific literals: {offenders}"


def test_discovery_modules_use_no_dynamic_or_process_execution():
    # Bare-name builtins that are always a code-execution primitive.
    banned_names = {"eval", "exec", "compile", "__import__"}
    # Attribute calls that indicate process spawning — checked separately
    # from `banned_names` because `.compile` is also `re.compile`, an
    # ordinary stdlib regex call this module legitimately makes.
    banned_attrs = {"system", "popen", "Popen", "run", "spawnv", "spawn"}
    for path in _DISCOVERY_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in banned_names, f"{path.name} calls {func.id}"
            if isinstance(func, ast.Attribute):
                assert func.attr not in banned_attrs, f"{path.name} calls .{func.attr}"


def test_discovery_modules_import_no_subprocess_os_or_filesystem():
    for path in _DISCOVERY_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        banned = ("subprocess", "os", "pathlib", "shutil", "tempfile")
        offenders = sorted(
            name
            for name in imported
            for bad in banned
            if name == bad or name.startswith(bad + ".")
        )
        assert offenders == [], f"{path.name} imports {offenders}"


def test_discovery_modules_import_none_of_the_forbidden_layers():
    for path in _DISCOVERY_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = sorted(
            name
            for name in imported
            for prefix in _FORBIDDEN_IMPORT_PREFIXES
            if name == prefix or name.startswith(prefix + ".")
        )
        assert offenders == [], f"{path.name} imports forbidden layer(s): {offenders}"


def test_analyze_source_is_pure_and_deterministic():
    source = PANEL_ONE_INO.read_text(encoding="utf-8")
    first = analyze_source(source)
    second = analyze_source(source)
    assert first == second
    assert first.render() == second.render() == source
