"""Streaming operations; deduplication stores one digest per unique key."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from itertools import islice
from typing import Any, BinaryIO

from ._io import JsonlError, dumps, write_line
from .expr import Predicate
from .paths import MISSING, Path, get_value, is_plain, set_nested

Lines = Iterable[tuple[int, bytes]]
Records = Iterable[tuple[int, bytes, Any]]


# --- head / tail / count ---------------------------------------------------------------------


def head(lines: Lines, n: int) -> Iterator[bytes]:
    """First ``n`` non-blank lines; stops reading as soon as it has them."""
    for _, raw in islice(lines, max(n, 0)):
        yield raw


def tail(lines: Lines, n: int) -> list[bytes]:
    """Last ``n`` non-blank lines, holding at most ``n`` lines in memory."""
    if n <= 0:
        return []
    return [raw for _, raw in deque(lines, maxlen=n)]


def tail_file(f: BinaryIO, n: int) -> list[bytes]:
    """Like :func:`tail` for a seekable, uncompressed file: reads only the end of the file."""
    if n <= 0:
        return []
    end = f.seek(0, os.SEEK_END)
    pos = end
    chunk = 1 << 16
    data = b""
    while pos > 0:
        step = min(chunk, pos)
        pos -= step
        f.seek(pos)
        data = f.read(step) + data
        # one extra piece because the first one may be cut in half
        if sum(1 for ln in data.split(b"\n") if ln.strip()) > n:
            break
        chunk = min(chunk * 2, 1 << 24)
    pieces = [ln.strip() for ln in data.split(b"\n")]
    if pos > 0 and pieces:
        pieces = pieces[1:]  # drop the possibly partial first line
    out = [p for p in pieces if p]
    if pos == 0 and pieces and pieces[0].startswith(b"\xef\xbb\xbf"):
        pieces[0] = pieces[0][3:].strip()
        out = [p for p in pieces if p]
    return out[-n:]


def count(lines: Lines) -> int:
    return sum(1 for _ in lines)


# --- filter / select -------------------------------------------------------------------------


def filter_records(
    records: Records,
    predicate: Predicate,
    *,
    invert: bool = False,
) -> Iterator[bytes]:
    for _, raw, value in records:
        if predicate(value) != invert:
            yield raw


def parse_select_spec(spec: str) -> tuple[str | None, str]:
    """``"alias=path"`` -> ``("alias", "path")``; ``"path"`` -> ``(None, "path")``."""
    if "=" in spec:
        alias, _, path = spec.partition("=")
        if not alias or not path:
            raise JsonlError(f"invalid select spec {spec!r}; use name=path")
        return alias, path
    return None, spec


def select_records(
    records: Records,
    fields: Sequence[tuple[str | None, str, Path]],
    *,
    flat: bool = False,
    null_missing: bool = False,
) -> Iterator[dict[str, Any]]:
    """Project records onto ``fields`` = ``(alias, path_text, parsed_path)`` triples."""
    for _, _, value in records:
        out: dict[str, Any] = {}
        for alias, text, path in fields:
            found = get_value(value, path)
            if found is MISSING:
                if not null_missing:
                    continue
                found = None
            if alias is not None or flat or not is_plain(path):
                out[alias or text] = found
            else:
                set_nested(out, path, found)
        yield out


# --- sample ----------------------------------------------------------------------------------


def sample_reservoir(lines: Lines, n: int, rng: random.Random) -> list[bytes]:
    """Uniform sample of ``n`` lines (Algorithm R), returned in original file order."""
    if n <= 0:
        return []
    kept: list[tuple[int, bytes]] = []
    for i, (_, raw) in enumerate(lines):
        if i < n:
            kept.append((i, raw))
        else:
            j = rng.randrange(i + 1)
            if j < n:
                kept[j] = (i, raw)
    kept.sort(key=lambda t: t[0])
    return [raw for _, raw in kept]


def sample_fraction(lines: Lines, fraction: float, rng: random.Random) -> Iterator[bytes]:
    """Keep each line independently with probability ``fraction`` (streaming, order kept)."""
    for _, raw in lines:
        if rng.random() < fraction:
            yield raw


# --- dedupe ----------------------------------------------------------------------------------


def _digest(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=16).digest()


def dedupe_lines(lines: Lines, *, canonical: bool = False) -> Iterator[bytes]:
    """Drop repeated lines, keeping the first. Memory grows with the number of unique lines
    (16 bytes of hash per line), not with the file size."""
    seen: set[bytes] = set()
    for _, raw in lines:
        if canonical:
            try:
                key = json.dumps(
                    json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ).encode("ascii")
            except (ValueError, RecursionError):
                key = raw
        else:
            key = raw
        h = _digest(key)
        if h not in seen:
            seen.add(h)
            yield raw


def dedupe_by(records: Records, fields: Sequence[Path]) -> Iterator[bytes]:
    """Drop records whose values at ``fields`` were already seen (first one wins).

    A missing field is distinct from ``null``.
    """
    seen: set[bytes] = set()
    for _, raw, value in records:
        key = []
        for path in fields:
            found = get_value(value, path)
            key.append([0] if found is MISSING else [1, found])
        h = _digest(
            json.dumps(key, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "ascii"
            )
        )
        if h not in seen:
            seen.add(h)
            yield raw


# --- split -----------------------------------------------------------------------------------

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$", re.IGNORECASE)
_UNITS = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "t": 1 << 40}


def parse_size(text: str) -> int:
    """``"500"``, ``"64k"``, ``"100MB"``, ``"1.5G"`` -> bytes (units are powers of 1024)."""
    m = _SIZE.match(text)
    if m is None:
        raise JsonlError(f"invalid size {text!r}; examples: 500k, 64MB, 1G")
    try:
        size = int(float(m[1]) * _UNITS[m[2].lower()])
    except (ValueError, OverflowError):
        raise JsonlError("size is outside the supported range") from None
    if size <= 0:
        raise JsonlError("size must be positive")
    return size


def split_lines(
    lines: Lines,
    open_part: Callable[[int], BinaryIO],
    *,
    max_lines: int | None = None,
    max_bytes: int | None = None,
) -> int:
    """Write lines into numbered parts, never cutting a line. Returns the number of parts.

    ``open_part(i)`` must return a fresh writable stream for part ``i`` (1-based); it is closed
    here. A part is rotated when adding the next line would exceed ``max_lines`` or
    ``max_bytes`` (a single over-long line still gets its own part).
    """
    if (max_lines is None) == (max_bytes is None):
        raise JsonlError("give exactly one of max_lines or max_bytes")
    if (max_lines is not None and max_lines <= 0) or (max_bytes is not None and max_bytes <= 0):
        raise JsonlError("split limits must be positive")
    part = 0
    out: BinaryIO | None = None
    n_lines = 0
    n_bytes = 0
    try:
        for _, raw in lines:
            size = len(raw) + 1
            full = out is not None and (
                (max_lines is not None and n_lines >= max_lines)
                or (max_bytes is not None and n_lines > 0 and n_bytes + size > max_bytes)
            )
            if out is None or full:
                if out is not None:
                    out.close()
                part += 1
                out = open_part(part)
                n_lines = n_bytes = 0
            write_line(out, raw)
            n_lines += 1
            n_bytes += size
    finally:
        if out is not None:
            out.close()
    return part


def passthrough(lines: Iterable[bytes], out: BinaryIO) -> int:
    n = 0
    for raw in lines:
        write_line(out, raw)
        n += 1
    return n


def write_objects(objs: Iterable[Any], out: BinaryIO) -> int:
    n = 0
    for obj in objs:
        write_line(out, dumps(obj))
        n += 1
    return n
