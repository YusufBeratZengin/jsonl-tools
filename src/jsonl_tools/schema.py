"""Streaming JSON Schema inference. Memory is bounded by the size of the schema, not the data."""

from __future__ import annotations

from typing import Any

from .stats import type_name

DRAFT = "https://json-schema.org/draft/2020-12/schema"


class _Node:
    __slots__ = ("count", "items", "obj_count", "overflow", "props", "types")

    def __init__(self) -> None:
        self.count = 0
        self.types: set[str] = set()
        self.props: dict[str, _Node] = {}
        self.obj_count = 0
        self.overflow = False
        self.items: _Node | None = None


class SchemaInferrer:
    """Merge many JSON values into a single JSON Schema.

    ``required`` lists properties present in every observed object. Objects with more than
    ``max_props`` distinct keys (dynamic maps) stop growing and get ``additionalProperties``.
    """

    def __init__(self, *, max_props: int = 200, max_depth: int = 32) -> None:
        self.max_props = max_props
        self.max_depth = max_depth
        self.root = _Node()

    def add(self, value: Any) -> None:
        self._add(self.root, value, 0)

    def _add(self, node: _Node, value: Any, depth: int) -> None:
        node.count += 1
        t = type_name(value)
        node.types.add(t)
        if depth >= self.max_depth:
            return
        if t == "object":
            node.obj_count += 1
            for key, child_value in value.items():
                child = node.props.get(key)
                if child is None:
                    if len(node.props) >= self.max_props:
                        node.overflow = True
                        continue
                    child = node.props[key] = _Node()
                self._add(child, child_value, depth + 1)
        elif t == "array":
            if node.items is None:
                node.items = _Node()
            for element in value:
                self._add(node.items, element, depth + 1)

    def schema(self, *, require: bool = True) -> dict[str, Any]:
        out = {"$schema": DRAFT}
        out.update(self._render(self.root, require))
        return out

    def _render(self, node: _Node, require: bool) -> dict[str, Any]:
        types = set(node.types)
        if "number" in types:
            types.discard("integer")
        out: dict[str, Any] = {}
        ordered = sorted(types)
        if ordered:
            out["type"] = ordered[0] if len(ordered) == 1 else ordered
        if "object" in node.types:
            out["properties"] = {k: self._render(v, require) for k, v in node.props.items()}
            if require:
                required = [k for k, v in node.props.items() if v.count == node.obj_count]
                if required:
                    out["required"] = required
            if node.overflow:
                out["additionalProperties"] = True
        if "array" in node.types and node.items is not None and node.items.count:
            out["items"] = self._render(node.items, require)
        return out
