"""CleanupService 单元测试"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from xiaopaw.cleanup.service import CleanupPolicy, CleanupService


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_service(tmp_path: Path, policy: CleanupPolicy | None = None) -> CleanupService:
    return CleanupService(data_dir=tmp_path, policy=policy)


def _touch_with_age(path: Path, age_days: float) -> None:
    """创建文件并设置 mtime 为若干天前。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    old_ts = time.time() - age_days * 86400
    import os
    os.utime(path, (old_ts, old_ts))


def _mkdir_with_age(path: Path, age_days: float) -> None:
    """创建目录并设置 mtime 为若干天前。"""
    path.mkdir(parents=True, exist_ok=True)
    old_ts = time.time() - age_days * 86400
    import os
    os.utime(path, (old_ts, old_ts))


# ── sweep ────────────────────────────────────────────────────────────────────


class TestSweep:
    @pytest.mark.asyncio
    async def test_sweep_removes_old_tmp_files(self, tmp_path: Path):
        svc = _make_service(tmp_path, CleanupPolicy(rules={"workspace/sessions/*/tmp": 1}))
        # 创建旧文件（2 天前）
        old_file = tmp_path / "workspace/sessions/s-001/tmp/old.txt"
        _touch_with_age(old_file, 2)
        # 创建新文件（今天）
        new_file = tmp_path / "workspace/sessions/s-001/tmp/new.txt"
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.touch()

        stats = await svc.sweep()

        assert not old_file.exists(), "旧文件应被删除"
        assert new_file.exists(), "新文件应保留"
        assert stats.get("workspace/sessions/*/tmp", 0) == 1

    @pytest.mark.asyncio
    async def test_sweep_removes_old_dir(self, tmp_path: Path):
        svc = _make_service(tmp_path, CleanupPolicy(rules={"workspace/sessions/*/tmp": 1}))
        old_dir = tmp_path / "workspace/sessions/s-001/tmp/old_dir"
        _mkdir_with_age(old_dir, 2)

        stats = await svc.sweep()

        assert not old_dir.exists(), "旧目录应被删除"
        assert stats.get("workspace/sessions/*/tmp", 0) == 1

    @pytest.mark.asyncio
    async def test_sweep_keeps_new_files(self, tmp_path: Path):
        svc = _make_service(tmp_path, CleanupPolicy(rules={"workspace/sessions/*/tmp": 7}))
        new_file = tmp_path / "workspace/sessions/s-001/tmp/recent.txt"
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.touch()  # mtime = now

        stats = await svc.sweep()

        assert new_file.exists(), "新文件不应被删除"
        assert stats.get("workspace/sessions/*/tmp", 0) == 0

    @pytest.mark.asyncio
    async def test_sweep_removes_old_session_jsonl(self, tmp_path: Path):
        svc = _make_service(
            tmp_path,
            CleanupPolicy(rules={}, session_jsonl_retention_days=30),
        )
        old_jsonl = tmp_path / "sessions/s-old.jsonl"
        _touch_with_age(old_jsonl, 31)
        new_jsonl = tmp_path / "sessions/s-new.jsonl"
        new_jsonl.parent.mkdir(parents=True, exist_ok=True)
        new_jsonl.touch()

        stats = await svc.sweep()

        assert not old_jsonl.exists(), "过期 JSONL 应被删除"
        assert new_jsonl.exists(), "新 JSONL 应保留"
        assert stats.get("sessions/*.jsonl", 0) == 1

    @pytest.mark.asyncio
    async def test_sweep_empty_workspace_ok(self, tmp_path: Path):
        """空目录时 sweep 不应报错。"""
        svc = _make_service(tmp_path)
        stats = await svc.sweep()
        assert isinstance(stats, dict)

    @pytest.mark.asyncio
    async def test_sweep_preserves_target_directory(self, tmp_path: Path):
        """删除文件后目录本身应保留。"""
        svc = _make_service(tmp_path, CleanupPolicy(rules={"workspace/sessions/*/tmp": 1}))
        target_dir = tmp_path / "workspace/sessions/s-001/tmp"
        target_dir.mkdir(parents=True)
        old_file = target_dir / "old.txt"
        _touch_with_age(old_file, 2)

        await svc.sweep()

        assert target_dir.exists(), "目录本身不应被删除"
        assert not old_file.exists(), "文件应被删除"


# ── ensure_workspace_dirs ─────────────────────────────────────────────────────


class TestEnsureWorkspaceDirs:
    def test_creates_required_subdirs(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        svc.ensure_workspace_dirs("my-session")

        base = tmp_path / "workspace/sessions/my-session"
        assert (base / "uploads").is_dir()
        assert (base / "outputs").is_dir()
        assert (base / "tmp").is_dir()

    def test_idempotent_on_existing_dirs(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        svc.ensure_workspace_dirs("my-session")
        svc.ensure_workspace_dirs("my-session")  # 再次调用不报错


# ── write_feishu_credentials ──────────────────────────────────────────────────


class TestWriteFeishuCredentials:
    def test_credentials_written_correctly(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        svc.write_feishu_credentials(app_id="cli_test", app_secret="secret123")

        creds_file = tmp_path / "workspace/.config/feishu.json"
        assert creds_file.exists()
        creds = json.loads(creds_file.read_text())
        assert creds["app_id"] == "cli_test"
        assert creds["app_secret"] == "secret123"

    def test_credentials_atomic_write(self, tmp_path: Path):
        """写入过程中不应存在 .json.tmp 残留文件。"""
        svc = _make_service(tmp_path)
        svc.write_feishu_credentials(app_id="a", app_secret="b")

        tmp_file = tmp_path / "workspace/.config/feishu.json.tmp"
        assert not tmp_file.exists(), ".tmp 文件应在写入后被清理"

    def test_overwrite_existing_credentials(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        svc.write_feishu_credentials(app_id="old_id", app_secret="old_secret")
        svc.write_feishu_credentials(app_id="new_id", app_secret="new_secret")

        creds_file = tmp_path / "workspace/.config/feishu.json"
        creds = json.loads(creds_file.read_text())
        assert creds["app_id"] == "new_id"
        assert creds["app_secret"] == "new_secret"


# ── write_baidu_credentials ───────────────────────────────────────────────────


class TestWriteBaiduCredentials:
    def test_credentials_written_correctly(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        svc.write_baidu_credentials(api_key="qianfan-key-xyz")

        creds_file = tmp_path / "workspace/.config/baidu.json"
        assert creds_file.exists()
        creds = json.loads(creds_file.read_text())
        assert creds["api_key"] == "qianfan-key-xyz"

    def test_empty_api_key_skips_write(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        svc.write_baidu_credentials(api_key="")

        creds_file = tmp_path / "workspace/.config/baidu.json"
        assert not creds_file.exists(), "空 api_key 不应写入文件"

    def test_credentials_atomic_write(self, tmp_path: Path):
        """写入过程中不应留有 .json.tmp 残留文件。"""
        svc = _make_service(tmp_path)
        svc.write_baidu_credentials(api_key="key-123")

        tmp_file = tmp_path / "workspace/.config/baidu.json.tmp"
        assert not tmp_file.exists(), ".tmp 文件应在写入后被清理"

    def test_overwrite_existing_credentials(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        svc.write_baidu_credentials(api_key="old-key")
        svc.write_baidu_credentials(api_key="new-key")

        creds_file = tmp_path / "workspace/.config/baidu.json"
        creds = json.loads(creds_file.read_text())
        assert creds["api_key"] == "new-key"

    def test_config_dir_created_if_missing(self, tmp_path: Path):
        """目标目录不存在时应自动创建。"""
        svc = _make_service(tmp_path)
        config_dir = tmp_path / "workspace" / ".config"
        assert not config_dir.exists()

        svc.write_baidu_credentials(api_key="key-abc")

        assert config_dir.is_dir()
        assert (config_dir / "baidu.json").exists()

