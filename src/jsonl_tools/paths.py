"""Field paths such as ``usage.tokens``, ``messages[0].role`` or ``messages[].role``."""

from __future__ import annotations

import re
from typing import Any

Segment = str | int | None  # str = key, int = index, None = every element (``[]``)
Path = tuple[Segment, ...]


class PathError(ValueError):
    """Raised for a malformed field path."""


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING: Any = _Missing()

_TOKEN = re.compile(r"^(?P<key>[^.\[\]]*)(?P<idx>(?:\[-?\d*\])*)$")
_INDEX = re.compile(r"\[(-?\d*)\]")


def parse_path(text: str) -> Path:
    """Parse ``a.b[0].c`` / ``a[].b`` into segments. ``[]`` matches every list element."""
    if not text:
        raise PathError("empty field path")
    segments: list[Segment] = []
    for part in text.split("."):
        m = _TOKEN.match(part)
        if m is None or (not m["key"] and not m["idx"]):
            raise PathError(f"invalid field path {text!r}")
        if m["key"]:
            segments.append(m["key"])
        for idx in _INDEX.findall(m["idx"]):
            segments.append(int(idx) if idx else None)
    return tuple(segments)


def has_wildcard(path: Path) -> bool:
    return any(seg is None for seg in path)


def is_plain(path: Path) -> bool:
    """True when the path only contains object keys (no indexes or wildcards)."""
    return all(isinstance(seg, str) for seg in path)


def get_values(obj: Any, path: Path) -> list[Any]:
    """Return every value reachable through ``path`` (empty when nothing matches)."""
    current = [obj]
    for seg in path:
        nxt: list[Any] = []
        for item in current:
            if seg is None:
                if isinstance(item, list):
                    nxt.extend(item)
            elif isinstance(seg, int):
                if isinstance(item, list) and -len(item) <= seg < len(item):
                    nxt.append(item[seg])
            elif isinstance(item, dict) and seg in item:
                nxt.append(item[seg])
        if not nxt:
            return []
        current = nxt
    return current


def get_value(obj: Any, path: Path) -> Any:
    """Single value for a wildcard-free path, ``MISSING`` when absent.

    With a wildcard the result is the list of all matches (``MISSING`` when empty).
    """
    if not has_wildcard(path):
        for seg in path:
            if isinstance(seg, int):
                if not (isinstance(obj, list) and -len(obj) <= seg < len(obj)):
                    return MISSING
                obj = obj[seg]
            else:
                if not (isinstance(obj, dict) and seg in obj):
                    return MISSING
                obj = obj[seg]
        return obj
    found = get_values(obj, path)
    return found if found else MISSING


def set_nested(dst: dict[str, Any], keys: Path, value: Any) -> None:
    """Set ``value`` at a plain (keys only) path, creating intermediate objects."""
    cur = dst
    for key in keys[:-1]:
        assert isinstance(key, str)
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    last = keys[-1]
    assert isinstance(last, str)
    cur[last] = value
