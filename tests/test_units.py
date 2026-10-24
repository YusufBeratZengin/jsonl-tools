from __future__ import annotations

import gzip
import io
import os
import random
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

import jsonl_tools
from jsonl_tools import ops
from jsonl_tools._io import (
    BadLineError,
    iter_lines,
    iter_records,
    open_input,
    open_output,
    parse_line,
)
from jsonl_tools.expr import ExprError, compile_expr
from jsonl_tools.paths import MISSING, PathError, get_value, get_values, parse_path, set_nested
from jsonl_tools.stats import FrequentValues, Stats, percentile

from .conftest import jl

SRC = str(Path(__file__).resolve().parent.parent / "src")
ENV = {**os.environ, "PYTHONPATH": SRC}


def test_parse_path():
    assert parse_path("a.b") == ("a", "b")
    assert parse_path("a[0].b[-1]") == ("a", 0, "b", -1)
    assert parse_path("a[].b") == ("a", None, "b")
    assert parse_path("[0][1]") == (0, 1)
    for bad in ["", "a..b", "a.", ".a", "a[x]", "a[", "[]x"]:
        with pytest.raises(PathError):
            parse_path(bad)


def test_get_value_and_values():
    obj = {"a": [{"b": 1}, {"b": 2}, {"c": 3}], "s": "str"}
    assert get_value(obj, parse_path("a[1].b")) == 2
    assert get_value(obj, parse_path("a[-1].c")) == 3
    assert get_value(obj, parse_path("a[].b")) == [1, 2]
    assert get_value(obj, parse_path("a[9].b")) is MISSING
    assert get_value(obj, parse_path("a[].zzz")) is MISSING
    assert get_value(obj, parse_path("s.x")) is MISSING
    assert get_value(obj, parse_path("s[0]")) is MISSING
    assert get_values(obj, parse_path("a[].b")) == [1, 2]
    assert get_values(5, parse_path("a")) == []
    assert not MISSING


def test_set_nested():
    d: dict[str, object] = {"a": 1}
    set_nested(d, ("a", "b"), 2)
    set_nested(d, ("x", "y", "z"), 3)
    assert d == {"a": {"b": 2}, "x": {"y": {"z": 3}}}


def test_parse_line_errors():
    assert parse_line(b'{"a": [1, 2.5, null]}') == {"a": [1, 2.5, None]}
    for bad in [b"{", b"", b"Infinity", b'{"a":NaN}', b"1 2", b"\xff"]:
        with pytest.raises(ValueError):
            parse_line(bad)


def test_iter_records_skip_and_raise():
    lines = [(1, b"1"), (2, b"x"), (3, b"3")]
    with pytest.raises(BadLineError) as exc:
        list(iter_records(iter(lines)))
    assert exc.value.lineno == 2
    seen: list[int] = []
    got = list(iter_records(iter(lines), skip_bad=True, on_bad=lambda n, _m: seen.append(n)))
    assert [v for _, _, v in got] == [1, 3] and seen == [2]
    assert len(list(iter_records(iter(lines), skip_bad=True))) == 2


def test_io_roundtrip_gzip(tmp_path):
    path = str(tmp_path / "x.jsonl.gz")
    with open_output(path) as out:
        out.write(b'{"a":1}\n{"a":2}\n')
    first = Path(path).read_bytes()
    assert gzip.decompress(first) == b'{"a":1}\n{"a":2}\n'
    with open_input(path) as f:
        assert [ln for _, ln in iter_lines(f)] == [b'{"a":1}', b'{"a":2}']
    # deterministic gzip output (no timestamp / file name)
    with open_output(str(tmp_path / "y.jsonl.gz")) as out:
        out.write(b'{"a":1}\n{"a":2}\n')
    assert first == (tmp_path / "y.jsonl.gz").read_bytes()


def test_open_input_non_peekable_streams():
    class Plain(io.RawIOBase):
        def __init__(self, data: bytes) -> None:
            self.buf = io.BytesIO(data)

        def readable(self) -> bool:
            return True

        def readinto(self, b):  # type: ignore[no-untyped-def]
            return self.buf.readinto(b)

    with open_input("-", io.BytesIO(gzip.compress(b"1\n2\n"))) as f:
        assert f.read() == b"1\n2\n"
    with open_input("-", io.BufferedReader(Plain(b"1\n"))) as f:  # type: ignore[arg-type]
        assert f.read() == b"1\n"


def test_expr_semantics():
    p = compile_expr("a==1")
    assert p({"a": 1}) and p({"a": 1.0}) and not p({"a": True}) and not p({"a": "1"})
    assert compile_expr("a==true")({"a": True}) and not compile_expr("a==true")({"a": 1})
    assert compile_expr("a==null")({"a": None}) and not compile_expr("a==null")({})
    assert compile_expr("a>1")({"a": 2}) and not compile_expr("a>1")({"a": "9"})
    assert not compile_expr("a>1")({"a": True})
    assert compile_expr("a>b")({"a": "c"})
    assert compile_expr("a!=1")({}) and not compile_expr("a!=1")({"a": 1})
    assert compile_expr("a ~ x.z")({"a": "xyz"}) and not compile_expr("a~x")({"a": 5})
    assert compile_expr("a.b==c d")({"a": {"b": "c d"}})
    assert compile_expr("a exists")({"a": None})
    for bad in ["", "a", "a>", "a>[1]", "a>null", "a~("]:
        with pytest.raises(ExprError):
            compile_expr(bad)


def test_percentile():
    assert percentile([5.0], 0.95) == 5.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 1) == 4.0
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_frequent_values_exact_and_heavy_hitters():
    fv = FrequentValues(capacity=3)
    for k in "aabbc":
        fv.add(k)
    assert fv.exact and fv.top(2) == [("a", 2), ("b", 2)]
    fv = FrequentValues(capacity=10)
    rng = random.Random(1)
    for i in range(5000):
        fv.add("hot" if rng.random() < 0.4 else f"u{i}")
    assert not fv.exact and fv.top(1)[0][0] == "hot" and len(fv.counts) <= 10


def test_stats_percentiles_approximate_for_large_input():
    s = Stats(seed=1)
    for i in range(20000):
        s.add({"x": i})
    info = s.result()["paths"]["x"]["numeric"]
    assert not info["percentiles_exact"]
    assert abs(info["p50"] - 10000) < 800 and abs(info["p95"] - 19000) < 800
    assert info["min"] == 0 and info["max"] == 19999 and info["mean"] == 9999.5


def test_stats_presence_counts_once_per_record():
    s = Stats()
    s.add({"a": [{"b": 1}, {"b": 2}]})
    s.add({"a": []})
    info = s.result()["paths"]["a[].b"]
    assert info["present"] == 1 and info["occurrences"] == 2 and info["present_ratio"] == 0.5


def test_tail_file_matches_streaming_tail(tmp_path):
    rows = [f'{{"i":{i},"pad":"{"x" * (i % 7) * 50}"}}'.encode() for i in range(300)]
    data = b"\n\n".join(rows) + b"\n"
    path = tmp_path / "t.jsonl"
    path.write_bytes(data)
    for n in (1, 5, 299, 300, 1000):
        with open(path, "rb") as f:
            fast = ops.tail_file(f, n)
        assert fast == ops.tail(iter_lines(io.BytesIO(data)), n)
    with open(path, "rb") as f:
        assert ops.tail_file(f, 0) == []
    # no trailing newline, bom, tiny file
    path.write_bytes(b'\xef\xbb\xbf{"a":1}')
    with open(path, "rb") as f:
        assert ops.tail_file(f, 3) == [b'{"a":1}']


def test_parse_size():
    assert ops.parse_size("500") == 500
    assert ops.parse_size("64k") == 64 * 1024
    assert ops.parse_size("2MB") == 2 * 1024**2
    assert ops.parse_size("1.5G") == int(1.5 * 1024**3)
    for bad in ["", "x", "-1", "0", "1 parsec"]:
        with pytest.raises(Exception, match="size"):
            ops.parse_size(bad)


def test_split_lines_requires_exactly_one_limit():
    with pytest.raises(Exception, match="exactly one"):
        ops.split_lines([], lambda i: io.BytesIO())


def test_public_api_read_jsonl(tmp_path):
    p = tmp_path / "a.jsonl.gz"
    p.write_bytes(gzip.compress(jl({"a": 1}) + b"bad\n" + jl({"a": 2})))
    assert list(jsonl_tools.read_jsonl(p, skip_bad=True)) == [{"a": 1}, {"a": 2}]
    with pytest.raises(BadLineError):
        list(jsonl_tools.read_jsonl(p))
    inf = jsonl_tools.SchemaInferrer()
    inf.add({"a": 1})
    assert inf.schema()["properties"]["a"]["type"] == "integer"


def test_module_entry_point(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "jsonl_tools", "count", "-"],
        input=jl({"a": 1}, {"a": 2}),
        capture_output=True,
        env=ENV,
        check=True,
    )
    assert proc.stdout.strip() == b"2"


def test_head_closes_pipe_quietly():
    big = b'{"a":1}\n' * 100000
    proc = subprocess.run(
        [sys.executable, "-m", "jsonl_tools", "head", "-n", "1"],
        input=big,
        capture_output=True,
        env=ENV,
        check=True,
    )
    assert proc.stdout == b'{"a":1}\n' and proc.stderr == b""


class _NullSink(io.RawIOBase):
    def __init__(self) -> None:
        self.bytes = 0

    def writable(self) -> bool:
        return True

    def write(self, b):  # type: ignore[no-untyped-def]
        self.bytes += len(b)
        return len(b)


def test_streaming_memory_is_flat():
    """Lines are generated lazily and discarded, so peak memory is independent of input size."""

    def peak(n: int) -> tuple[int, int]:
        line = b'{"id":1,"text":"some text here","usage":{"tokens":12}}'
        lines = ((i, line) for i in range(n))
        sink = _NullSink()
        tracemalloc.start()
        records = iter_records(lines)
        ops.passthrough(ops.filter_records(records, compile_expr("usage.tokens>1")), sink)  # type: ignore[arg-type]
        _, high = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return high, sink.bytes

    small, _ = peak(2_000)
    large, written = peak(100_000)
    assert written == 100_000 * 55
    assert large < 300_000 and large < small + 100_000
