"""IntermediateTool 单元测试"""

from __future__ import annotations

import json

import pytest

from xiaopaw.tools.intermediate_tool import IntermediateTool, IntermediateToolSchema


class TestIntermediateToolSchema:
    def test_string_passthrough(self):
        s = IntermediateToolSchema(intermediate_product="hello")
        assert s.intermediate_product == "hello"

    def test_empty_string(self):
        s = IntermediateToolSchema(intermediate_product="")
        assert s.intermediate_product == ""

    def test_list_joined_with_newlines(self):
        s = IntermediateToolSchema(intermediate_product=["a", "b", "c"])
        assert s.intermediate_product == "a\nb\nc"

    def test_list_with_non_strings(self):
        s = IntermediateToolSchema(intermediate_product=[1, 2, 3])
        assert s.intermediate_product == "1\n2\n3"

    def test_dict_to_json(self):
        s = IntermediateToolSchema(intermediate_product={"key": "value"})
        parsed = json.loads(s.intermediate_product)
        assert parsed["key"] == "value"

    def test_int_to_str(self):
        s = IntermediateToolSchema(intermediate_product=42)
        assert s.intermediate_product == "42"

    def test_none_to_str(self):
        s = IntermediateToolSchema(intermediate_product=None)
        assert s.intermediate_product == "None"

    def test_nested_dict(self):
        s = IntermediateToolSchema(intermediate_product={"a": [1, 2]})
        assert '"a"' in s.intermediate_product


class TestIntermediateTool:
    def test_run_returns_confirmation_message(self):
        tool = IntermediateTool()
        result = tool._run("some intermediate result")
        assert "已保存" in result

    def test_run_with_empty_string(self):
        tool = IntermediateTool()
        result = tool._run("")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_tool_name(self):
        tool = IntermediateTool()
        assert tool.name == "Save_Intermediate_Product_Tool"

    def test_tool_description_mentions_intermediate(self):
        tool = IntermediateTool()
        assert "中间" in tool.description
