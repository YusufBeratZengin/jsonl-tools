from __future__ import annotations

import gzip
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jsonl_tools.cli import main

Result = tuple[int, bytes, str]


@pytest.fixture
def run() -> Callable[..., Result]:
    """Run the CLI in-process: ``run("head", "-n", "2", "-", stdin=b"...")``."""

    def _run(*argv: str, stdin: bytes = b"") -> Result:
        out = io.BytesIO()
        err = io.StringIO()
        code = main(list(argv), stdin=io.BytesIO(stdin), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    return _run


def jl(*records: Any) -> bytes:
    """Encode records as JSONL bytes."""
    return b"".join(json.dumps(r).encode() + b"\n" for r in records)


@pytest.fixture
def write(tmp_path: Path) -> Callable[..., Path]:
    def _write(name: str, data: bytes) -> Path:
        path = tmp_path / name
        if name.endswith(".gz"):
            data = gzip.compress(data, mtime=0)
        path.write_bytes(data)
        return path

    return _write


@pytest.fixture
def chat() -> bytes:
    return jl(
        {"id": 1, "role": "user", "text": "hello world", "usage": {"tokens": 10}},
        {"id": 2, "role": "assistant", "text": "Hi there", "usage": {"tokens": 1500}},
        {"id": 3, "role": "user", "text": "ERROR: boom", "usage": {"tokens": 2000.5}},
        {"id": 4, "role": "user", "text": "hello world", "usage": {"tokens": 10}},
        {"id": 5, "role": "tool"},
    )
