#!/usr/bin/env python3
"""ASR 回执阈值校准工具。

从 EvoPaw 自身暴露的 Prometheus endpoint 拉取 ``evopaw_asr_latency_seconds``
直方图与 ``evopaw_asr_requests_total`` 计数，估算 P50 / P80 / P95，给出
``short_wait_s`` 与 ``long_audio_threshold_ms`` 的推荐取值。

为什么要分位数:
- ``short_wait_s``：让 ≥ 80% 的请求"在不发回执的情况下"能完成 → 取 P80(latency) 向上取整。
- ``long_audio_threshold_ms``：用 P75(audio duration) 作阈值 —— 长于这个时长的语音
  多数情况下转写时间也会超过 short_wait_s，提前发回执体验更好。**当前 metrics 没有
  audio duration 直方图**，所以本脚本只给 short_wait_s 的推荐，long_audio_threshold_ms
  仍需用日志或人工 grep 历史 ``duration_ms``，详见 runbook 步骤 B。

依赖:
- 标准库 ``urllib`` 拉 ``/metrics``，**不引入 prometheus_client 的 parser** 以保持依赖最小。
  自己写一个轻量解析器只够用 Counter / Histogram 这两类。

用法:
    # 默认连本地 EvoPaw 的 metrics 端点
    python3 scripts/calibrate_thresholds.py

    # 远端
    python3 scripts/calibrate_thresholds.py --url http://10.0.0.1:9100/metrics

退出码:
- 0：成功输出推荐
- 2：metrics 拉到了但 ASR 直方图 sample_count == 0（还没有任何 ASR 请求）
- 3：网络错误或 metrics 解析失败
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


_DEFAULT_URL = "http://127.0.0.1:9100/metrics"
_LATENCY_METRIC = "evopaw_asr_latency_seconds"
_REQUESTS_METRIC = "evopaw_asr_requests_total"


# ── 轻量 metrics 解析 ──────────────────────────────────────────


@dataclass
class HistogramSeries:
    """单一 label set 下的 histogram 视图."""

    labels: tuple[tuple[str, str], ...]
    buckets: list[tuple[float, float]]  # (le, cumulative_count)
    sum_value: float = 0.0
    count_value: float = 0.0


_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')
_SAMPLE_RE = re.compile(
    r"^(?P<name>\w+)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[^\s]+)\s*$"
)


def _parse_labels(text: str | None) -> tuple[tuple[str, str], ...]:
    if not text:
        return ()
    return tuple(sorted((m.group(1), m.group(2)) for m in _LABEL_RE.finditer(text)))


def _parse_metrics(payload: str) -> tuple[
    list[HistogramSeries], dict[tuple[tuple[str, str], ...], float]
]:
    """解析 Prometheus exposition：返回 (histograms_for_latency, requests_counters).

    requests_counters: provider/status label 组合 → counter 当前值
    """
    histograms: dict[tuple[tuple[str, str], ...], HistogramSeries] = {}
    requests_counters: dict[tuple[tuple[str, str], ...], float] = {}

    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        labels = _parse_labels(m.group("labels"))

        if name == _REQUESTS_METRIC:
            requests_counters[labels] = value
            continue

        if name == f"{_LATENCY_METRIC}_bucket":
            le_value: float | None = None
            keyed_labels: list[tuple[str, str]] = []
            for k, v in labels:
                if k == "le":
                    le_value = math.inf if v in {"+Inf", "Inf"} else float(v)
                else:
                    keyed_labels.append((k, v))
            if le_value is None:
                continue
            key = tuple(sorted(keyed_labels))
            hs = histograms.setdefault(key, HistogramSeries(labels=key, buckets=[]))
            hs.buckets.append((le_value, value))
        elif name == f"{_LATENCY_METRIC}_sum":
            key = labels
            hs = histograms.setdefault(key, HistogramSeries(labels=key, buckets=[]))
            hs.sum_value = value
        elif name == f"{_LATENCY_METRIC}_count":
            key = labels
            hs = histograms.setdefault(key, HistogramSeries(labels=key, buckets=[]))
            hs.count_value = value

    return list(histograms.values()), requests_counters


def _quantile_from_histogram(h: HistogramSeries, q: float) -> float | None:
    """对单条 histogram 估算 q 分位数（线性插值，与 Prometheus histogram_quantile 行为对齐）."""
    if h.count_value <= 0 or not h.buckets:
        return None
    buckets = sorted(h.buckets, key=lambda b: b[0])
    target = q * h.count_value
    prev_le = 0.0
    prev_count = 0.0
    for le, cum in buckets:
        if cum >= target:
            if le == math.inf:
                # 最后一桶是 +Inf，无法插值，返回上界（前一个有限桶上限）
                return prev_le if prev_le > 0 else h.sum_value / max(h.count_value, 1.0)
            if cum == prev_count:
                return le
            ratio = (target - prev_count) / (cum - prev_count)
            return prev_le + (le - prev_le) * ratio
        prev_le = le
        prev_count = cum
    return prev_le


# ── 推荐计算 ──────────────────────────────────────────────────


def _aggregate(latency_series: Iterable[HistogramSeries]) -> HistogramSeries | None:
    """跨 provider 把 buckets 合并（如果只有一个 provider 就直接返回它）."""
    series = list(latency_series)
    if not series:
        return None
    if len(series) == 1:
        return series[0]
    bucket_sums: dict[float, float] = defaultdict(float)
    for s in series:
        for le, cum in s.buckets:
            bucket_sums[le] += cum
    merged = HistogramSeries(
        labels=(("aggregated", "true"),),
        buckets=sorted(bucket_sums.items()),
        sum_value=sum(s.sum_value for s in series),
        count_value=sum(s.count_value for s in series),
    )
    return merged


def _round_up_seconds(value: float) -> int:
    """取整为整秒，至少 1 秒."""
    return max(1, int(math.ceil(value)))


def _format_status_breakdown(
    requests: dict[tuple[tuple[str, str], ...], float],
) -> str:
    if not requests:
        return "（无 ASR 请求 metric 数据）"
    rows: list[tuple[str, float]] = []
    for labels, count in requests.items():
        d = dict(labels)
        rows.append((f"{d.get('provider', '?')} / {d.get('status', '?')}", count))
    rows.sort(key=lambda x: -x[1])
    return "\n".join(f"  {label:40s}  {count:.0f}" for label, count in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="根据 Prometheus ASR 指标校准回执阈值",
    )
    parser.add_argument(
        "--url",
        default=_DEFAULT_URL,
        help=f"EvoPaw Prometheus endpoint，默认 {_DEFAULT_URL}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="HTTP 超时（秒）",
    )
    args = parser.parse_args(argv)

    try:
        with urllib.request.urlopen(args.url, timeout=args.timeout) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"❌ 拉取 metrics 失败：{exc}", file=sys.stderr)
        return 3

    try:
        latency_series, requests_counters = _parse_metrics(payload)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 解析 metrics 失败：{exc}", file=sys.stderr)
        return 3

    aggregated = _aggregate(latency_series)
    if aggregated is None or aggregated.count_value <= 0:
        print(
            "⚠ 当前 evopaw_asr_latency_seconds 无样本。",
            "请先让 EvoPaw 跑至少几十条真实语音再回来。",
            file=sys.stderr,
        )
        return 2

    p50 = _quantile_from_histogram(aggregated, 0.50)
    p80 = _quantile_from_histogram(aggregated, 0.80)
    p95 = _quantile_from_histogram(aggregated, 0.95)
    p99 = _quantile_from_histogram(aggregated, 0.99)
    avg = aggregated.sum_value / max(aggregated.count_value, 1.0)

    print(f"已收集 {int(aggregated.count_value)} 个 ASR 转写样本")
    print(f"延迟分布（秒）：avg={avg:.2f}  P50={p50:.2f}  P80={p80:.2f}  "
          f"P95={p95:.2f}  P99={p99:.2f}")
    print()
    print("ASR 请求按 provider/status 分布：")
    print(_format_status_breakdown(requests_counters))
    print()

    short_wait_s_rec = _round_up_seconds(p80)
    print("──────── 推荐取值 ────────")
    print(
        f"  short_wait_s          建议 ~{short_wait_s_rec}（取 P80={p80:.2f}s 向上取整；"
        "确保 ≥80% 的请求在不发回执的情况下完成）"
    )
    print(
        "  max_wait_s            当前默认 120；如果 P95 ≪ 120s，可降为 max(60, ⌈P99×2⌉) "
        f"≈ {max(60, _round_up_seconds(p99 * 2))} 以更快释放 worker"
    )
    print(
        "  long_audio_threshold_ms  本脚本无音频时长 metric 数据。"
        "请按 runbook 步骤 B 用日志取 audio duration 的 P75 后再写。"
    )
    print()
    print(
        "上述推荐写入 config.yaml 后重启 EvoPaw 即可生效。"
        "建议跑两周后再回来跑一次本脚本观察新分布。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
