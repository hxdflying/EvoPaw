"""CleanupService — 存储清理服务

职责：按策略清理过期的工作空间文件和 Trace 记录，防止磁盘无限增长。

触发时机：
- 进程启动时执行一次 sweep（清理历史残留）
- 每日凌晨 3:00 由 CronService 触发执行

清理策略（可在 config.yaml 中调整）：
  data/workspace/sessions/*/tmp/      → 1 天
  data/workspace/sessions/*/uploads/  → 7 天
  data/workspace/sessions/*/outputs/  → 30 天
  data/traces/                         → 30 天
  data/sessions/*.jsonl                → 365 天
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CleanupPolicy:
    """清理策略配置。"""

    # 相对于 data_dir 的目录 glob 模式 → 保留天数
    rules: dict[str, int] = field(
        default_factory=lambda: {
            "workspace/sessions/*/tmp": 1,
            "workspace/sessions/*/uploads": 7,
            "workspace/sessions/*/outputs": 30,
            "traces": 30,
        }
    )
    # sessions/*.jsonl 文件的保留天数（按文件 mtime）
    session_jsonl_retention_days: int = 365


class CleanupService:
    """异步存储清理服务。

    使用方式：
        svc = CleanupService(data_dir=Path("./data"))
        await svc.sweep()           # 手动执行一次完整扫描
    """

    def __init__(
        self,
        data_dir: Path,
        policy: CleanupPolicy | None = None,
    ) -> None:
        self._data_dir = data_dir.resolve()
        self._policy = policy or CleanupPolicy()
        self._lock = asyncio.Lock()

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    async def sweep(self) -> dict[str, int]:
        """执行一次完整清理扫描，返回各规则删除的文件/目录数。"""
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._sync_sweep)

    # ── 内部同步实现（在线程池中运行，避免阻塞事件循环）──────────────────────

    def _sync_sweep(self) -> dict[str, int]:
        stats: dict[str, int] = {}
        now_s = time.time()

        # 1. 工作空间目录清理（按目录模式 + 文件 mtime）
        for pattern, days in self._policy.rules.items():
            cutoff_s = now_s - days * 86400
            count = 0
            # 展开 glob 模式
            matched_dirs = list(self._data_dir.glob(pattern))
            for target_dir in matched_dirs:
                if not target_dir.is_dir():
                    continue
                count += self._clean_dir_by_mtime(target_dir, cutoff_s)
            stats[pattern] = count

        # 2. sessions/*.jsonl 文件清理（按文件 mtime）
        session_dir = self._data_dir / "sessions"
        if session_dir.exists():
            cutoff_s = now_s - self._policy.session_jsonl_retention_days * 86400
            count = 0
            for jsonl_file in session_dir.glob("*.jsonl"):
                try:
                    if jsonl_file.stat().st_mtime < cutoff_s:
                        jsonl_file.unlink()
                        count += 1
                        logger.info("cleanup: removed session file %s", jsonl_file)
                except OSError as exc:
                    logger.warning("cleanup: failed to remove %s: %s", jsonl_file, exc)
            stats["sessions/*.jsonl"] = count

        total = sum(stats.values())
        if total > 0:
            logger.info("cleanup: sweep complete, removed %d items. details: %s", total, stats)
        else:
            logger.debug("cleanup: sweep complete, nothing to remove")
        return stats

    def _clean_dir_by_mtime(self, target_dir: Path, cutoff_s: float) -> int:
        """清理目录内超过截止时间（mtime）的文件和子目录。

        仅删除内容，保留目录本身（防止后续写入报错）。
        """
        count = 0
        if not target_dir.exists():
            return 0

        for item in list(target_dir.iterdir()):
            try:
                mtime = item.stat().st_mtime
                if mtime >= cutoff_s:
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                    count += 1
                    logger.debug("cleanup: removed dir %s (mtime %.0f)", item, mtime)
                else:
                    item.unlink()
                    count += 1
                    logger.debug("cleanup: removed file %s (mtime %.0f)", item, mtime)
            except OSError as exc:
                logger.warning("cleanup: failed to remove %s: %s", item, exc)

        return count

    # ── 工作空间初始化 ────────────────────────────────────────────────────────

    def ensure_workspace_dirs(self, session_id: str) -> None:
        """确保 session 工作目录结构存在（首次访问时创建）。"""
        session_base = self._data_dir / "workspace" / "sessions" / session_id
        for sub in ("uploads", "outputs", "tmp"):
            (session_base / sub).mkdir(parents=True, exist_ok=True)

    def write_feishu_credentials(self, app_id: str, app_secret: str) -> None:
        """将飞书凭证写入沙盒 .config/feishu.json（凭证不经过 LLM）。

        文件权限设为 0o600（仅属主可读），目录权限设为 0o700，
        防止同宿主机其他用户或容器内非 root 进程读取凭证。
        """
        import json

        config_dir = self._data_dir / "workspace" / ".config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_dir.chmod(0o700)  # 目录：仅属主可进入

        creds = {"app_id": app_id, "app_secret": app_secret}
        creds_file = config_dir / "feishu.json"

        # 原子写入：先写临时文件再 rename
        tmp_file = creds_file.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_file.chmod(0o600)  # 临时文件：仅属主可读写
        os.replace(tmp_file, creds_file)
        # rename 后再次设置权限（不同 OS 行为不同）
        creds_file.chmod(0o600)
        logger.info("cleanup: feishu credentials written to %s (mode 0600)", creds_file)

    def write_baidu_credentials(self, api_key: str) -> None:
        """将百度千帆 API Key 写入沙盒 .config/baidu.json（凭证不经过 LLM）。

        文件权限设为 0o600（仅属主可读），与 feishu.json 保持一致。
        若 api_key 为空则跳过（百度搜索 Skill 不可用，但不阻断主流程）。
        """
        import json

        if not api_key:
            logger.info("cleanup: BAIDU_API_KEY 未配置，跳过写入 baidu.json")
            return

        config_dir = self._data_dir / "workspace" / ".config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_dir.chmod(0o700)

        creds = {"api_key": api_key}
        creds_file = config_dir / "baidu.json"

        tmp_file = creds_file.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_file.chmod(0o600)
        os.replace(tmp_file, creds_file)
        creds_file.chmod(0o600)
        logger.info("cleanup: baidu credentials written to %s (mode 0600)", creds_file)
