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
