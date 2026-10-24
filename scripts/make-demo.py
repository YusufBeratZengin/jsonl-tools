"""Generate docs/demo.svg and docs/demo.txt from real, in-process CLI output."""

from __future__ import annotations

import argparse
import html
import io
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = [
    ["count", "examples/chat.jsonl"],
    ["filter", "examples/app.log.jsonl", "level==ERROR", "--count"],
    ["select", "examples/chat.jsonl", "id", "tokens=usage.tokens"],
    ["validate", "examples/chat.jsonl"],
]


def transcript() -> str:
    from jsonl_tools.cli import main

    lines = []
    for args in COMMANDS:
        out, err = io.BytesIO(), io.StringIO()
        # Resolve input without embedding local paths in generated artifacts.
        actual = [str(ROOT / arg) if arg.startswith("examples/") else arg for arg in args]
        code = main(actual, stdin=io.BytesIO(), stdout=out, stderr=err)
        if code or err.getvalue():
            raise RuntimeError(f"demo failed: {args}: {err.getvalue()}")
        lines.append("$ jsonl-tools " + shlex.join(args))
        lines.extend(out.getvalue().decode("utf-8").rstrip().splitlines())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def svg(text: str) -> str:
    lines = text.splitlines()
    width = max(820, max(map(len, lines)) * 9 + 48)
    height = 72 + len(lines) * 23
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        '<title id="title">jsonl-tools terminal demo</title>',
        '<desc id="description">Actual output: count records, filter errors, '
        'select fields and validate JSONL.</desc>',
        f'<rect width="{width}" height="{height}" rx="12" fill="#101827"/>',
        '<text x="24" y="30" fill="#94a3b8" font-family="monospace" '
        'font-size="14">jsonl-tools | Python 3.10+ | zero runtime dependencies</text>',
    ]
    for i, line in enumerate(lines):
        color = "#67e8f9" if line.startswith("$") else "#e2e8f0"
        elements.append(
            f'<text x="24" y="{64 + i * 23}" fill="{color}" '
            f'font-family="monospace" font-size="14" xml:space="preserve">'
            f"{html.escape(line)}</text>"
        )
    return "\n".join([*elements, "</svg>", ""])


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if demo artifacts are stale")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    text = transcript()
    for name, content in {"demo.txt": text, "demo.svg": svg(text)}.items():
        path = ROOT / "docs" / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"stale demo: {path.name}; run python scripts/make-demo.py")
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
    print("demo verified" if args.check else "demo generated")


if __name__ == "__main__":
    run()
