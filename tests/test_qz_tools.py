import unittest

from proxy.qz_tool_apply_patch import APPLY_PATCH_TOOL_ADAPTER
from proxy.qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
from proxy.qz_tools import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_registry_adapts_apply_patch_tool_and_choice(self):
        registry = ToolRegistry((APPLY_PATCH_TOOL_ADAPTER,))
        tool = {"type": "apply_patch"}

        adapted = registry.adapter_for_tool(tool).to_upstream_tool(tool)
        choice = registry.normalize_tool_choice({"type": "apply_patch"})

        self.assertEqual(adapted["type"], "function")
        self.assertEqual(adapted["name"], "apply_patch")
        self.assertEqual(choice, {"type": "function", "name": "apply_patch"})

    def test_registry_adapts_web_search_tool_and_choice(self):
        registry = ToolRegistry((APPLY_PATCH_TOOL_ADAPTER, WEB_SEARCH_TOOL_ADAPTER))
        tool = {"type": "web_search"}

        adapted = registry.adapter_for_tool(tool).to_upstream_tool(tool)
        choice = registry.normalize_tool_choice({"type": "web_search"})

        self.assertEqual(adapted["type"], "function")
        self.assertEqual(adapted["name"], "web_search")
        self.assertEqual(choice, {"type": "function", "name": "web_search"})


if __name__ == "__main__":
    unittest.main()
