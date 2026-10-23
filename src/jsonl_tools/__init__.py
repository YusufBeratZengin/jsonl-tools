"""jsonl-tools: streaming toolkit for large JSONL and .jsonl.gz files."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from ._io import (
    BadLineError,
    JsonlError,
    iter_lines,
    iter_records,
    open_input,
    open_output,
    parse_line,
)
from .expr import ExprError, compile_expr
from .paths import MISSING, PathError, get_value, get_values, parse_path
from .schema import SchemaInferrer
from .stats import Stats

__version__ = "0.1.0"

__all__ = [
    "MISSING",
    "BadLineError",
    "ExprError",
    "JsonlError",
    "PathError",
    "SchemaInferrer",
    "Stats",
    "compile_expr",
    "get_value",
    "get_values",
    "iter_lines",
    "iter_records",
    "open_input",
    "open_output",
    "parse_line",
    "parse_path",
    "read_jsonl",
]


def read_jsonl(path: str | os.PathLike[str], *, skip_bad: bool = False) -> Iterator[Any]:
    """Lazily yield the parsed value of every non-blank line of a ``.jsonl``/``.jsonl.gz`` file."""
    with open_input(os.fspath(path)) as f:
        for _, _, value in iter_records(iter_lines(f), skip_bad=skip_bad):
            yield value
