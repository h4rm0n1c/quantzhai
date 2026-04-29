#!/usr/bin/env python3

try:
    from .qz_tools import function_tool
except ImportError:
    from qz_tools import function_tool


class WebSearchToolAdapter:
    upstream_name = "web_search"

    def accepts_tool(self, tool: dict) -> bool:
        return isinstance(tool, dict) and tool.get("type") == "web_search"

    def to_upstream_tool(self, tool: dict) -> dict:
        return function_tool(
            "web_search",
            "Search the web, open a page, or find text in an opened page using the local web runtime.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "open_page", "find_in_page"],
                        "description": "The web action to perform.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for search, or needle text for find_in_page.",
                    },
                    "profile": {
                        "type": "string",
                        "enum": ["auto", "broad", "coding", "research", "news", "ai_models", "reference", "sysadmin"],
                        "description": "Search profile used to select SearXNG categories and engines.",
                    },
                    "url": {
                        "type": "string",
                        "description": "Page URL for open_page or find_in_page.",
                    },
                    "page_id": {
                        "type": "string",
                        "description": "Previously opened page identifier for find_in_page.",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional SearXNG categories to use for search.",
                    },
                    "engines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional SearXNG engines to use for search.",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                        "description": "Optional maximum number of search results to return.",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        )

    def normalize_tool_choice(self, tool_choice: dict):
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "web_search":
            return {"type": "function", "name": "web_search"}
        return None

    def input_to_upstream(self, item: dict):
        return None

    def output_to_codex(self, item: dict, output_style: str = "native"):
        return None


WEB_SEARCH_TOOL_ADAPTER = WebSearchToolAdapter()
