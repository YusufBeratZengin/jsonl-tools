"""Streaming input/output helpers: plain and gzip files, stdin/stdout, line parsing."""

from __future__ import annotations

import gzip
import io
import json
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, BinaryIO, cast

GZIP_MAGIC = b"\x1f\x8b"
UTF8_BOM = b"\xef\xbb\xbf"
BUFSIZE = 1 << 20


class JsonlError(Exception):
    """Base class for errors that the CLI reports without a traceback."""


class BadLineError(JsonlError):
    """A line is not valid JSON."""

    def __init__(self, lineno: int, message: str) -> None:
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno
        self.message = message


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


_DECODER = json.JSONDecoder(parse_constant=_reject_constant)


def parse_line(raw: bytes) -> Any:
    """Parse one JSONL line. Raises ``ValueError`` with a short, human-readable message."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 at byte {exc.start}") from None
    try:
        return _DECODER.decode(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{exc.msg} (column {exc.colno})") from None
    except RecursionError:
        raise ValueError("nesting too deep") from None


def _is_gzip(f: BinaryIO) -> bool:
    peek = getattr(f, "peek", None)
    if peek is not None:
        return bool(peek(2)[:2] == GZIP_MAGIC)
    if f.seekable():
        pos = f.tell()
        head = f.read(2)
        f.seek(pos)
        return head == GZIP_MAGIC
    return False


@contextmanager
def open_input(path: str | None, stdin: BinaryIO | None = None) -> Iterator[BinaryIO]:
    """Open ``path`` (or stdin for ``None``/``-``) for binary reading, decompressing gzip.

    Gzip is detected from the magic bytes, not the file name.
    """
    close_raw = path not in (None, "-")
    raw: BinaryIO = open(path, "rb") if close_raw and path else (stdin or sys.stdin.buffer)  # noqa: SIM115
    reader: BinaryIO = raw
    try:
        if _is_gzip(raw):
            reader = io.BufferedReader(gzip.GzipFile(fileobj=raw, mode="rb"), BUFSIZE)
        yield reader
    finally:
        if reader is not raw:
            reader.close()
        if close_raw:
            raw.close()


@contextmanager
def open_output(path: str | None, stdout: BinaryIO | None = None) -> Iterator[BinaryIO]:
    """Open ``path`` (or stdout for ``None``/``-``) for binary writing; ``.gz`` compresses."""
    if path in (None, "-"):
        out = stdout or sys.stdout.buffer
        try:
            yield out
        finally:
            out.flush()
        return
    assert path is not None
    with open(path, "wb") as raw:
        if path.endswith(".gz"):
            gz = gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0)
            writer = io.BufferedWriter(cast(io.RawIOBase, gz), BUFSIZE)
            try:
                yield writer
            finally:
                writer.close()
        else:
            yield raw


def iter_lines(f: BinaryIO) -> Iterator[tuple[int, bytes]]:
    """Yield ``(line_number, stripped_bytes)`` for every non-blank line, streaming."""
    for n, line in enumerate(f, 1):
        if n == 1 and line.startswith(UTF8_BOM):
            line = line[3:]
        line = line.strip()
        if line:
            yield n, line


def iter_records(
    lines: Iterator[tuple[int, bytes]],
    *,
    skip_bad: bool = False,
    on_bad: Callable[[int, str], None] | None = None,
) -> Iterator[tuple[int, bytes, Any]]:
    """Parse lines lazily, yielding ``(line_number, raw_line, value)``.

    A bad line raises :class:`BadLineError` unless ``skip_bad`` is set, in which case it is
    reported through ``on_bad`` and skipped.
    """
    for n, raw in lines:
        try:
            value = parse_line(raw)
        except ValueError as exc:
            if not skip_bad:
                raise BadLineError(n, str(exc)) from None
            if on_bad is not None:
                on_bad(n, str(exc))
            continue
        yield n, raw, value


def dumps(value: Any) -> bytes:
    """Compact UTF-8 JSON, falling back to ASCII escapes for lone surrogates."""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def write_line(out: BinaryIO, raw: bytes) -> None:
    out.write(raw)
    out.write(b"\n")
