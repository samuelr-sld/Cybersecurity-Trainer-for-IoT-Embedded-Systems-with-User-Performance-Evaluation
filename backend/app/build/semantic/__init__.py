"""The semantic representation layer (Phases B3/B6) — what code MEANS, both ways.

    C++ source
        -> B1 CodeSection            (app/build/discovery/)
        -> B3 Semantic IR            (this package: analyzer.py)
        -> B4 Blockly adapter        (app/build/blockly_bridge/)
        -> Blockly
        -> B5 back to the IR         (app/build/blockly_bridge/)
        -> B6 Arduino C++ source     (this package: generator.py)

BOTH ENDS LIVE HERE, AND NEITHER IS THE OTHER'S INVERSE BY CONSTRUCTION.
`analyzer.py` reads a tiny subset of C++ and carries the rest verbatim;
`generator.py` writes that subset back and carries the rest verbatim. They
share the IR and nothing else — no shared parser state, no cached rendering,
no round-trip cheat — which is what makes an authored statement with no source
generate exactly like one read from firmware.

BLOCKLY IS AN ADAPTER, NOT A DEPENDENCY. Nothing in this package imports
Blockly, `app.blockly`, a workspace, a block id, XML, a serialization format
or any UI state, and nothing here is shaped by how Blockly draws anything.
The IR is equally valid for code that came from handwritten C++, from a
future parser, from another editor, or eventually from Blockly itself — B4
converts between this model and Blockly, in its own module, in both
directions. See `operations.py` for why the block catalog's
`semantic_operation` vocabulary is nonetheless reused verbatim, and how that
agreement is enforced without a type dependency.

The dependency direction is one way and shallow:

    app.build.semantic  ->  app.build.discovery

and nothing else from `app`. No filesystem, no session, no panel package, no
scenario, no MQTT, no permission model, no metric.
"""

from __future__ import annotations

from app.build.semantic.analyzer import analyze_document
from app.build.semantic.emissions import (
    CPP_DECLARATION_TYPES,
    CallEmission,
    CppEmission,
    CppEmissionTable,
    FunctionEmission,
    MethodEmission,
    build_default_emissions,
    default_cpp_emissions,
)
from app.build.semantic.errors import (
    CppGenerationError,
    InvalidContainerError,
    InvalidSemanticValueError,
    MissingOperationArgumentError,
    SemanticAnalysisError,
    SemanticError,
    SemanticModelError,
    UnknownOperationError,
    UnsupportedOperationError,
)
from app.build.semantic.generator import INDENT, generate_cpp
from app.build.semantic.models import (
    ARITHMETIC_OPERATORS,
    ASSIGNMENT_OPERATORS,
    COMPARISON_OPERATORS,
    LOGICAL_OPERATORS,
    ORDERING_OPERATORS,
    UPDATE_OPERATORS,
    ForStatement,
    LogicalValue,
    NotValue,
    TernaryValue,
    UpdateStatement,
    DECLARABLE_TYPES,
    DECLARATION_QUALIFIERS,
    DECLARATION_TYPE_NAMES,
    DEFAULT_TYPE_NAMES,
    ArithmeticValue,
    AssignmentStatement,
    CallStatement,
    CallValue,
    MethodCallStatement,
    ComparisonValue,
    ConditionalStatement,
    LiteralValue,
    OperationStatement,
    OperationValue,
    ReturnStatement,
    SemanticArgument,
    SemanticProgram,
    SemanticSection,
    SemanticStatement,
    SemanticValue,
    SymbolValue,
    UnsupportedReason,
    UnsupportedStatement,
    VariableDeclaration,
    is_variable_name,
)
from app.build.semantic.operations import (
    FUNCTIONS_IMPLEMENTATION,
    GPIO_DIGITAL_WRITE,
    GPIO_PIN_MODE,
    PROGRAM_LOOP,
    PROGRAM_SETUP,
    TEXT_INDEX_OF,
    TEXT_LENGTH,
    TEXT_SUBSTRING,
    TIME_DELAY,
    OperationForm,
    SemanticOperation,
    SemanticOperationRegistry,
    SemanticParameter,
    SemanticType,
    build_default_operations,
    default_semantic_operations,
    is_operation_id,
)

__all__ = [
    "LOGICAL_OPERATORS",
    "ORDERING_OPERATORS",
    "UPDATE_OPERATORS",
    "ForStatement",
    "LogicalValue",
    "NotValue",
    "TernaryValue",
    "UpdateStatement",
    "ASSIGNMENT_OPERATORS",
    "DECLARATION_QUALIFIERS",
    "DECLARATION_TYPE_NAMES",
    "DEFAULT_TYPE_NAMES",
    "AssignmentStatement",
    "CallValue",
    "MethodCallStatement",
    "ARITHMETIC_OPERATORS",
    "COMPARISON_OPERATORS",
    "CPP_DECLARATION_TYPES",
    "DECLARABLE_TYPES",
    "FUNCTIONS_IMPLEMENTATION",
    "GPIO_DIGITAL_WRITE",
    "GPIO_PIN_MODE",
    "INDENT",
    "PROGRAM_LOOP",
    "PROGRAM_SETUP",
    "TEXT_INDEX_OF",
    "TEXT_LENGTH",
    "TEXT_SUBSTRING",
    "TIME_DELAY",
    "ArithmeticValue",
    "MethodEmission",
    "OperationValue",
    "ReturnStatement",
    "VariableDeclaration",
    "is_variable_name",
    "CallEmission",
    "CallStatement",
    "ComparisonValue",
    "ConditionalStatement",
    "CppEmission",
    "CppEmissionTable",
    "CppGenerationError",
    "FunctionEmission",
    "InvalidContainerError",
    "InvalidSemanticValueError",
    "LiteralValue",
    "MissingOperationArgumentError",
    "OperationForm",
    "OperationStatement",
    "SemanticAnalysisError",
    "SemanticArgument",
    "SemanticError",
    "SemanticModelError",
    "SemanticOperation",
    "SemanticOperationRegistry",
    "SemanticParameter",
    "SemanticProgram",
    "SemanticSection",
    "SemanticStatement",
    "SemanticType",
    "SemanticValue",
    "SymbolValue",
    "UnknownOperationError",
    "UnsupportedOperationError",
    "UnsupportedReason",
    "UnsupportedStatement",
    "analyze_document",
    "build_default_emissions",
    "build_default_operations",
    "default_cpp_emissions",
    "default_semantic_operations",
    "generate_cpp",
    "is_operation_id",
]
