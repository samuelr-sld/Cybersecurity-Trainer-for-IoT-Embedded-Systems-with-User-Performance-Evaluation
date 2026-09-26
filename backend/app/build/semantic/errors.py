"""Errors raised by the semantic layer.

Their own module so `models.py` (which validates) and `analyzer.py` (which
produces) can both raise them without either importing the other.

WHAT IS NOT AN ERROR HERE. C++ the semantic layer cannot represent is never
an error — it becomes an `UnsupportedStatement` carrying its exact source
text (see `models.py`). A `SemanticAnalysisError` means the *input structure*
was impossible (a function section with no body braces at all), which a
`BuildDocument` produced by `app/build/discovery/analyzer.py` can never be.
"""

from __future__ import annotations


class SemanticError(ValueError):
    """Base for every error the semantic layer raises."""


class SemanticModelError(SemanticError):
    """A semantic model object was constructed in an invalid shape."""


class UnknownOperationError(SemanticError):
    """An operation id was requested that the registry does not define."""


class SemanticAnalysisError(SemanticError):
    """Source structure the analyzer cannot make any honest sense of."""


# --- generation: the IR -> C++ direction (Phase B6) --------------------------
#
# WHAT IS NOT AN ERROR HERE EITHER, and it is the same principle read backwards.
# An `UnsupportedStatement` is never a generation failure: its exact source text
# IS its C++, emitted verbatim. The generator raises only when it is asked to
# write something it has no honest rendering for — an operation no table
# declares, an operand that is not there, a value with no C++ spelling. Every
# one of those means the IR, the operation registry and the emission table
# disagree about one construct, which firmware cannot cause.
#
# NOTHING IS EVER SILENTLY OMITTED. There is no "skip what we cannot write"
# path anywhere in `generator.py`; a construct is generated, carried verbatim,
# or raised.


class CppGenerationError(SemanticError):
    """Base for every error raised while writing C++ from the IR."""


class UnsupportedOperationError(CppGenerationError):
    """An operation cannot be written as C++.

    The registry declares no operation with that id, no emission declares how
    it is spelled, or the statement carries an operand the declared operation
    does not name. Never raised for source the IR simply did not understand —
    that is an `UnsupportedStatement` and it generates its own text.
    """


class InvalidContainerError(CppGenerationError):
    """A construct is being written in a role its form does not allow.

    A section whose operation the registry calls a statement, an emission that
    spells a container as a call, or a statement form the generator has no rule
    for. `models.py` makes most of these impossible by construction; they stay
    reachable through an injected registry or emission table, which is exactly
    the drift this error is here to surface.
    """


class MissingOperationArgumentError(CppGenerationError):
    """A declared parameter has no argument to write into the call.

    `OperationStatement` covers every parameter of the operation it carries, so
    this is drift rather than a malformed statement: the registry the generator
    was given declares an operand the statement was never built with.
    """


class InvalidSemanticValueError(CppGenerationError):
    """A value has no C++ literal the generator will write for it.

    A value form outside `LiteralValue`/`SymbolValue`, a non-finite number, or
    text holding a character this generator will not guess an escape for.
    Refused rather than approximated — writing `inf` or a raw control byte into
    firmware would be inventing a literal the source never had.
    """
