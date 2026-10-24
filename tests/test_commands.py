from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from .conftest import jl


def lines(data: bytes) -> list[str]:
    return data.decode().splitlines()


def test_head_tail_count_stdin(run, chat):
    code, out, _ = run("head", "-n", "2", stdin=chat)
    assert code == 0 and len(lines(out)) == 2 and json.loads(lines(out)[0])["id"] == 1
    _, out, _ = run("tail", "-n", "2", stdin=chat)
    assert [json.loads(x)["id"] for x in lines(out)] == [4, 5]
    _, out, _ = run("count", stdin=chat)
    assert out == b"5\n"


def test_blank_lines_ignored_and_crlf_normalized(run):
    data = b'{"a":1}\r\n\r\n   \n{"a":2}'
    _, out, _ = run("head", stdin=data)
    assert out == b'{"a":1}\n{"a":2}\n'
    assert run("count", stdin=data)[1] == b"2\n"


def test_bom_is_stripped(run):
    _, out, _ = run("head", "-n", "1", stdin=b'\xef\xbb\xbf{"a":1}\n')
    assert out == b'{"a":1}\n'


def test_head_zero_and_file_path(run, write, chat):
    path = write("c.jsonl", chat)
    assert run("head", "-n", "0", str(path))[1] == b""
    assert run("count", str(path))[1] == b"5\n"


def test_gzip_detected_by_content(run, write, chat):
    path = write("c.jsonl.gz", chat)
    renamed = path.with_name("noext")
    path.rename(renamed)
    assert run("count", str(renamed))[1] == b"5\n"
    _, out, _ = run("tail", "-n", "1", str(renamed))
    assert json.loads(out)["id"] == 5
    assert run("count", stdin=gzip.compress(chat))[1] == b"5\n"


def test_truncated_gzip_is_a_clean_error(run, write, chat):
    path = write("t.jsonl.gz", chat * 200)
    path.write_bytes(path.read_bytes()[:-30])
    code, _, err = run("count", str(path))
    assert code == 2 and "error" in err


def test_missing_file(run, tmp_path):
    code, _, err = run("count", str(tmp_path / "nope.jsonl"))
    assert code == 2 and "jsonl-tools: error" in err


def test_output_file_and_gzip_output(run, tmp_path, chat):
    target = tmp_path / "o.jsonl.gz"
    code, out, _ = run("head", "-n", "2", "-o", str(target), stdin=chat)
    assert code == 0 and out == b""
    assert len(gzip.decompress(target.read_bytes()).splitlines()) == 2
    plain = tmp_path / "o.jsonl"
    run("head", "-n", "1", "-o", str(plain), stdin=chat)
    assert plain.read_bytes().count(b"\n") == 1


@pytest.mark.parametrize(
    ("expr", "ids"),
    [
        ("usage.tokens>1000", [2, 3]),
        ("usage.tokens>=1500", [2, 3]),
        ("usage.tokens<100", [1, 4]),
        ("usage.tokens<=10", [1, 4]),
        ("role==user", [1, 3, 4]),
        ('role=="user"', [1, 3, 4]),
        ("role!=user", [2, 5]),
        ("text~^ERROR", [3]),
        ("text!~hello", [2, 3, 5]),
        ("usage exists", [1, 2, 3, 4]),
        ("usage.tokens missing", [5]),
        ("id==3", [3]),
    ],
)
def test_filter_expressions(run, chat, expr, ids):
    code, out, _ = run("filter", "-", expr, stdin=chat)
    assert code == 0
    assert [json.loads(x)["id"] for x in lines(out)] == ids


def test_filter_combinators_invert_count(run, chat):
    _, out, _ = run("filter", "-", "role==user", "--and", "usage.tokens>100", stdin=chat)
    assert [json.loads(x)["id"] for x in lines(out)] == [3]
    _, out, _ = run("filter", "-", "id==1", "--or", "id==5", stdin=chat)
    assert [json.loads(x)["id"] for x in lines(out)] == [1, 5]
    _, out, _ = run("filter", "-", "role==user", "-v", stdin=chat)
    assert [json.loads(x)["id"] for x in lines(out)] == [2, 5]
    assert run("filter", "-", "role==user", "-c", stdin=chat)[1] == b"3\n"


def test_filter_preserves_raw_line(run):
    raw = b'{"a": 1,   "b":"x"}\n'
    assert run("filter", "-", "a==1", stdin=raw)[1] == b'{"a": 1,   "b":"x"}\n'


def test_filter_wildcard_paths(run):
    data = jl({"m": [{"r": "u"}, {"r": "a"}]}, {"m": [{"r": "u"}]}, {"m": []})
    assert run("filter", "-", "m[].r==a", "-c", stdin=data)[1] == b"1\n"
    assert run("filter", "-", "m[0].r==u", "-c", stdin=data)[1] == b"2\n"
    assert run("filter", "-", "m[].r!=a", "-c", stdin=data)[1] == b"2\n"


@pytest.mark.parametrize("expr", ["nonsense", "a==", "a>[1]", "a~(", "[bad"])
def test_filter_bad_expression(run, expr):
    code, _, err = run("filter", "-", expr, stdin=b'{"a":1}\n')
    assert code == 2 and "error" in err


def test_filter_bad_line_fails_with_line_number(run):
    code, _, err = run("filter", "-", "a==1", stdin=b'{"a":1}\n{oops\n')
    assert code == 2 and "line 2" in err


def test_skip_bad(run):
    data = b'{"a":1}\nnot json\n{"a":1}\n\xff\xfe\n'
    code, out, err = run("filter", "-", "a==1", "--skip-bad", stdin=data)
    assert code == 0 and out.count(b"\n") == 2
    assert "skipped 2 bad line(s)" in err and "first: 2, 4" in err


def test_select_nested_alias_flat(run, chat):
    _, out, _ = run("select", "-", "id", "usage.tokens", "who=role", stdin=chat)
    first = json.loads(lines(out)[0])
    assert first == {"id": 1, "usage": {"tokens": 10}, "who": "user"}
    assert json.loads(lines(out)[4]) == {"id": 5, "who": "tool"}
    _, out, _ = run("select", "-", "--flat", "usage.tokens", stdin=chat)
    assert json.loads(lines(out)[0]) == {"usage.tokens": 10}
    _, out, _ = run("select", "-", "--null-missing", "usage.tokens", stdin=chat)
    assert json.loads(lines(out)[4]) == {"usage": {"tokens": None}}


def test_select_indexes_and_wildcards(run):
    data = jl({"m": [{"r": "u"}, {"r": "a"}]})
    _, out, _ = run("select", "-", "m[0].r", "all=m[].r", "m[5]", stdin=data)
    assert json.loads(out) == {"m[0].r": "u", "all": ["u", "a"]}


def test_select_bad_spec(run):
    assert run("select", "-", "=x", stdin=b"{}\n")[0] == 2
    assert run("select", "-", "a..b", stdin=b"{}\n")[0] == 2


def test_select_unicode_roundtrip(run):
    text = "Günaydın ✓"  # noqa: RUF001
    _, out, _ = run("select", "-", "t", stdin=('{"t":"%s"}\n' % text).encode())  # noqa: UP031
    assert out.decode() == '{"t":"%s"}\n' % text  # noqa: UP031


def test_sample_reproducible(run):
    data = jl(*({"i": i} for i in range(200)))
    a = run("sample", "-n", "10", "--seed", "3", stdin=data)[1]
    b = run("sample", "-n", "10", "--seed", "3", stdin=data)[1]
    c = run("sample", "-n", "10", "--seed", "4", stdin=data)[1]
    assert a == b and a != c
    ids = [json.loads(x)["i"] for x in lines(a)]
    assert len(ids) == 10 and ids == sorted(ids)
    frac = run("sample", "-p", "0.25", "--seed", "1", stdin=data)[1]
    assert 20 < len(lines(frac)) < 80
    assert run("sample", "-p", "1", stdin=data)[1].count(b"\n") == 200
    assert run("sample", "-p", "0", stdin=data)[1] == b""
    assert len(lines(run("sample", "-n", "500", stdin=data)[1])) == 200
    assert run("sample", "-n", "0", stdin=data)[1] == b""


def test_sample_argument_errors(run):
    assert run("sample", stdin=b"{}\n")[0] == 2
    assert run("sample", "-n", "1", "-p", "0.5", stdin=b"{}\n")[0] == 2
    assert run("sample", "-p", "2", stdin=b"{}\n")[0] == 2


def test_dedupe_whole_line_and_canonical(run):
    data = b'{"a":1,"b":2}\n{"b":2,"a":1}\n{"a":1,"b":2}\n{"a":3}\n'
    assert run("dedupe", stdin=data)[1].count(b"\n") == 3
    _, out, err = run("dedupe", "--canonical", "-v", stdin=data)
    assert out.count(b"\n") == 2 and "kept 2 of 4" in err


def test_dedupe_canonical_tolerates_bad_lines(run):
    assert run("dedupe", "--canonical", stdin=b"x\nx\n{}\n")[1] == b"x\n{}\n"


def test_dedupe_by_fields(run, chat):
    _, out, _ = run("dedupe", "--by", "role", stdin=chat)
    assert [json.loads(x)["id"] for x in lines(out)] == [1, 2, 5]
    _, out, _ = run("dedupe", "--by", "role,text", stdin=chat)
    assert [json.loads(x)["id"] for x in lines(out)] == [1, 2, 3, 5]
    _, out, _ = run("dedupe", "--by", "text", stdin=chat)  # missing text is its own key
    assert [json.loads(x)["id"] for x in lines(out)] == [1, 2, 3, 5]


def test_dedupe_by_null_vs_missing(run):
    data = jl({"a": None}, {}, {"a": None}, {})
    assert run("dedupe", "--by", "a", stdin=data)[1].count(b"\n") == 2


def test_validate(run):
    good = jl({"a": 1}, [1, 2])
    code, out, _ = run("validate", stdin=good)
    assert code == 0 and b"2 valid, 0 invalid" in out
    bad = b'{"a":1}\n{broken\n\n[1,\nNaN\n{"a":1} trailing\n"ok"\n'
    code, out, _ = run("validate", stdin=bad)
    text = out.decode()
    assert code == 1
    assert "line 2:" in text and "line 4:" in text and "line 5:" in text and "line 6:" in text
    assert text.splitlines()[-1] == "6 line(s): 2 valid, 4 invalid"


def test_validate_modes(run, tmp_path):
    bad = b'{"a":1}\n[1]\nnope\n\xff\n'
    code, out, _ = run("validate", "--object", "--json", stdin=bad)
    report = json.loads(out)
    assert code == 1 and report["lines"] == 4 and report["invalid"] == 3
    assert report["problems"][0] == {"line": 2, "error": "not a JSON object"}
    assert "invalid UTF-8" in report["problems"][2]["error"]
    code, out, _ = run("validate", "--limit", "1", stdin=bad)
    assert b"and 1 more" in out
    assert run("validate", "-q", stdin=bad) == (1, b"", "")
    clean = tmp_path / "clean.jsonl"
    run("validate", "-q", "--clean", str(clean), stdin=bad)
    assert clean.read_bytes() == b'{"a":1}\n[1]\n'
    assert run("validate", "--limit", "0", stdin=bad)[1].count(b"line ") == 2


def test_validate_deep_nesting(run):
    deep = b"[" * 100000 + b"]" * 100000 + b"\n"
    code, out, _ = run("validate", stdin=deep)
    assert code == 1 and b"nesting too deep" in out


def test_stats_text_and_json(run, chat):
    code, out, _ = run("stats", "-", stdin=chat)
    text = out.decode()
    assert code == 0 and "records: 5" in text
    assert "usage.tokens" in text and "80.0%" in text and "integer 75%, number 25%" in text
    _, out, _ = run("stats", "--json", "--top", "2", "--field", "usage", stdin=chat)
    report = json.loads(out)
    assert set(report["paths"]) == {"usage", "usage.tokens"}
    num = report["paths"]["usage.tokens"]["numeric"]
    assert num["min"] == 10 and num["max"] == 2000.5 and num["count"] == 4
    assert num["mean"] == pytest.approx((10 + 1500 + 2000.5 + 10) / 4, rel=1e-5)
    assert num["p50"] == pytest.approx(755) and num["percentiles_exact"]
    assert report["paths"]["usage.tokens"]["top_values"][0] == {"value": 10, "count": 2}


def test_stats_empty_and_options(run):
    assert run("stats", stdin=b"")[1] == b"records: 0\n"
    data = jl({"a": {"b": {"c": 1}}})
    _, out, _ = run("stats", "--json", "--max-depth", "1", stdin=data)
    assert set(json.loads(out)["paths"]) == {"a"}  # root depth is zero
    _, out, _ = run("stats", "--json", "--max-paths", "2", stdin=data)
    assert json.loads(out)["paths_truncated"] is True
    _, out, _ = run("stats", "--max-paths", "1", stdin=jl({"a": 1}, {"b": 2}))
    assert b"path limit reached" in out


def test_stats_non_object_records_and_strings(run):
    data = jl(1, "abc", None, True, [1, 2], {"s": "x" * 500})
    _, out, _ = run("stats", "--json", stdin=data)
    paths = json.loads(out)["paths"]
    assert set(paths["$"]["types"]) == {"integer", "string", "null", "boolean", "array", "object"}
    assert paths["s"]["string_length"] == {"min": 500, "max": 500, "mean": 500.0}
    assert "top_values" not in paths["s"]  # long strings are not tracked


def test_schema(run):
    data = jl(
        {"id": 1, "tags": ["a"], "meta": {"k": 1.5}, "opt": None},
        {"id": 2, "tags": [], "meta": {"k": 2}},
        {"id": 3.5, "tags": ["b", 1]},
    )
    code, out, _ = run("schema", stdin=data)
    schema = json.loads(out)
    assert code == 0
    assert schema["$schema"].startswith("https://json-schema.org")
    assert schema["required"] == ["id", "tags"]
    assert schema["properties"]["id"]["type"] == "number"
    assert schema["properties"]["tags"]["items"]["type"] == ["integer", "string"]
    assert schema["properties"]["meta"]["properties"]["k"]["type"] == "number"
    assert schema["properties"]["opt"]["type"] == "null"
    no_req = json.loads(run("schema", "--no-required", stdin=data)[1])
    assert "required" not in no_req


def test_schema_dynamic_keys_and_depth(run):
    data = jl({f"k{i}": i for i in range(10)})
    schema = json.loads(run("schema", "--max-props", "3", stdin=data)[1])
    assert len(schema["properties"]) == 3 and schema["additionalProperties"] is True
    deep = jl({"a": [[[[1]]]]})
    assert json.loads(run("schema", stdin=deep)[1])["properties"]["a"]["items"]["items"]


def test_split_by_lines(run, tmp_path, chat):
    out_dir = tmp_path / "parts"
    code, out, _ = run("split", "-l", "2", "--prefix", "p", "--out-dir", str(out_dir), stdin=chat)
    names = sorted(p.name for p in out_dir.iterdir())
    assert code == 0 and names == ["p-0001.jsonl", "p-0002.jsonl", "p-0003.jsonl"]
    assert len(out.splitlines()) == 3
    joined = b"".join((out_dir / n).read_bytes() for n in names)
    assert joined == chat
    assert (out_dir / "p-0003.jsonl").read_bytes().count(b"\n") == 1


def test_split_by_size_never_cuts_lines(run, tmp_path, write):
    src = write("big.jsonl", jl(*({"i": i, "pad": "x" * 40} for i in range(30))))
    out_dir = tmp_path / "o"
    code, _, _ = run("split", "-s", "300", "--out-dir", str(out_dir), str(src))
    parts = sorted(out_dir.iterdir())
    assert code == 0 and len(parts) > 3 and parts[0].name == "big-0001.jsonl"
    for part in parts:
        data = part.read_bytes()
        assert len(data) <= 300 and data.endswith(b"\n")
        for ln in data.splitlines():
            json.loads(ln)
    assert sum(p.read_bytes().count(b"\n") for p in parts) == 30


def test_split_oversized_line_gets_own_part(run, tmp_path):
    data = jl({"a": "x" * 100}, {"a": 1})
    out_dir = tmp_path / "o"
    run("split", "-s", "10", "--out-dir", str(out_dir), stdin=data)
    assert len(list(out_dir.iterdir())) == 2


def test_split_gzip_and_prefix_rules(run, tmp_path, write, chat):
    src = write("data.jsonl.gz", chat)
    out_dir = tmp_path / "o"
    run("split", "-l", "3", "--gzip", "--out-dir", str(out_dir), str(src))
    names = sorted(p.name for p in out_dir.iterdir())
    assert names == ["data-0001.jsonl.gz", "data-0002.jsonl.gz"]
    assert gzip.decompress((out_dir / names[0]).read_bytes()).count(b"\n") == 3
    run("split", "-l", "3", "--out-dir", str(out_dir), stdin=chat)
    assert (out_dir / "part-0001.jsonl").exists()


@pytest.mark.parametrize(
    "argv",
    [("split",), ("split", "-l", "1", "-s", "1k"), ("split", "-l", "0"), ("split", "-s", "abc")],
)
def test_split_argument_errors(run, argv):
    assert run(*argv, stdin=b"{}\n")[0] == 2


def test_cli_misc(run, capsys):
    with pytest.raises(SystemExit) as exc:
        run("--version")
    assert exc.value.code == 0
    assert "jsonl-tools 0.1.0" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run("head", "-n", "x")
    with pytest.raises(SystemExit):
        run()


def test_example_files_work_end_to_end(run):
    root = Path(__file__).resolve().parent.parent / "examples"
    code, out, _ = run("filter", str(root / "app.log.jsonl"), "level==ERROR", "-c")
    assert code == 0 and int(out) >= 1
    code, out, _ = run("validate", str(root / "chat.jsonl"))
    assert code == 0
