"""BaiduSearchTool — 百度千帆搜索 API 的 CrewAI 工具封装（按 XiaoPaw 标准整理）。

💡【第12课·工具设计哲学】本工具体现了课程中的三个核心原则：
   1. 语义完整性：工具聚合了百度千帆的搜索能力，Agent 调用一次即得结构化结果，
      而非先调"发请求"工具再调"解析响应"工具（原子 API 的聚合）
   2. 建设性报错：所有错误返回"错误→原因→解决提示"三段式自然语言，
      让 Agent 能理解错误并自主决定下一步（而非收到 HTTP 500 一头雾水）
   3. I/O 瘦身：输入只有 4 个语义清晰的参数，输出直接返回格式化文本，
      避免将 API 原始响应的所有嵌套字段暴露给 Agent

用于在 Skill 或 Agent 中进行网页搜索，适合作为 baidu_search Skill 的底层实现参考。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator


logger = logging.getLogger(__name__)


class BaiduSearchInput(BaseModel):
    """百度搜索工具的输入参数模式。

    💡【第13课·参数描述工程】每个 Field 的 description 都精确描述了：
       取值范围、默认值、典型使用场景——LLM 生成工具调用时直接参考这些描述，
       Field description 越精准，LLM 传参越准确
    """

    query: str = Field(
        ...,
        description=(
            "搜索查询内容，即用户要搜索的问题或关键词，不能为空，不能只包含空白字符，"
            "通常由一个或几个词组成。"
        ),
    )
    top_k: int | None = Field(
        20,
        description="返回的搜索结果数量，默认20，在精确信息搜索时推荐5以下，广泛调研时10以上。",
    )
    recency_filter: Literal["week", "month", "semiyear", "year"] | None = Field(
        None,
        description=(
            "根据网页发布时间进行筛选，可选值week(最近7天)、month(最近30天)、"
            "semiyear(最近180天)、year(最近365天)。"
        ),
    )
    sites: list[str] | None = Field(
        None,
        description=(
            "指定搜索的站点列表，最多支持20个站点，默认None，仅在设置的站点中进行搜索。"
        ),
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        # 💡【第13课·建设性报错】错误消息三段式结构：错误现象→原因→解决建议
        # Agent 收到此错误后能理解"为什么"并知道"怎么修"，而非收到 ValueError("empty string")
        if not v or not v.strip():
            raise ValueError(
                "错误：查询内容不能为空。"
                "原因：输入的查询参数为空或只包含空白字符。"
                "解决提示：请提供有效的搜索关键词或问题。"
            )
        return v.strip()

    @field_validator("sites")
    @classmethod
    def validate_sites(cls, v: list[str] | None) -> list[str] | None:
        if v and len(v) > 20:
            raise ValueError(
                f"错误：站点列表数量超出限制。"
                f"原因：当前提供了{len(v)}个站点，但最多只支持20个站点。"
                f"解决提示：请将站点列表减少到20个以内。"
            )
        return v

    @field_validator("top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v < 0:
            raise ValueError(
                f"错误：top_k参数值无效。"
                f"原因：当前值{v}小于0，top_k必须大于等于0。"
            )
        if v > 50:
            raise ValueError(
                f"错误：top_k参数值超出限制。"
                f"原因：当前值{v}大于50，web类型最大支持50条结果。"
            )
        return v


class BaiduSearchTool(BaseTool):
    """百度搜索工具（基于百度千帆 web_search 接口）。"""

    # 工具名必须是英文，避免 CrewAI 过滤
    name: str = "search_web"
    description: str = (
        "使用百度搜索引擎查找相关信息，可以按时间范围、指定站点等条件筛选搜索结果。\n"
        "适用于需要查询最新资讯、公开网页内容、特定站点内容的场景。"
    )
    args_schema: type[BaseModel] = BaiduSearchInput

    def _run(
        self,
        query: str,
        top_k: int = 20,
        recency_filter: str | None = None,
        sites: list[str] | None = None,
        **_: Any,
    ) -> str:
        api_key = os.getenv("BAIDU_API_KEY")
        if not api_key:
            logger.error("BAIDU_API_KEY 未设置")
            return (
                "错误：缺少API认证密钥。\n"
                "原因：未提供百度千帆 AppBuilder API Key，环境变量BAIDU_API_KEY未设置。\n"
                "解决提示：联系管理员设置环境变量BAIDU_API_KEY，或检查系统环境变量配置是否正确。\n"
            )

        logger.info("开始执行百度搜索 query=%s top_k=%s recency=%s sites=%s", query, top_k, recency_filter, sites)

        resource_type_filter = [{"type": "web", "top_k": top_k}]
        payload: dict[str, Any] = {
            "messages": [{"content": query, "role": "user"}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": resource_type_filter,
        }
        if recency_filter:
            payload["search_recency_filter"] = recency_filter

        search_filter: dict[str, Any] = {}
        if sites:
            search_filter["match"] = {"site": sites}
        if search_filter:
            payload["search_filter"] = search_filter

        url = "https://qianfan.baidubce.com/v2/ai_search/web_search"
        headers = {
            "X-Appbuilder-Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        logger.info("发送搜索请求 url=%s", url)

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            result = resp.json()
        except requests.exceptions.Timeout:
            logger.error("请求超时")
            return (
                "错误：请求超时。\n"
                "原因：服务器响应时间超过30秒，可能是网络延迟或服务器繁忙。\n"
                "解决提示：稍后重试搜索请求，或尝试其它工具。\n"
            )
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "未知"
            logger.error("HTTP 请求错误 status=%s err=%s", status, e)
            return (
                f"错误：HTTP请求错误。\n"
                f"原因：HTTP请求失败，状态码{status}，错误详情：{e}。\n"
                f"解决提示：请稍后重试，若持续失败可尝试其它工具。\n"
            )
        except requests.exceptions.RequestException as e:
            logger.error("网络请求异常 err=%s", e)
            return (
                f"错误：网络请求异常。\n"
                f"原因：网络请求过程中发生异常，错误类型：{type(e).__name__}，错误详情：{e}。\n"
                f"解决提示：请稍后重试，若持续失败可尝试其它工具。\n"
            )
        except json.JSONDecodeError as e:
            logger.error("JSON 解析错误 err=%s", e)
            return (
                "错误：响应解析错误。\n"
                f"原因：服务器返回的响应不是有效的JSON格式，错误详情：{e}。\n"
                "解决提示：稍后重试，若持续失败可尝试其它工具。\n"
            )

        request_id = result.get("request_id") or result.get("requestId", "未知")
        logger.info("百度搜索请求ID request_id=%s", request_id)

        error_code = result.get("code")
        if error_code is not None and error_code not in (0, ""):
            error_msg = result.get("message", "未知错误")
            logger.error("API 返回错误 code=%s msg=%s", error_code, error_msg)
            return (
                f"错误：API返回错误。\n"
                f"原因：百度搜索API返回错误码{error_code}，错误信息：{error_msg}，请求ID：{request_id}。\n"
                "解决提示：请检查请求参数或稍后重试，若持续失败可尝试其它工具。\n"
            )

        references = result.get("references", [])
        if not references:
            logger.warning("搜索完成但无结果 query=%s", query)
            return (
                f"错误：未找到相关搜索结果。\n"
                f"原因：使用关键词'{query}'进行搜索，但未找到匹配的结果，可能是关键词过于具体或过滤条件过于严格。\n"
                "解决提示：尝试更通用的搜索词，或放宽过滤条件（如去掉站点限制、时间范围）。\n"
            )

        logger.info("搜索成功，结果数=%s", len(references))
        lines: list[str] = [f"找到 {len(references)} 条搜索结果", ""]
        for ref in references:
            ref_id = ref.get("id", "?")
            title = ref.get("title", "无标题")
            url = ref.get("url", "")
            content = ref.get("content", "")
            lines.append(
                f"结果{ref_id}: [ {title} ] ( {url} )\n  内容摘要: {content}\n"
            )
            lines.append("")

        return "\n".join(lines)

