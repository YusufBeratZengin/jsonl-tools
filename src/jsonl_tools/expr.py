"""Tiny filter expressions: ``usage.tokens>1000``, ``role==user``, ``text~regex``."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .paths import get_values, parse_path

Predicate = Callable[[Any], bool]

_BINARY = re.compile(r"^\s*([^\s=!<>~]+)\s*(==|!=|>=|<=|!~|~|>|<)\s*(.*?)\s*$", re.DOTALL)
_UNARY = re.compile(r"^\s*(\S+)\s+(exists|missing)\s*$")


class ExprError(ValueError):
    """Raised for an invalid filter expression."""


def _literal(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return bool(a == b)


def _make_ordering(op: str, expected: Any) -> Callable[[Any], bool]:
    if not (_is_number(expected) or isinstance(expected, str)):
        raise ExprError(f"operator {op} needs a number or string, got {expected!r}")

    def compare(v: Any) -> bool:
        both_numbers = _is_number(expected) and _is_number(v)
        both_strings = isinstance(expected, str) and isinstance(v, str)
        if not (both_numbers or both_strings):
            return False
        if op == ">":
            return bool(v > expected)
        if op == ">=":
            return bool(v >= expected)
        if op == "<":
            return bool(v < expected)
        return bool(v <= expected)

    return compare


def compile_expr(text: str) -> Predicate:
    """Compile an expression into a predicate over a parsed JSON value.

    Operators: ``==  !=  >  >=  <  <=`` (JSON literal or bare word on the right), ``~`` / ``!~``
    (regular-expression search on strings), and ``PATH exists`` / ``PATH missing``.
    A path with ``[]`` matches if any element matches; ``!=`` and ``!~`` are the exact negation
    of ``==`` and ``~``, so a missing field satisfies them.
    """
    unary = _UNARY.match(text)
    if unary:
        path = parse_path(unary[1])
        want = unary[2] == "exists"
        return lambda obj: bool(get_values(obj, path)) == want

    m = _BINARY.match(text)
    if m is None:
        raise ExprError(f"cannot parse expression {text!r}")
    path = parse_path(m[1])
    op, raw = m[2], m[3]
    if raw == "":
        raise ExprError(f"missing value in expression {text!r}")

    positive: Callable[[Any], bool]
    if op in ("~", "!~"):
        try:
            rx = re.compile(raw)
        except re.error as exc:
            raise ExprError(f"invalid regex {raw!r}: {exc}") from None

        def positive(v: Any) -> bool:
            return isinstance(v, str) and rx.search(v) is not None

    elif op in ("==", "!="):
        expected = _literal(raw)

        def positive(v: Any) -> bool:
            return _equal(v, expected)

    else:
        positive = _make_ordering(op, _literal(raw))

    negate = op in ("!=", "!~")

    def predicate(obj: Any) -> bool:
        hit = any(positive(v) for v in get_values(obj, path))
        return not hit if negate else hit

    return predicate
