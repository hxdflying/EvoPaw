"""集成测试：feishu_ops 新脚本实况验证

测试策略：
  - 动态导入 scripts 模块（同单元测试，避免子进程开销）
  - 用 patch.object 替换 _feishu_auth._get_token 返回真实 token
  - requests.* 调用真实飞书 API，验证端到端正确性
  - @pytest.mark.feishu —— 需要 FEISHU_APP_ID + FEISHU_APP_SECRET

运行方式：
    # 跑全部 feishu 实况测试
    FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \\
        pytest tests/integration/test_feishu_ops_live.py -v -s

    # 跑指定类
    FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \\
        pytest tests/integration/test_feishu_ops_live.py::TestBitablePipelineLive -v -s

注意：每次运行会在飞书云空间创建真实文档/表格/多维表格（放在应用根目录）。
      可以通过 --name / --title 的时间戳后缀区分，手动定期清理即可。
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import requests as _requests


# ── Scripts 目录注入 ──────────────────────────────────────────────────────────

SCRIPTS_DIR = (
    Path(__file__).parent.parent.parent / "xiaopaw" / "skills" / "feishu_ops" / "scripts"
)


def _import_script(name: str):
    """动态导入 feishu_ops scripts 目录下的模块（缓存到 sys.modules）。"""
    if SCRIPTS_DIR not in [Path(p) for p in sys.path]:
        sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.import_module(name)


# ── Session 级别 Fixtures ─────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def feishu_creds() -> dict[str, str]:
    """读取环境变量中的飞书凭证；未设置则跳过整个 session。"""
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        pytest.skip("FEISHU_APP_ID / FEISHU_APP_SECRET 未设置，跳过飞书实况集成测试")
    return {"app_id": app_id, "app_secret": app_secret}


@pytest.fixture(scope="session")
def feishu_token(feishu_creds: dict[str, str]) -> str:
    """获取真实 tenant_access_token（session 级别，2 小时内不重新获取）。"""
    resp = _requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json=feishu_creds,
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        pytest.skip(f"获取 tenant_access_token 失败，请检查凭证：{data}")
    return data["tenant_access_token"]


@pytest.fixture(scope="session")
def auth_mod():
    """_feishu_auth 模块对象（session 级别，所有脚本共享同一实例）。"""
    return _import_script("_feishu_auth")


# ── 核心辅助：运行脚本并解析输出 ──────────────────────────────────────────────


def _run(mod, argv: list[str], auth_mod, feishu_token: str, capsys) -> dict:
    """
    调用脚本 main()：
      1. 替换 sys.argv
      2. patch _feishu_auth._get_token → 真实 token（跳过凭证文件读取）
      3. 捕获 stdout JSON 输出
    """
    with patch("sys.argv", [mod.__name__] + argv):
        with patch.object(auth_mod, "_get_token", return_value=feishu_token):
            with pytest.raises(SystemExit):
                mod.main()
    raw = capsys.readouterr().out
    return json.loads(raw)


def _ts() -> str:
    """6 位时间戳后缀，避免飞书资源名称冲突。"""
    return str(int(time.time()))[-6:]


# ─────────────────────────────────────────────────────────────────────────────
# create_doc.py 实况测试
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.feishu
@pytest.mark.integration
class TestCreateDocLive:
    """验证 create_doc.py 能在飞书云空间成功创建文档。"""

    def test_creates_doc_returns_document_id(self, feishu_token, auth_mod, capsys):
        """基础创建：返回非空 document_id 和 url。"""
        mod = _import_script("create_doc")
        out = _run(mod, ["--title", f"集成测试文档_{_ts()}"], auth_mod, feishu_token, capsys)

        assert out["errcode"] == 0, f"创建文档失败：{out['errmsg']}"
        assert out["data"]["document_id"], "document_id 不能为空"
        assert out["data"]["url"], "url 不能为空"
        assert out["data"]["blocks_written"] == 0

    def test_title_preserved_in_response(self, feishu_token, auth_mod, capsys):
        """返回的 title 字段应与入参一致。"""
        title = f"XiaoPaw测试_{_ts()}"
        mod = _import_script("create_doc")
        out = _run(mod, ["--title", title], auth_mod, feishu_token, capsys)

        assert out["errcode"] == 0
        assert out["data"]["title"] == title

    def test_creates_doc_with_markdown_content(self, feishu_token, auth_mod, capsys):
        """带 --content 创建：文档应写入指定 Markdown 内容，blocks_written > 0。"""
        md = (
            "# 测试报告\n\n"
            "## 一、概述\n\n"
            "这是一份集成测试生成的文档。\n\n"
            "- 要点 A\n"
            "- 要点 B\n\n"
            "1. 步骤一\n"
            "2. 步骤二\n\n"
            "```\nprint('hello world')\n```"
        )
        mod = _import_script("create_doc")
        out = _run(
            mod,
            ["--title", f"MD内容测试_{_ts()}", "--content", md],
            auth_mod, feishu_token, capsys,
        )

        assert out["errcode"] == 0, f"创建文档失败：{out['errmsg']}"
        assert out["data"]["document_id"], "document_id 不能为空"
        assert out["data"]["blocks_written"] > 0, "应写入至少一个 block"


# ─────────────────────────────────────────────────────────────────────────────
# create_sheet.py 实况测试
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.feishu
@pytest.mark.integration
class TestCreateSheetLive:
    """验证 create_sheet.py 能成功创建飞书电子表格。"""

    def test_creates_sheet_returns_token(self, feishu_token, auth_mod, capsys):
        """基础创建：返回非空 spreadsheet_token 和 url。"""
        mod = _import_script("create_sheet")
        out = _run(mod, ["--title", f"集成测试表格_{_ts()}"], auth_mod, feishu_token, capsys)

        assert out["errcode"] == 0, f"创建表格失败：{out['errmsg']}"
        assert out["data"]["spreadsheet_token"], "spreadsheet_token 不能为空"
        assert out["data"]["url"], "url 不能为空"

    def test_title_preserved(self, feishu_token, auth_mod, capsys):
        title = f"销售报表_{_ts()}"
        mod = _import_script("create_sheet")
        out = _run(mod, ["--title", title], auth_mod, feishu_token, capsys)

        assert out["errcode"] == 0
        assert out["data"]["title"] == title


# ─────────────────────────────────────────────────────────────────────────────
# write_sheet.py 实况测试（依赖 create_sheet.py）
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.feishu
@pytest.mark.integration
class TestWriteSheetLive:
    """验证 write_sheet.py 能向飞书表格写入数据。"""

    def test_creates_sheet_then_writes_data(self, feishu_token, auth_mod, capsys):
        """
        全链路：
          1. create_sheet 创建新表格 → 获取 spreadsheet_token
          2. write_sheet 写入 3 行 3 列数据
          3. 验证返回范围和行列数
        """
        # 1. 创建表格
        sheet_mod = _import_script("create_sheet")
        sheet_out = _run(
            sheet_mod,
            ["--title", f"集成测试写入_{_ts()}"],
            auth_mod, feishu_token, capsys,
        )
        assert sheet_out["errcode"] == 0, f"创建表格失败：{sheet_out['errmsg']}"
        token = sheet_out["data"]["spreadsheet_token"]

        # 2. 写入数据
        values = [
            ["姓名", "月份", "销售额"],
            ["Alice", "1月", 100000],
            ["Bob",   "2月",  85000],
        ]
        write_mod = _import_script("write_sheet")
        write_out = _run(
            write_mod,
            ["--sheet", token, "--values", json.dumps(values, ensure_ascii=False)],
            auth_mod, feishu_token, capsys,
        )

        assert write_out["errcode"] == 0, f"写入数据失败：{write_out['errmsg']}"
        assert write_out["data"]["rows_written"] == 3
        assert write_out["data"]["cols_written"] == 3
        assert write_out["data"]["spreadsheet_token"] == token
        # 范围应包含 A1:C3
        assert write_out["data"]["range"].endswith("C3"), (
            f"期望范围以 C3 结尾，实际：{write_out['data']['range']}"
        )

    def test_writes_with_start_cell(self, feishu_token, auth_mod, capsys):
        """指定 --start_cell B2 写入 2 列数据，验证终止格为 C3。"""
        sheet_mod = _import_script("create_sheet")
        sheet_out = _run(
            sheet_mod,
            ["--title", f"偏移写入测试_{_ts()}"],
            auth_mod, feishu_token, capsys,
        )
        assert sheet_out["errcode"] == 0
        token = sheet_out["data"]["spreadsheet_token"]

        values = [["标题A", "标题B"], ["数据1", "数据2"]]
        write_mod = _import_script("write_sheet")
        write_out = _run(
            write_mod,
            [
                "--sheet", token,
                "--values", json.dumps(values, ensure_ascii=False),
                "--start_cell", "B2",
            ],
            auth_mod, feishu_token, capsys,
        )

        assert write_out["errcode"] == 0, f"写入失败：{write_out['errmsg']}"
        assert write_out["data"]["rows_written"] == 2
        assert write_out["data"]["cols_written"] == 2
        # 从 B2 开始，2行2列 → B2:C3
        assert write_out["data"]["range"].endswith("C3"), (
            f"期望范围以 C3 结尾，实际：{write_out['data']['range']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# create_bitable.py 实况测试
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.feishu
@pytest.mark.integration
class TestCreateBitableLive:
    """验证 create_bitable.py 能成功创建多维表格应用。"""

    def test_creates_bitable_returns_app_token(self, feishu_token, auth_mod, capsys):
        mod = _import_script("create_bitable")
        out = _run(
            mod,
            ["--name", f"集成测试Bitable_{_ts()}"],
            auth_mod, feishu_token, capsys,
        )

        assert out["errcode"] == 0, f"创建多维表格失败：{out['errmsg']}"
        assert out["data"]["app_token"], "app_token 不能为空"
        assert out["data"]["url"], "url 不能为空"


# ─────────────────────────────────────────────────────────────────────────────
# create_bitable_table.py + write_bitable_records.py 全链路测试
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.feishu
@pytest.mark.integration
class TestBitablePipelineLive:
    """
    多维表格全链路：
      create_bitable → create_bitable_table（含字段定义）→ write_bitable_records
    """

    def test_full_bitable_pipeline(self, feishu_token, auth_mod, capsys):
        """
        端到端验证：
          1. 创建多维表格应用 → app_token
          2. 创建数据表（text / select / checkbox 三种字段类型）→ table_id
          3. 写入 3 条记录 → 验证 record_count == 3
        """
        # ── 步骤 1：创建多维表格 ──────────────────────────────────────────────
        bitable_mod = _import_script("create_bitable")
        bitable_out = _run(
            bitable_mod,
            ["--name", f"集成测试Bitable_{_ts()}"],
            auth_mod, feishu_token, capsys,
        )
        assert bitable_out["errcode"] == 0, f"创建 Bitable 失败：{bitable_out['errmsg']}"
        app_token = bitable_out["data"]["app_token"]
        assert app_token

        # ── 步骤 2：创建数据表 ────────────────────────────────────────────────
        fields = [
            {"name": "任务名称", "type": "text"},
            {"name": "优先级",   "type": "select",   "options": ["高", "中", "低"]},
            {"name": "完成",     "type": "checkbox"},
        ]
        table_mod = _import_script("create_bitable_table")
        table_out = _run(
            table_mod,
            [
                "--app", app_token,
                "--name", "任务清单",
                "--fields", json.dumps(fields, ensure_ascii=False),
            ],
            auth_mod, feishu_token, capsys,
        )
        assert table_out["errcode"] == 0, f"创建数据表失败：{table_out['errmsg']}"
        table_id = table_out["data"]["table_id"]
        assert table_id, "table_id 不能为空"
        assert table_out["data"]["table_name"] == "任务清单"
        assert len(table_out["data"]["fields_created"]) == 3

        # ── 步骤 3：写入记录 ──────────────────────────────────────────────────
        records = [
            {"任务名称": "完成集成测试", "优先级": "高",  "完成": False},
            {"任务名称": "代码 Review",  "优先级": "中",  "完成": True},
            {"任务名称": "文档更新",     "优先级": "低",  "完成": False},
        ]
        records_mod = _import_script("write_bitable_records")
        records_out = _run(
            records_mod,
            [
                "--app", app_token,
                "--table_id", table_id,
                "--records", json.dumps(records, ensure_ascii=False),
            ],
            auth_mod, feishu_token, capsys,
        )
        assert records_out["errcode"] == 0, f"写入记录失败：{records_out['errmsg']}"
        assert records_out["data"]["record_count"] == 3
        assert len(records_out["data"]["record_ids"]) == 3

    def test_creates_table_all_field_types(self, feishu_token, auth_mod, capsys):
        """验证所有支持的字段类型均能成功创建。"""
        # 先创建 bitable
        bitable_mod = _import_script("create_bitable")
        bitable_out = _run(
            bitable_mod,
            ["--name", f"全字段测试_{_ts()}"],
            auth_mod, feishu_token, capsys,
        )
        assert bitable_out["errcode"] == 0
        app_token = bitable_out["data"]["app_token"]

        # 包含所有 7 种字段类型
        fields = [
            {"name": "文本字段",   "type": "text"},
            {"name": "数字字段",   "type": "number"},
            {"name": "单选字段",   "type": "select",      "options": ["A", "B", "C"]},
            {"name": "多选字段",   "type": "multiselect", "options": ["X", "Y"]},
            {"name": "日期字段",   "type": "date"},
            {"name": "勾选字段",   "type": "checkbox"},
            {"name": "链接字段",   "type": "url"},
        ]
        table_mod = _import_script("create_bitable_table")
        table_out = _run(
            table_mod,
            [
                "--app", app_token,
                "--name", "全字段数据表",
                "--fields", json.dumps(fields, ensure_ascii=False),
            ],
            auth_mod, feishu_token, capsys,
        )

        assert table_out["errcode"] == 0, f"创建全字段数据表失败：{table_out['errmsg']}"
        assert len(table_out["data"]["fields_created"]) == 7
        created_names = [f["name"] for f in table_out["data"]["fields_created"]]
        for field in fields:
            assert field["name"] in created_names, f"字段 {field['name']} 未被创建"

    def test_writes_large_batch(self, feishu_token, auth_mod, capsys):
        """写入 50 条记录（验证非边界批量写入）。"""
        # 创建 bitable + table
        bitable_mod = _import_script("create_bitable")
        bitable_out = _run(
            bitable_mod,
            ["--name", f"批量写入测试_{_ts()}"],
            auth_mod, feishu_token, capsys,
        )
        assert bitable_out["errcode"] == 0
        app_token = bitable_out["data"]["app_token"]

        table_mod = _import_script("create_bitable_table")
        table_out = _run(
            table_mod,
            [
                "--app", app_token,
                "--name", "批量数据表",
                "--fields", json.dumps([{"name": "序号", "type": "number"}], ensure_ascii=False),
            ],
            auth_mod, feishu_token, capsys,
        )
        assert table_out["errcode"] == 0
        table_id = table_out["data"]["table_id"]

        # 写入 50 条
        records = [{"序号": i} for i in range(1, 51)]
        records_mod = _import_script("write_bitable_records")
        records_out = _run(
            records_mod,
            [
                "--app", app_token,
                "--table_id", table_id,
                "--records", json.dumps(records, ensure_ascii=False),
            ],
            auth_mod, feishu_token, capsys,
        )

        assert records_out["errcode"] == 0, f"批量写入失败：{records_out['errmsg']}"
        assert records_out["data"]["record_count"] == 50


# ─────────────────────────────────────────────────────────────────────────────
# upload_sheet.py 实况测试
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.feishu
@pytest.mark.integration
class TestUploadSheetLive:
    """验证 upload_sheet.py 能将本地 xlsx 文件导入为飞书电子表格。"""

    def test_uploads_xlsx_returns_spreadsheet_token(
        self, feishu_token, auth_mod, capsys, tmp_path
    ):
        """
        全链路：
          1. 创建最小 xlsx 文件（openpyxl）
          2. upload_sheet 上传并导入
          3. 验证返回 spreadsheet_token 和 url
        """
        try:
            import openpyxl  # noqa: PLC0415
        except ImportError:
            pytest.skip("openpyxl 未安装，跳过 upload_sheet 实况测试")

        # 生成真实 xlsx 文件
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "测试数据"
        ws.append(["姓名", "部门", "入职年份"])
        ws.append(["张三", "工程部", 2021])
        ws.append(["李四", "产品部", 2022])
        xlsx_path = tmp_path / f"测试数据_{_ts()}.xlsx"
        wb.save(str(xlsx_path))

        mod = _import_script("upload_sheet")
        out = _run(
            mod,
            [
                "--file_path", str(xlsx_path),
                "--title", f"集成测试导入_{_ts()}",
            ],
            auth_mod, feishu_token, capsys,
        )

        assert out["errcode"] == 0, f"导入失败：{out['errmsg']}"
        assert out["data"]["spreadsheet_token"], "spreadsheet_token 不能为空"
        assert out["data"]["url"], "url 不能为空"
        assert out["data"]["file_size"] > 0
