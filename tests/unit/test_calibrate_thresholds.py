"""scripts/calibrate_thresholds.py 单测.

只覆盖纯函数：metrics 文本解析 + 分位数估算 + 推荐取整。不打网络。
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "calibrate_thresholds.py"
    spec = importlib.util.spec_from_file_location("calibrate_thresholds", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _make_payload(buckets: list[tuple[str, int]], total_sum: float) -> str:
    """构造一段最小 Prometheus exposition：一条 latency histogram + 一条 requests counter."""
    lines = [
        "# HELP evopaw_asr_latency_seconds latency",
        "# TYPE evopaw_asr_latency_seconds histogram",
    ]
    cumulative = 0
    for le, count in buckets:
        cumulative += count
        lines.append(
            f'evopaw_asr_latency_seconds_bucket{{provider="aliyun_funasr_realtime",le="{le}"}} '
            f"{cumulative}"
        )
    total = sum(c for _, c in buckets)
    lines.append(
        f'evopaw_asr_latency_seconds_sum{{provider="aliyun_funasr_realtime"}} {total_sum}'
    )
    lines.append(
        f'evopaw_asr_latency_seconds_count{{provider="aliyun_funasr_realtime"}} {total}'
    )
    lines.append("# TYPE evopaw_asr_requests_total counter")
    lines.append(
        f'evopaw_asr_requests_total{{provider="aliyun_funasr_realtime",status="success"}} {total}'
    )
    return "\n".join(lines) + "\n"


class TestParseMetrics:
    def test_parses_histogram_buckets_and_count(self, mod):
        payload = _make_payload(
            buckets=[("0.1", 0), ("0.5", 5), ("1.0", 10), ("5.0", 5), ("+Inf", 0)],
            total_sum=12.5,
        )
        series, counters = mod._parse_metrics(payload)
        assert len(series) == 1
        s = series[0]
        assert s.count_value == 20
        assert s.sum_value == pytest.approx(12.5)
        # 桶数量 = 5（含 +Inf）
        assert len(s.buckets) == 5
        # +Inf 桶被识别为 math.inf
        assert math.isinf(s.buckets[-1][0])

    def test_requests_counter_keyed_by_status(self, mod):
        payload = _make_payload(
            buckets=[("1.0", 5), ("+Inf", 0)],
            total_sum=2.5,
        )
        _, counters = mod._parse_metrics(payload)
        # 至少有一个 (provider, status=success) → 5
        assert any(
            dict(labels).get("status") == "success" and value == 5
            for labels, value in counters.items()
        )

    def test_ignores_unrelated_metrics(self, mod):
        payload = _make_payload([("1.0", 1), ("+Inf", 0)], 0.5)
        payload += "evopaw_other_metric 42\n"
        series, _ = mod._parse_metrics(payload)
        assert len(series) == 1


class TestQuantile:
    def test_p50_in_dense_bucket(self, mod):
        payload = _make_payload(
            buckets=[("0.1", 0), ("0.5", 0), ("1.0", 100), ("5.0", 0), ("+Inf", 0)],
            total_sum=70.0,
        )
        series, _ = mod._parse_metrics(payload)
        p50 = mod._quantile_from_histogram(series[0], 0.5)
        # 50% 落点应在 (0.5, 1.0] 之间
        assert 0.5 < p50 <= 1.0

    def test_p95_pushes_to_top_bucket(self, mod):
        payload = _make_payload(
            buckets=[("0.5", 80), ("1.0", 10), ("5.0", 10), ("+Inf", 0)],
            total_sum=120.0,
        )
        series, _ = mod._parse_metrics(payload)
        p95 = mod._quantile_from_histogram(series[0], 0.95)
        # P95 应进入 (1.0, 5.0] 桶
        assert 1.0 < p95 <= 5.0

    def test_returns_none_when_no_samples(self, mod):
        empty = mod.HistogramSeries(labels=(), buckets=[(1.0, 0)], sum_value=0.0, count_value=0)
        assert mod._quantile_from_histogram(empty, 0.5) is None


class TestAggregate:
    def test_single_provider_returns_as_is(self, mod):
        s = mod.HistogramSeries(
            labels=(("provider", "a"),), buckets=[(1.0, 5), (math.inf, 5)],
            sum_value=2.5, count_value=5,
        )
        merged = mod._aggregate([s])
        assert merged is s

    def test_multi_provider_buckets_summed(self, mod):
        a = mod.HistogramSeries(
            labels=(("provider", "a"),), buckets=[(1.0, 3), (math.inf, 3)],
            sum_value=1.5, count_value=3,
        )
        b = mod.HistogramSeries(
            labels=(("provider", "b"),), buckets=[(1.0, 7), (math.inf, 7)],
            sum_value=4.0, count_value=7,
        )
        merged = mod._aggregate([a, b])
        assert merged is not None
        assert merged.count_value == 10
        assert merged.sum_value == pytest.approx(5.5)
        bucket_dict = dict(merged.buckets)
        assert bucket_dict[1.0] == 10  # 3+7

    def test_empty_returns_none(self, mod):
        assert mod._aggregate([]) is None


class TestRounding:
    @pytest.mark.parametrize(
        ("v", "expected"),
        [(0.1, 1), (0.99, 1), (1.0, 1), (1.01, 2), (5.5, 6), (10.0, 10)],
    )
    def test_round_up_seconds(self, mod, v, expected):
        assert mod._round_up_seconds(v) == expected


class TestEntryPoint:
    def test_main_returns_3_on_unreachable_url(self, mod, capsys):
        rc = mod.main(["--url", "http://127.0.0.1:1/metrics", "--timeout", "0.2"])
        assert rc == 3

    def test_main_returns_2_when_no_samples(self, mod, monkeypatch, capsys):
        """模拟 metrics 端点返回完全空的 payload."""
        empty_payload = (
            "# HELP empty\n"
            'evopaw_asr_latency_seconds_count{provider="x"} 0\n'
        )

        class _Resp:
            def __init__(self, data: bytes):
                self._data = data

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return self._data

        monkeypatch.setattr(
            mod.urllib.request,
            "urlopen",
            lambda *a, **kw: _Resp(empty_payload.encode()),
        )
        rc = mod.main(["--url", "http://example/metrics"])
        assert rc == 2
