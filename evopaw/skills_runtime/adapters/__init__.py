"""skills_runtime adapters —— 把 SkillDispatcher 包装为各 backend 协议族需要的形态。

- `claude_mcp.build_skill_loader_server(...)`：包成 Claude Agent SDK MCP server，
  保留 P2 之前 `tools/skill_loader.py` 的对外接口。
- `openai_tools.build_openai_tool_schema(dispatcher)`：返回 OpenAI function tool
  schema 字典（仅描述 + 参数；OpenAIChatBackend 自己 await dispatcher.dispatch）。
"""
