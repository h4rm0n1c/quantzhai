#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


def function_tool(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
    }


class ToolAdapter(Protocol):
    upstream_name: str

    def accepts_tool(self, tool: dict) -> bool:
        ...

    def to_upstream_tool(self, tool: dict) -> dict:
        ...

    def normalize_tool_choice(self, tool_choice: dict):
        ...

    def input_to_upstream(self, item: dict):
        ...

    def output_to_codex(self, item: dict, output_style: str = "native"):
        ...


@dataclass(frozen=True)
class ToolRegistry:
    adapters: tuple[ToolAdapter, ...]

    def adapter_for_tool(self, tool: dict):
        for adapter in self.adapters:
            if adapter.accepts_tool(tool):
                return adapter
        return None

    def normalize_tool_choice(self, tool_choice: dict):
        for adapter in self.adapters:
            normalized = adapter.normalize_tool_choice(tool_choice)
            if normalized is not None:
                return normalized
        return None

    def input_to_upstream(self, item: dict):
        for adapter in self.adapters:
            normalized = adapter.input_to_upstream(item)
            if normalized is not None:
                return normalized
        return None

    def output_to_codex(self, item: dict, output_style: str = "native"):
        for adapter in self.adapters:
            normalized = adapter.output_to_codex(item, output_style)
            if normalized is not None:
                return normalized
        return None
