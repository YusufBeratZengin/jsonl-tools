"""One-pass field statistics with bounded memory."""

from __future__ import annotations

import json
import math
import random
from typing import Any

RESERVOIR_SIZE = 4096
TOP_CAPACITY = 1000
MAX_TRACKED_STRING = 100


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile (``q`` in 0..1) of an already sorted list."""
    if not sorted_values:
        raise ValueError("no values")
    k = (len(sorted_values) - 1) * q
    lo = math.floor(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    weight = k - lo
    return sorted_values[lo] * (1 - weight) + sorted_values[hi] * weight


class FrequentValues:
    """Misra-Gries summary: exact while there are at most ``capacity`` distinct values,
    otherwise counts are lower bounds and the heavy hitters are still found."""

    def __init__(self, capacity: int = TOP_CAPACITY) -> None:
        self.capacity = capacity
        self.counts: dict[str, int] = {}
        self.exact = True

    def add(self, key: str) -> None:
        counts = self.counts
        if key in counts:
            counts[key] += 1
        elif len(counts) < self.capacity:
            counts[key] = 1
        else:
            self.exact = False
            dead = []
            for k in counts:
                counts[k] -= 1
                if counts[k] == 0:
                    dead.append(k)
            for k in dead:
                del counts[k]

    def top(self, n: int) -> list[tuple[str, int]]:
        return sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


class PathStats:
    """Everything we know about one field path."""

    def __init__(self) -> None:
        self.present = 0  # records containing the path at least once
        self.occurrences = 0  # total occurrences (arrays can repeat a path)
        self.last_record = 0
        self.types: dict[str, int] = {}
        self.num_count = 0
        self.num_min = math.inf
        self.num_max = -math.inf
        self.num_mean = 0.0
        self.sample: list[float] = []
        self.str_count = 0
        self.str_min = 0
        self.str_max = 0
        self.str_sum = 0
        self.top = FrequentValues()

    def observe(self, value: Any, record: int, rng: random.Random) -> None:
        self.occurrences += 1
        if self.last_record != record:
            self.last_record = record
            self.present += 1
        t = type_name(value)
        self.types[t] = self.types.get(t, 0) + 1
        if t == "integer" or t == "number":
            try:
                x = float(value)
            except OverflowError:
                x = math.inf
            if math.isfinite(x):
                self.num_count += 1
                self.num_mean = (
                    self.num_mean * ((self.num_count - 1) / self.num_count) + x / self.num_count
                )
                self.num_min = min(self.num_min, x)
                self.num_max = max(self.num_max, x)
                if len(self.sample) < RESERVOIR_SIZE:
                    self.sample.append(x)
                else:
                    j = rng.randrange(self.num_count)
                    if j < RESERVOIR_SIZE:
                        self.sample[j] = x
            if t == "integer":
                self.top.add(json.dumps(value))
        elif t == "string":
            n = len(value)
            if self.str_count == 0:
                self.str_min = self.str_max = n
            else:
                self.str_min = min(self.str_min, n)
                self.str_max = max(self.str_max, n)
            self.str_count += 1
            self.str_sum += n
            if n <= MAX_TRACKED_STRING:
                self.top.add(json.dumps(value, ensure_ascii=False))
        elif t == "boolean" or t == "null":
            self.top.add(json.dumps(value))

    def to_dict(self, records: int, top: int) -> dict[str, Any]:
        out: dict[str, Any] = {
            "present": self.present,
            "present_ratio": round(self.present / records, 6) if records else 0.0,
            "occurrences": self.occurrences,
            "types": dict(sorted(self.types.items(), key=lambda kv: -kv[1])),
        }
        if self.num_count:
            ordered = sorted(self.sample)
            out["numeric"] = {
                "count": self.num_count,
                "min": _tidy(self.num_min),
                "max": _tidy(self.num_max),
                "mean": _tidy(self.num_mean),
                "p50": _tidy(percentile(ordered, 0.50)),
                "p95": _tidy(percentile(ordered, 0.95)),
                "percentiles_exact": self.num_count <= RESERVOIR_SIZE,
            }
        if self.str_count:
            out["string_length"] = {
                "min": self.str_min,
                "max": self.str_max,
                "mean": round(self.str_sum / self.str_count, 2),
            }
        if top > 0 and self.top.counts:
            out["top_values"] = [{"value": json.loads(k), "count": c} for k, c in self.top.top(top)]
            out["top_values_exact"] = self.top.exact
        return out


def _tidy(x: float) -> int | float:
    if x == int(x) and abs(x) < 1e15:
        return int(x)
    return float(f"{x:.6g}")


class Stats:
    """Accumulate field statistics over parsed records.

    Paths use dots for keys and ``[]`` for list elements (``messages[].role``). The number of
    tracked paths and the nesting depth are capped, so memory stays bounded for any input.
    """

    def __init__(self, *, max_paths: int = 500, max_depth: int = 12, seed: int = 0) -> None:
        self.records = 0
        self.max_paths = max_paths
        self.max_depth = max_depth
        self.paths: dict[str, PathStats] = {}
        self.truncated = False
        self._rng = random.Random(seed)

    def add(self, value: Any) -> None:
        self.records += 1
        self._walk(value, "", 0)

    def _walk(self, value: Any, path: str, depth: int) -> None:
        ps = self.paths.get(path)
        if ps is None:
            if len(self.paths) >= self.max_paths:
                self.truncated = True
                return
            ps = self.paths[path] = PathStats()
        ps.observe(value, self.records, self._rng)
        if depth >= self.max_depth:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                self._walk(child, f"{path}.{key}" if path else key, depth + 1)
        elif isinstance(value, list):
            child_path = f"{path}[]"
            for child in value:
                self._walk(child, child_path, depth + 1)

    def result(self, *, fields: list[str] | None = None, top: int = 5) -> dict[str, Any]:
        paths: dict[str, Any] = {}
        for path, ps in self.paths.items():
            shown = path or "$"
            if path == "" and set(ps.types) == {"object"}:
                continue  # the root object is implied
            if fields and not any(_under(shown, f) for f in fields):
                continue
            paths[shown] = ps.to_dict(self.records, top)
        return {"records": self.records, "paths_truncated": self.truncated, "paths": paths}


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith((prefix + ".", prefix + "["))


def render_text(result: dict[str, Any]) -> str:
    """Human-readable report for :meth:`Stats.result` output."""
    paths: dict[str, Any] = result["paths"]
    lines = [f"records: {result['records']:,}"]
    if result["paths_truncated"]:
        lines.append("note: path limit reached, rarer fields are not shown (raise --max-paths)")
    if not paths:
        return "\n".join(lines) + "\n"

    width = max(len("field"), *(len(p) for p in paths))
    lines += ["", f"{'field'.ljust(width)}  {'present':>8}  types"]
    for path, info in paths.items():
        total = sum(info["types"].values())
        types = ", ".join(f"{t} {100 * c / total:.0f}%" for t, c in info["types"].items())
        ratio = f"{100 * info['present_ratio']:.1f}%"
        lines.append(f"{path.ljust(width)}  {ratio:>8}  {types}")

    numeric = {p: i["numeric"] for p, i in paths.items() if "numeric" in i}
    if numeric:
        lines += [
            "",
            f"{'numeric'.ljust(width)}  {'min':>10}  {'max':>10}  {'mean':>12}  "
            f"{'p50':>10}  {'p95':>10}",
        ]
        for path, n in numeric.items():
            approx = "" if n["percentiles_exact"] else "  (p50/p95 approximate)"
            lines.append(
                f"{path.ljust(width)}  {n['min']:>10}  {n['max']:>10}  {n['mean']:>12}  "
                f"{n['p50']:>10}  {n['p95']:>10}{approx}"
            )

    repeated = {
        p: i["top_values"]
        for p, i in paths.items()
        if i.get("top_values") and i["top_values"][0]["count"] > 1
    }
    if repeated:
        lines += ["", "most frequent values (fields with repeats)"]
        for path, tops in repeated.items():
            shown = ", ".join(
                f"{json.dumps(t['value'], ensure_ascii=False)} ({t['count']})" for t in tops
            )
            lines.append(f"  {path}: {shown}")
    return "\n".join(lines) + "\n"
