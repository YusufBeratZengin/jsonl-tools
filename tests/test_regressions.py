"""Regressions for streaming boundaries and safe CLI failures (no subprocesses)."""

import gzip
import io
import json
import random

import pytest

from jsonl_tools import PathError, SchemaInferrer, Stats, ops, parse_path
from jsonl_tools._io import JsonlError, dumps, iter_lines


@pytest.mark.parametrize("command", ["head", "tail", "sample"])
def test_negative_counts_are_errors(run, command):
    code, out, error = run(command, "-n", "-1", stdin=b"1\n")
    assert code == 2 and not out and "non-negative" in error


@pytest.mark.parametrize(
    "argv",
    [
        ("stats", "--max-paths", "0"),
        ("stats", "--max-depth", "-1"),
        ("stats", "--top", "-1"),
        ("schema", "--max-props", "0"),
        ("validate", "--limit", "-1"),
        ("split", "-l", "1", "--prefix", "../outside"),
    ],
)
def test_invalid_limits_fail_before_processing(run, argv):
    assert run(*argv, stdin=b"1\n")[0] == 2


def test_many_bad_lines_report_total_with_bounded_preview(run):
    code, out, error = run("select", "-", "a", "--skip-bad", stdin=b"bad\n" * 200 + b"{}\n")
    assert code == 0 and out == b"{}\n"
    assert "skipped 200 bad line(s) (first: 1, 2, 3, 4, 5, ...)" in error


def test_tail_bom_only_first_line_matches_streaming():
    data = b"\xef\xbb\xbf\n  \n1\n2\n"
    assert ops.tail_file(io.BytesIO(data), 10) == ops.tail(iter_lines(io.BytesIO(data)), 10)


def test_malformed_negative_index_is_a_path_error():
    with pytest.raises(PathError):
        parse_path("a[-]")


@pytest.mark.parametrize("limits", [{"max_lines": 0}, {"max_bytes": -1}])
def test_library_split_rejects_nonpositive_limits(limits):
    with pytest.raises(JsonlError, match="positive"):
        ops.split_lines([], lambda _: io.BytesIO(), **limits)


def test_head_does_not_read_ahead():
    def source():
        yield 1, b"first"
        raise AssertionError("head consumed an extra line")

    assert list(ops.head(source(), 1)) == [b"first"]
    assert ops.tail([], 0) == []
    assert ops.sample_reservoir([], 0, random.Random(0)) == []


def test_lone_surrogates_roundtrip_in_projection_and_dedupe():
    assert json.loads(dumps({"text": "\ud800"})) == {"text": "\ud800"}
    rows = [(1, b"first", {"x": "\ud800"}), (2, b"second", {"x": "\ud801"})]
    assert list(ops.dedupe_by(rows, [parse_path("x")])) == [b"first", b"second"]


def test_schema_depth_cap_and_empty_input():
    inferrer = SchemaInferrer(max_depth=1)
    assert set(inferrer.schema()) == {"$schema"}
    inferrer.add({"a": {"b": 1}})
    assert inferrer.schema()["properties"]["a"] == {"type": "object", "properties": {}}


def test_stats_extreme_numbers_produce_finite_json():
    stats = Stats()
    for value in [1e308, 1e308, -(10**400), -1e308]:
        stats.add({"x": value})
    report = stats.result()
    numeric = report["paths"]["x"]["numeric"]
    assert numeric["count"] == 3
    assert numeric["mean"] == pytest.approx(1e308 / 3, rel=1e-5)
    assert numeric["p50"] == 1e308
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("command,extra", [("head", []), ("validate", ["--clean"])])
def test_same_input_output_is_rejected_before_truncation(run, tmp_path, command, extra):
    source = tmp_path / "source.jsonl"
    source.write_bytes(b"1\n2\n")
    args = [command, str(source), *(extra or ["-o"]), str(source)]
    code, _, error = run(*args)
    assert code == 2 and "different files" in error
    assert source.read_bytes() == b"1\n2\n"


def test_gzip_split_can_be_removed_immediately(run, tmp_path):
    code, _, _ = run("split", "-l", "1", "--gzip", "--out-dir", str(tmp_path), stdin=b"1\n2\n")
    assert code == 0
    for path in tmp_path.glob("*.gz"):
        path.unlink()  # Windows rejects unlink when the raw gzip file handle is leaked.


@pytest.mark.parametrize("limits", [{"max_lines": 2}, {"max_bytes": 4}])
def test_split_streams_complete_records_and_closes_parts(limits):
    parts = []

    class Part(io.BytesIO):
        def close(self):
            if not self.closed:
                parts.append(self.getvalue())
            super().close()

    count = ops.split_lines([(1, b"1"), (2, b"2"), (3, b"33333")], lambda _: Part(), **limits)
    assert count == 2 and parts == [b"1\n2\n", b"33333\n"]


def test_split_closes_current_part_on_input_failure():
    part = io.BytesIO()

    def source():
        yield 1, b"1"
        raise OSError("read failed")

    with pytest.raises(OSError, match="read failed"):
        ops.split_lines(source(), lambda _: part, max_lines=10)
    assert part.closed


def test_corrupt_deflate_is_a_clean_cli_error(run):
    data = bytearray(gzip.compress(b"1\n"))
    data[10] = 0xff  # reserved deflate block type
    code, _, error = run("count", stdin=bytes(data))
    assert code == 2 and "jsonl-tools: error:" in error


def test_unbounded_size_is_a_clean_cli_error(run):
    code, _, error = run("split", "--size", "9" * 400 + "G", stdin=b"1\n")
    assert code == 2 and "size" in error


def test_canonical_dedupe_retains_unparseable_deep_lines(run):
    deep = b"[" * 2000 + b"]" * 2000
    code, out, _ = run("dedupe", "--canonical", stdin=deep + b"\n" + deep + b"\n")
    assert code == 0 and out == deep + b"\n"
