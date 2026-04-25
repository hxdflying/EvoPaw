"""scripts/audit_audio_sample_rate.py 的离线单测.

不依赖 ffprobe；只测 _collect_files / _recommend 等纯函数分支。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    """importlib 加载 scripts/audit_audio_sample_rate.py 为模块.

    注：必须在 ``exec_module`` 之前把模块挂到 ``sys.modules`` —— 否则模块内的
    ``@dataclass`` + ``from __future__ import annotations`` 解析注解时找不到本模块。
    """
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "audit_audio_sample_rate.py"
    spec = importlib.util.spec_from_file_location("audit_audio_sample_rate", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


class TestCollectFiles:
    def test_single_file_returned_as_is(self, tmp_path, mod):
        f = tmp_path / "a.opus"
        f.write_bytes(b"x")
        assert mod._collect_files([f]) == [f]

    def test_directory_recursive_filters_extensions(self, tmp_path, mod):
        (tmp_path / "voice").mkdir()
        good = tmp_path / "voice" / "a.opus"
        good.write_bytes(b"x")
        also_good = tmp_path / "voice" / "b.audio"
        also_good.write_bytes(b"x")
        irrelevant = tmp_path / "voice" / "readme.txt"
        irrelevant.write_text("hi")

        files = sorted(mod._collect_files([tmp_path]))
        assert good in files
        assert also_good in files
        assert irrelevant not in files

    def test_empty_inputs_returns_empty(self, tmp_path, mod):
        assert mod._collect_files([]) == []


class TestRecommend:
    def _r(self, sample_rate: int, mod):
        return mod.ProbeResult(
            path=Path("x"),
            codec="opus",
            sample_rate=sample_rate,
            channels=1,
            status="PASS" if sample_rate == 16000 else "WARN_OFF_RATE",
        )

    def test_all_16k_recommends_plan_a(self, mod):
        results = [self._r(16000, mod), self._r(16000, mod)]
        text = mod._recommend(results)
        assert "方案 A" in text
        assert "16kHz" in text

    def test_all_8k_recommends_8k_model(self, mod):
        results = [self._r(8000, mod), self._r(8000, mod)]
        text = mod._recommend(results)
        assert "方案 A" in text
        assert "fun-asr-flash-8k-realtime" in text

    def test_uniform_other_rate_recommends_plan_b(self, mod):
        results = [self._r(48000, mod) for _ in range(3)]
        text = mod._recommend(results)
        assert "方案 B" in text
        assert "ffmpeg" in text

    def test_mixed_rates_recommends_plan_b(self, mod):
        results = [self._r(16000, mod), self._r(48000, mod), self._r(24000, mod)]
        text = mod._recommend(results)
        assert "方案 B" in text
        assert "混合" in text

    def test_no_samples_returns_inconclusive(self, mod):
        text = mod._recommend([])
        assert "无法判定" in text or "暂无可用样本" in text


class TestEntryPoint:
    def test_main_returns_2_on_no_files(self, tmp_path, mod, capsys):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        rc = mod.main([str(empty_dir)])
        assert rc == 2
