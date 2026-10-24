"""Command-line interface: ``jsonl-tools <command> [options] [FILE]``.

``FILE`` may be a plain ``.jsonl`` file, a gzip file (detected by content) or ``-`` for stdin.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import os
import random
import sys
import zlib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from . import __version__, ops
from ._io import (
    JsonlError,
    iter_lines,
    iter_records,
    open_input,
    open_output,
    parse_line,
    write_line,
)
from .expr import ExprError, Predicate, compile_expr
from .paths import PathError, parse_path
from .schema import SchemaInferrer
from .stats import Stats, render_text


class _Ctx:
    """Streams and bookkeeping for one invocation (injectable for tests)."""

    def __init__(self, stdin: BinaryIO, stdout: BinaryIO, stderr: TextIO) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.skipped: list[int] = []
        self.skipped_count = 0

    def note_bad(self, lineno: int, _message: str) -> None:
        self.skipped_count += 1
        if len(self.skipped) < 5:
            self.skipped.append(lineno)

    def records(self, f: BinaryIO, args: argparse.Namespace) -> Iterator[tuple[int, bytes, Any]]:
        return iter_records(
            iter_lines(f), skip_bad=getattr(args, "skip_bad", False), on_bad=self.note_bad
        )

    def text(self, s: str) -> None:
        self.stdout.write(s.encode("utf-8"))

    def report_skipped(self) -> None:
        if self.skipped:
            first = ", ".join(str(n) for n in self.skipped[:5])
            more = ", ..." if self.skipped_count > 5 else ""
            print(
                f"jsonl-tools: skipped {self.skipped_count} bad line(s) (first: {first}{more})",
                file=self.stderr,
            )


# --- command implementations -------------------------------------------------------------------


def _cmd_head(ctx: _Ctx, args: argparse.Namespace) -> int:
    with open_input(args.file, ctx.stdin) as f, open_output(args.output, ctx.stdout) as out:
        ops.passthrough(ops.head(iter_lines(f), args.n), out)
    return 0


def _cmd_tail(ctx: _Ctx, args: argparse.Namespace) -> int:
    with open_output(args.output, ctx.stdout) as out:
        if args.file not in (None, "-") and _is_plain_file(args.file):
            with open(args.file, "rb") as f:
                lines = ops.tail_file(f, args.n)
        else:
            with open_input(args.file, ctx.stdin) as f:
                lines = ops.tail(iter_lines(f), args.n)
        ops.passthrough(lines, out)
    return 0


def _is_plain_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) != b"\x1f\x8b" and f.seekable()
    except OSError:
        return False


def _cmd_count(ctx: _Ctx, args: argparse.Namespace) -> int:
    with open_input(args.file, ctx.stdin) as f:
        ctx.text(f"{ops.count(iter_lines(f))}\n")
    return 0


def _cmd_filter(ctx: _Ctx, args: argparse.Namespace) -> int:
    main_pred = compile_expr(args.expr)
    or_preds = [compile_expr(e) for e in args.or_]
    and_preds = [compile_expr(e) for e in args.and_]
    any_of: list[Predicate] = [main_pred, *or_preds]

    def predicate(value: Any) -> bool:
        return any(p(value) for p in any_of) and all(p(value) for p in and_preds)

    with open_input(args.file, ctx.stdin) as f:
        matches = ops.filter_records(ctx.records(f, args), predicate, invert=args.invert)
        if args.count:
            ctx.text(f"{sum(1 for _ in matches)}\n")
        else:
            with open_output(args.output, ctx.stdout) as out:
                ops.passthrough(matches, out)
    ctx.report_skipped()
    return 0


def _cmd_select(ctx: _Ctx, args: argparse.Namespace) -> int:
    fields = []
    for spec in args.fields:
        alias, text = ops.parse_select_spec(spec)
        fields.append((alias, text, parse_path(text)))
    with open_input(args.file, ctx.stdin) as f, open_output(args.output, ctx.stdout) as out:
        rows = ops.select_records(
            ctx.records(f, args), fields, flat=args.flat, null_missing=args.null_missing
        )
        ops.write_objects(rows, out)
    ctx.report_skipped()
    return 0


def _cmd_stats(ctx: _Ctx, args: argparse.Namespace) -> int:
    stats = Stats(max_paths=args.max_paths, max_depth=args.max_depth)
    with open_input(args.file, ctx.stdin) as f:
        for _, _, value in ctx.records(f, args):
            stats.add(value)
    result = stats.result(fields=args.field or None, top=args.top)
    if args.json:
        ctx.text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    else:
        ctx.text(render_text(result))
    ctx.report_skipped()
    return 0


def _cmd_schema(ctx: _Ctx, args: argparse.Namespace) -> int:
    inferrer = SchemaInferrer(max_props=args.max_props)
    with open_input(args.file, ctx.stdin) as f:
        for _, _, value in ctx.records(f, args):
            inferrer.add(value)
    schema = inferrer.schema(require=not args.no_required)
    ctx.text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n")
    ctx.report_skipped()
    return 0


def _cmd_split(ctx: _Ctx, args: argparse.Namespace) -> int:
    if (args.lines is None) == (args.size is None):
        raise JsonlError("give exactly one of --lines or --size")
    max_bytes = ops.parse_size(args.size) if args.size is not None else None
    if args.lines is not None and args.lines <= 0:
        raise JsonlError("--lines must be positive")
    prefix = args.prefix or _default_prefix(args.file)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jsonl.gz" if args.gzip else ".jsonl"
    created: list[str] = []

    def open_part(i: int) -> BinaryIO:
        path = out_dir / f"{prefix}-{i:04d}{suffix}"
        created.append(path.as_posix())
        if path.exists() and args.file != "-" and path.samefile(args.file):
            raise JsonlError("split output would overwrite the input")
        if args.gzip:
            return gzip.GzipFile(filename=path, mode="wb", mtime=0)  # type: ignore[return-value]
        return open(path, "wb")  # closed by split_lines

    with open_input(args.file, ctx.stdin) as f:
        ops.split_lines(iter_lines(f), open_part, max_lines=args.lines, max_bytes=max_bytes)
    ctx.text("".join(p + "\n" for p in created))
    return 0


def _default_prefix(file: str) -> str:
    if file == "-":
        return "part"
    name = Path(file).name
    for ext in (".jsonl.gz", ".ndjson.gz", ".jsonl", ".ndjson", ".gz"):
        if name.endswith(ext):
            return name[: -len(ext)] or "part"
    return Path(name).stem or "part"


def _cmd_sample(ctx: _Ctx, args: argparse.Namespace) -> int:
    if (args.n is None) == (args.fraction is None):
        raise JsonlError("give exactly one of -n or --fraction")
    if args.fraction is not None and not 0 <= args.fraction <= 1:
        raise JsonlError("--fraction must be between 0 and 1")
    rng = random.Random(args.seed)
    with open_input(args.file, ctx.stdin) as f, open_output(args.output, ctx.stdout) as out:
        lines = iter_lines(f)
        if args.n is not None:
            ops.passthrough(ops.sample_reservoir(lines, args.n, rng), out)
        else:
            ops.passthrough(ops.sample_fraction(lines, args.fraction, rng), out)
    return 0


def _cmd_dedupe(ctx: _Ctx, args: argparse.Namespace) -> int:
    seen = [0]
    kept = 0
    by = [p for item in (args.by or []) for p in item.split(",") if p]
    with open_input(args.file, ctx.stdin) as f, open_output(args.output, ctx.stdout) as out:
        stream: Iterator[bytes]
        if by:
            paths = [parse_path(p) for p in by]
            stream = ops.dedupe_by(_tick(ctx.records(f, args), seen), paths)
        else:
            stream = ops.dedupe_lines(_tick(iter_lines(f), seen), canonical=args.canonical)
        for raw in stream:
            write_line(out, raw)
            kept += 1
    ctx.report_skipped()
    if args.verbose:
        print(f"jsonl-tools: kept {kept} of {seen[0]} line(s)", file=ctx.stderr)
    return 0


def _tick(it: Iterator[Any], counter: list[int]) -> Iterator[Any]:
    for item in it:
        counter[0] += 1
        yield item


def _cmd_validate(ctx: _Ctx, args: argparse.Namespace) -> int:
    total = 0
    bad = 0
    shown = 0
    problems: list[dict[str, Any]] = []
    clean_cm = open_output(args.clean, ctx.stdout) if args.clean else None
    clean: BinaryIO | None = clean_cm.__enter__() if clean_cm else None
    try:
        with open_input(args.file, ctx.stdin) as f:
            for n, raw in iter_lines(f):
                total += 1
                message: str | None = None
                try:
                    value = parse_line(raw)
                    if args.object and not isinstance(value, dict):
                        message = "not a JSON object"
                except ValueError as exc:
                    message = str(exc)
                if message is None:
                    if clean is not None:
                        write_line(clean, raw)
                    continue
                bad += 1
                if args.limit == 0 or shown < args.limit:
                    shown += 1
                    problems.append({"line": n, "error": message})
                    if not args.json and not args.quiet:
                        ctx.text(f"line {n}: {message}\n")
    finally:
        if clean_cm is not None:
            clean_cm.__exit__(None, None, None)
    summary = {"lines": total, "valid": total - bad, "invalid": bad}
    if args.json:
        ctx.text(json.dumps({**summary, "problems": problems}, ensure_ascii=False) + "\n")
    elif not args.quiet:
        hidden = bad - shown
        if hidden > 0:
            ctx.text(f"... and {hidden} more (use --limit 0 to list all)\n")
        ctx.text(f"{total} line(s): {total - bad} valid, {bad} invalid\n")
    return 1 if bad else 0


# --- parser ------------------------------------------------------------------------------------


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer {text!r}") from None


def _file_arg(p: argparse.ArgumentParser, *, required: bool = False) -> None:
    if required:
        p.add_argument(
            "file", metavar="FILE", help="input file (.jsonl or .jsonl.gz), or - for stdin"
        )
    else:
        p.add_argument(
            "file",
            nargs="?",
            default="-",
            metavar="FILE",
            help="input file (.jsonl or .jsonl.gz); default: stdin",
        )


def _out_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "-o", "--output", metavar="FILE", help="write here instead of stdout (.gz compresses)"
    )


def _bad_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--skip-bad", action="store_true", help="skip invalid lines instead of failing")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jsonl-tools",
        description="Streaming toolkit for large JSONL and .jsonl.gz files.",
        epilog="Field paths: a.b, a[0].b, a[].b ([] = every list element). Exit codes: "
        "0 ok, 1 validate found invalid lines, 2 error.",
    )
    parser.add_argument("--version", action="version", version=f"jsonl-tools {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, help_: str, func: Callable[..., int]) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_, description=help_)
        p.set_defaults(func=func)
        return p

    p = add("head", "print the first N lines", _cmd_head)
    _file_arg(p)
    p.add_argument("-n", type=_int, default=10, help="number of lines (default 10)")
    _out_arg(p)

    p = add("tail", "print the last N lines", _cmd_tail)
    _file_arg(p)
    p.add_argument("-n", type=_int, default=10, help="number of lines (default 10)")
    _out_arg(p)

    p = add("count", "count non-blank lines", _cmd_count)
    _file_arg(p)

    p = add("filter", "keep lines matching an expression", _cmd_filter)
    _file_arg(p, required=True)
    p.add_argument(
        "expr", metavar="EXPR", help="e.g. 'usage.tokens>1000', 'role==user', 'text~error'"
    )
    p.add_argument(
        "--and",
        dest="and_",
        action="append",
        default=[],
        metavar="EXPR",
        help="additional expression that must also match (repeatable)",
    )
    p.add_argument(
        "--or",
        dest="or_",
        action="append",
        default=[],
        metavar="EXPR",
        help="alternative expression; any of EXPR/--or may match (repeatable)",
    )
    p.add_argument("-v", "--invert", action="store_true", help="keep non-matching lines")
    p.add_argument("-c", "--count", action="store_true", help="print only the number of matches")
    _bad_arg(p)
    _out_arg(p)

    p = add("select", "pick and rename fields", _cmd_select)
    _file_arg(p, required=True)
    p.add_argument(
        "fields",
        nargs="+",
        metavar="FIELD",
        help="path or name=path, e.g. id usage.tokens t=usage.tokens",
    )
    p.add_argument("--flat", action="store_true", help="use the path text as a flat key")
    p.add_argument("--null-missing", action="store_true", help="emit null for missing fields")
    _bad_arg(p)
    _out_arg(p)

    p = add("stats", "field presence, types, numeric summary, top values", _cmd_stats)
    _file_arg(p)
    p.add_argument(
        "--field",
        action="append",
        metavar="PATH",
        help="only this path and its children (repeatable)",
    )
    p.add_argument(
        "--top", type=_int, default=5, help="most frequent values per field (default 5, 0 = off)"
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--max-paths",
        type=_int,
        default=500,
        help="memory cap: distinct field paths tracked (default 500)",
    )
    p.add_argument(
        "--max-depth", type=_int, default=12, help="deepest nesting level analyzed (default 12)"
    )
    _bad_arg(p)

    p = add("schema", "infer a JSON Schema", _cmd_schema)
    _file_arg(p)
    p.add_argument("--no-required", action="store_true", help="do not emit required lists")
    p.add_argument(
        "--max-props",
        type=_int,
        default=200,
        help="distinct keys per object before treating it as a map (default 200)",
    )
    _bad_arg(p)

    p = add("split", "split into numbered parts by line count or size", _cmd_split)
    _file_arg(p)
    p.add_argument("-l", "--lines", type=_int, metavar="N", help="lines per part")
    p.add_argument("-s", "--size", metavar="SIZE", help="max bytes per part, e.g. 64MB (1k = 1024)")
    p.add_argument("--prefix", help="part name prefix (default: input name)")
    p.add_argument(
        "--out-dir", default=".", metavar="DIR", help="directory for the parts (default .)"
    )
    p.add_argument("--gzip", action="store_true", help="write .jsonl.gz parts")

    p = add("sample", "reproducible random sample", _cmd_sample)
    _file_arg(p)
    p.add_argument("-n", type=_int, help="exact number of lines (reservoir sampling)")
    p.add_argument("-p", "--fraction", type=float, help="keep each line with this probability")
    p.add_argument("--seed", type=_int, default=0, help="random seed (default 0)")
    _out_arg(p)

    p = add("dedupe", "drop duplicate lines or duplicate keys", _cmd_dedupe)
    _file_arg(p)
    p.add_argument(
        "--by",
        action="append",
        metavar="PATH",
        help="compare only these fields (repeatable or comma-separated)",
    )
    p.add_argument(
        "--canonical",
        action="store_true",
        help="ignore key order and whitespace when comparing whole lines",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="print a summary to stderr")
    _bad_arg(p)
    _out_arg(p)

    p = add("validate", "report invalid lines with line numbers", _cmd_validate)
    _file_arg(p)
    p.add_argument("--object", action="store_true", help="require every line to be a JSON object")
    p.add_argument(
        "--limit", type=_int, default=20, help="max problems to list (default 20, 0 = all)"
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("-q", "--quiet", action="store_true", help="no output, only the exit code")
    p.add_argument("--clean", metavar="FILE", help="write the valid lines to FILE")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return the exit code (0 ok, 1 validate failed, 2 error)."""
    ctx = _Ctx(stdin or sys.stdin.buffer, stdout or sys.stdout.buffer, stderr or sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        _check_outputs(args)
        code: int = args.func(ctx, args)
        ctx.stdout.flush()
        return code
    except BrokenPipeError:
        raise
    except (JsonlError, ExprError, PathError) as exc:
        print(f"jsonl-tools: error: {exc}", file=ctx.stderr)
    except (OSError, EOFError, zlib.error) as exc:
        print(f"jsonl-tools: error: {exc}", file=ctx.stderr)
    return 2


def _check_outputs(args: argparse.Namespace) -> None:
    """Reject destructive aliases and invalid limits before opening output files."""
    source = Path(args.file) if args.file != "-" else None
    for name in ("output", "clean"):
        target = getattr(args, name, None)
        if source is not None and target is not None and target != "-":
            output = Path(target)
            if output.resolve() == source.resolve() or (
                output.exists() and source.exists() and output.samefile(source)
            ):
                raise JsonlError("input and output must be different files")
    for name in ("n", "top", "max_depth", "limit"):
        value = getattr(args, name, None)
        if value is not None and value < 0:
            raise JsonlError(f"{name.replace('_', '-')} must be non-negative")
    for name in ("max_paths", "max_props"):
        value = getattr(args, name, None)
        if value is not None and value <= 0:
            raise JsonlError(f"{name.replace('_', '-')} must be positive")
    prefix = getattr(args, "prefix", None)
    if prefix is not None and (
        prefix in ("", ".", "..") or any(char in prefix for char in "/\\:")
    ):
        raise JsonlError("--prefix must be a file name, without directory separators")


def run() -> None:  # pragma: no cover - thin process wrapper
    """Console-script entry point (quiet exit when the reader closes the pipe)."""
    try:
        code = main()
        sys.stdout.flush()
    except BrokenPipeError:
        code = 0
        with contextlib.suppress(OSError, ValueError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


__all__ = ["build_parser", "main", "run"]
